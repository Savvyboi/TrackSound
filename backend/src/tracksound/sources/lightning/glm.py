"""GOES Geostationary Lightning Mapper (GLM) flashes from NOAA's public AWS buckets.

GLM is an optical lightning imager on the GOES-R satellites. It sees total lightning
(in-cloud and cloud-to-ground) over the Americas and nearby oceans, with ~2 ms timing and
a location error of about 4 km (8 km pixels; positions are flash *centroids* at cloud top).

Data: Level-2 "LCFA" NetCDF4 files, one per 20 s, at
``https://<bucket>.s3.amazonaws.com/GLM-L2-LCFA/YYYY/DDD/HH/OR_GLM-L2-LCFA_Gxx_sYYYYDDDHHMMSSs_e..._c....nc``
(no account needed). NetCDF4 is HDF5, read here with h5py; CF packing attributes
(``scale_factor``, ``add_offset``, ``_FillValue``, ``_Unsigned``) are applied by hand.

Time encoding (checked against real files):

- Current files: ``flash_time_offset_of_first_event`` is int16 with ``_Unsigned = true``,
  scale 0.00038148 s, offset -5 s, units ``seconds since <file start>``.
- 2018-era files: int16 *without* ``_Unsigned``, scale 2, units
  ``milliseconds since <file start>``. Some also hold junk flashes timed tens of seconds
  outside the file; flashes outside the file's own time span are dropped.
- ``product_time`` is seconds since 2000-01-01 12:00:00 UTC without leap seconds
  (``unix = product_time + 946728000``); it is only a fallback (it can be -999).

Satellites and archive coverage on AWS (listed November 2026):

==========  =========  ===========================================
satellite   slot       GLM files on AWS
==========  =========  ===========================================
GOES-16     East       2018-02-13 .. 2025-04-07 (operational East to 2025-04-07)
GOES-17     West       2018-08-22 (sparse; regular from 2019-02-07) .. 2023-01-10
GOES-18     West       2022-09-19 .. (operational West from 2023-01-04)
GOES-19     East       2025-01-15 .. (operational East from 2025-04-07)
==========  =========  ===========================================
"""
from __future__ import annotations

import calendar
import datetime as dt
import math
import re
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from .catalogue import assign_ids, make_strike
from .geo import in_bbox, normalize_lon

ProgressFn = Callable[[float, str | None], None]

PREFIX = "GLM-L2-LCFA"
J2000_UNIX = 946728000.0                 # 2000-01-01 12:00:00 UTC
SIGMA_GOOD_M = 4000.0                    # GLM location error (GOES-19 provisional review: ~3.7 km)
SIGMA_DEGRADED_M = 6000.0                # flashes with a degraded quality flag
FILE_SPAN_PAD_S = 10.0

BUCKETS = {"G16": "noaa-goes16", "G17": "noaa-goes17", "G18": "noaa-goes18", "G19": "noaa-goes19"}
SLOT = {"G16": "east", "G19": "east", "G17": "west", "G18": "west"}
SUBPOINT_LON = {"east": -75.2, "west": -137.2}
SLOT_NAMES = {"east": "GOES-East", "west": "GOES-West"}
COVER_DLON = 64.0                        # GLM field of view: about +-64 deg of longitude
COVER_LAT = 57.0                         # and +-57 deg of latitude around the sub-satellite point


def _utc(y, m, d, hh=0, mm=0) -> float:
    return float(calendar.timegm((y, m, d, hh, mm, 0)))


# (first, last) UTC with GLM data on AWS; None = still running
ARCHIVE = {"G16": (_utc(2018, 2, 13), _utc(2025, 4, 7, 21)), "G17": (_utc(2018, 8, 22), _utc(2023, 1, 10, 19)),
           "G18": (_utc(2022, 9, 19), None), "G19": (_utc(2025, 1, 15), None)}
# operational handovers: before -> after
HANDOVER = {"east": ("G16", "G19", _utc(2025, 4, 7, 15)), "west": ("G17", "G18", _utc(2023, 1, 4, 18))}


@dataclass(frozen=True)
class GlmFile:
    sat: str
    key: str
    size: int
    t_start: float
    t_end: float

    @property
    def bucket(self) -> str:
        return BUCKETS[self.sat]

    @property
    def url(self) -> str:
        return f"https://{self.bucket}.s3.amazonaws.com/{self.key}"


# ---- satellite choice --------------------------------------------------------------------------

def covers(slot: str, lat: float, lon: float) -> bool:
    dlon = abs(float(normalize_lon(lon - SUBPOINT_LON[slot])))
    return dlon <= COVER_DLON and abs(lat) <= COVER_LAT


def has_archive(sat: str, t: float) -> bool:
    a, b = ARCHIVE[sat]
    return t >= a - 86400 and (b is None or t <= b + 86400)


def satellites_for(bbox: dict, t0: float, t1: float, satellite: str = "auto") -> list[str]:
    """Satellites to try, in order of preference.

    ``auto``: the slot (East/West) whose sub-satellite point is nearer the region, then the
    operational satellite of that slot for the time, then the slot's other satellite, then
    the other slot if it still sees the region. ``east``/``west`` pick a slot; ``G16`` ..
    ``G19`` force a satellite.
    """
    s = (satellite or "auto").strip().upper().replace("GOES-", "G").replace("GOES", "G")
    if s in BUCKETS:
        return [s]
    lat_c = 0.5 * (bbox["lat_min"] + bbox["lat_max"])
    lon_c = float(normalize_lon(0.5 * (bbox["lon_min"] + bbox["lon_max"])))
    if s in ("EAST", "WEST"):
        slots = [s.lower()]
    elif s == "AUTO":
        mid = 0.5 * (SUBPOINT_LON["east"] + SUBPOINT_LON["west"])
        first = "east" if lon_c > mid else "west"
        slots = [first] + [o for o in ("east", "west") if o != first]
    else:
        raise ValueError(f"unknown satellite {satellite!r} (use auto, east, west, G16, G17, G18 or G19)")
    slots = [sl for sl in slots if covers(sl, lat_c, lon_c)]
    if not slots:
        raise ValueError("GLM only sees the Americas and nearby oceans (about 57 S-57 N, 160 E-10 W); "
                         "the region is outside its view")
    t_mid = 0.5 * (t0 + t1)
    out: list[str] = []
    for sl in slots:
        before, after, t_h = HANDOVER[sl]
        for sat in ((before, after) if t_mid < t_h else (after, before)):
            if has_archive(sat, t_mid) and sat not in out:
                out.append(sat)
    if not out:
        first_day = min(ARCHIVE[s][0] for s in BUCKETS)
        raise ValueError(f"no GLM archive for {_fmt(t_mid)} (the AWS archive starts {_fmt(first_day)[:10]})")
    return out


def _fmt(t: float) -> str:
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


# ---- listing ---------------------------------------------------------------------------------

_KEY_RE = re.compile(r"_s(\d{4})(\d{3})(\d{2})(\d{2})(\d{2})(\d)_e(\d{4})(\d{3})(\d{2})(\d{2})(\d{2})(\d)_")


def parse_key_times(key: str) -> tuple[float, float] | None:
    """(start, end) unix s from a GLM file name."""
    m = _KEY_RE.search(key)
    if not m:
        return None
    g = [int(x) for x in m.groups()]

    def t(y, doy, hh, mm, ss, tenth):
        return _utc(y, 1, 1) + (doy - 1) * 86400 + hh * 3600 + mm * 60 + ss + tenth / 10.0
    return t(*g[:6]), t(*g[6:])


def hour_prefix(t: float) -> str:
    d = dt.datetime.fromtimestamp(t, dt.timezone.utc)
    return f"{PREFIX}/{d.year:04d}/{d.timetuple().tm_yday:03d}/{d.hour:02d}/"


def parse_listing(xml_text: str) -> tuple[list[tuple[str, int]], str | None]:
    """(key, size) entries and the continuation token of one ListObjectsV2 page."""
    root = ET.fromstring(xml_text)
    ns = ""
    if root.tag.startswith("{"):
        ns = root.tag.split("}")[0] + "}"
    out = []
    for c in root.iter(f"{ns}Contents"):
        k = c.find(f"{ns}Key")
        s = c.find(f"{ns}Size")
        if k is not None and k.text:
            out.append((k.text, int(s.text) if s is not None and s.text else 0))
    tok = root.find(f"{ns}NextContinuationToken")
    truncated = root.find(f"{ns}IsTruncated")
    more = truncated is not None and (truncated.text or "").strip().lower() == "true"
    return out, (tok.text if (tok is not None and more) else None)


class _Http:
    """One requests.Session per thread (Session is not thread-safe)."""

    def __init__(self):
        self._local = threading.local()

    def get(self, url: str, **kw):
        import requests
        s = getattr(self._local, "s", None)
        if s is None:
            s = self._local.s = requests.Session()
            s.headers["User-Agent"] = "TrackSound (lightning source; https://github.com/)"
        last = None
        timeout = kw.pop("timeout", 60)
        for attempt in range(4):
            try:
                r = s.get(url, timeout=timeout, **kw)
                if r.status_code in (500, 502, 503, 504):
                    raise IOError(f"HTTP {r.status_code} from {url}")
                return r
            except (IOError, OSError) as e:          # requests exceptions derive from IOError
                last = e
                time.sleep(0.5 * 2 ** attempt)
        raise IOError(f"could not reach {url}: {last}")


def list_hour(http: _Http, sat: str, hour_start: float, cache_dir: Path | None = None) -> list[GlmFile]:
    """GLM files of one satellite for one UTC hour (cached once the hour is long past)."""
    prefix = hour_prefix(hour_start)
    cache = cache_dir / BUCKETS[sat] / "listing" / (prefix.replace("/", "_").strip("_") + ".json") if cache_dir else None
    if cache is not None and cache.exists():
        import json
        try:
            return [GlmFile(sat, k, int(s), *parse_key_times(k)) for k, s in json.loads(cache.read_text())
                    if parse_key_times(k)]
        except (OSError, ValueError):
            pass
    entries: list[tuple[str, int]] = []
    token = None
    while True:
        params = {"list-type": "2", "prefix": prefix}
        if token:
            params["continuation-token"] = token
        r = http.get(f"https://{BUCKETS[sat]}.s3.amazonaws.com/", params=params, timeout=30)
        if r.status_code != 200:
            raise IOError(f"listing {BUCKETS[sat]}/{prefix} failed: HTTP {r.status_code}")
        page, token = parse_listing(r.text)
        entries += page
        if not token:
            break
    if cache is not None and hour_start + 3 * 3600 < time.time():
        import json
        cache.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_suffix(".tmp")
        tmp.write_text(json.dumps(entries))
        tmp.replace(cache)
    return [GlmFile(sat, k, s, *tt) for k, s in entries if (tt := parse_key_times(k)) and k.endswith(".nc")]


# ---- reading ---------------------------------------------------------------------------------

def _attr(a, name):
    v = a.get(name)
    if v is None:
        return None
    if isinstance(v, bytes):
        return v.decode(errors="replace")
    if isinstance(v, np.ndarray):
        if v.dtype.kind in ("S", "O", "U"):
            x = v.ravel()[0]
            return x.decode(errors="replace") if isinstance(x, bytes) else str(x)
        return v.ravel()[0] if v.size else None
    if isinstance(v, np.bytes_):
        return bytes(v).decode(errors="replace")
    return v


def decode_var(ds) -> np.ndarray:
    """A NetCDF variable as float64 with CF packing applied (NaN where _FillValue)."""
    raw = np.asarray(ds[()])
    a = ds.attrs
    fill = _attr(a, "_FillValue")
    mask = (raw == np.asarray(fill).astype(raw.dtype)) if fill is not None and raw.size else np.zeros(raw.shape, bool)
    unsigned = str(_attr(a, "_Unsigned") or "").strip().lower() == "true"
    if unsigned and raw.dtype.kind == "i":
        val = (raw.astype(np.int64) & ((1 << (8 * raw.dtype.itemsize)) - 1)).astype(np.float64)
    else:
        val = raw.astype(np.float64)
    scale = _attr(a, "scale_factor")
    offset = _attr(a, "add_offset")
    if scale is not None:
        val = val * float(scale)
    if offset is not None:
        val = val + float(offset)
    if mask.any():
        val = np.where(mask, np.nan, val)
    return val


_UNITS_RE = re.compile(r"^\s*(seconds?|milliseconds?|microseconds?|s|ms)\s+since\s+"
                       r"(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}(?:\.\d*)?))?)?", re.I)


def parse_time_units(units: str) -> tuple[float, float] | None:
    """(seconds per unit, epoch unix s) from 'seconds since 2025-07-19 18:00:00.000'."""
    m = _UNITS_RE.match(units or "")
    if not m:
        return None
    unit = m.group(1).lower()
    factor = 1e-3 if unit.startswith("milli") or unit == "ms" else 1e-6 if unit.startswith("micro") else 1.0
    y, mo, d = int(m.group(2)), int(m.group(3)), int(m.group(4))
    hh, mm = int(m.group(5) or 0), int(m.group(6) or 0)
    ss = float(m.group(7) or 0.0)
    return factor, _utc(y, mo, d, hh, mm) + ss


def read_flashes(path: str | Path, key_times: tuple[float, float] | None = None) -> dict[str, np.ndarray]:
    """Flashes of one GLM L2 file: arrays t_utc, lat, lon, energy_j, quality, area_m2."""
    import h5py
    empty = {k: np.zeros(0) for k in ("t_utc", "lat", "lon", "energy_j", "quality", "area_m2")}
    with h5py.File(path, "r") as f:
        if "flash_lat" not in f or f["flash_lat"].shape[0] == 0:
            return empty
        ds_t = f["flash_time_offset_of_first_event"]
        units = parse_time_units(str(_attr(ds_t.attrs, "units") or ""))
        off = decode_var(ds_t)
        if units is not None:
            factor, epoch = units
            t = epoch + off * factor
        else:  # fall back to product_time (seconds since J2000, no leap seconds)
            pt = float(decode_var(f["product_time"]).ravel()[0]) if "product_time" in f else math.nan
            if not math.isfinite(pt) or pt < 0:
                if key_times is None:
                    return empty
                pt = key_times[0] - J2000_UNIX
            t = J2000_UNIX + pt + off
        lat = decode_var(f["flash_lat"])
        lon = decode_var(f["flash_lon"])
        n = len(lat)
        energy = decode_var(f["flash_energy"]) if "flash_energy" in f else np.full(n, np.nan)
        qual = decode_var(f["flash_quality_flag"]) if "flash_quality_flag" in f else np.zeros(n)
        area = decode_var(f["flash_area"]) if "flash_area" in f else np.full(n, np.nan)
        if "flash_area" in f and str(_attr(f["flash_area"].attrs, "units") or "").strip() == "km2":
            area = area * 1e6
    ok = np.isfinite(t) & np.isfinite(lat) & np.isfinite(lon)
    if key_times is not None:
        ok &= (t >= key_times[0] - FILE_SPAN_PAD_S) & (t <= key_times[1] + FILE_SPAN_PAD_S)
    return {"t_utc": t[ok], "lat": lat[ok], "lon": normalize_lon(lon[ok]), "energy_j": energy[ok],
            "quality": qual[ok], "area_m2": area[ok]}


# ---- fetch -----------------------------------------------------------------------------------

def _download(http: _Http, f: GlmFile, cache_dir: Path) -> Path:
    dest = cache_dir / f.bucket / f.key
    if dest.exists() and (f.size <= 0 or dest.stat().st_size == f.size):
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + f".part{threading.get_ident()}")
    r = http.get(f.url, timeout=120)
    if r.status_code != 200:
        raise IOError(f"download failed ({r.status_code}): {f.url}")
    data = r.content
    if f.size > 0 and len(data) != f.size:
        raise IOError(f"incomplete download of {f.url} ({len(data)} of {f.size} bytes)")
    tmp.write_bytes(data)
    tmp.replace(dest)
    return dest


def list_window(http: _Http, sats: list[str], t0: float, t1: float, cache_dir: Path | None,
                progress: ProgressFn | None = None) -> tuple[list[GlmFile], list[float]]:
    """Files overlapping [t0, t1], hour by hour from the first satellite that has data.
    Returns (files, start times of hours with no data from any candidate)."""
    h0 = math.floor((t0 - 30.0) / 3600.0) * 3600.0
    hours = list(np.arange(h0, t1, 3600.0))
    files: list[GlmFile] = []
    missing: list[float] = []
    for n, h in enumerate(hours):
        if progress:
            progress(n / max(1, len(hours)), f"Listing GLM files ({hour_prefix(h)[len(PREFIX) + 1:-1]})")
        for sat in sats:
            entries = list_hour(http, sat, float(h), cache_dir)
            if entries:
                files += [e for e in entries if e.t_end >= t0 - 1.0 and e.t_start <= t1 + 1.0]
                break
        else:
            missing.append(float(h))
    files.sort(key=lambda f: (f.t_start, f.sat))
    return files, missing


def fetch_flashes(cache_dir: Path, bbox: dict, t0_utc: float, t1_utc: float, satellite: str = "auto",
                  progress: ProgressFn | None = None, workers: int = 8, http: _Http | None = None) -> tuple[list[dict], dict]:
    """GLM flashes inside ``bbox`` between t0 and t1 as Strike dicts, plus fetch info.

    Downloads go to ``cache_dir/<bucket>/<key>`` and are reused. ``progress`` may raise
    (job cancelled); pending downloads are then abandoned.
    """
    if t1_utc <= t0_utc:
        raise ValueError("t1_utc must be after t0_utc")
    http = http or _Http()
    sats = satellites_for(bbox, t0_utc, t1_utc, satellite)
    report = progress or (lambda f, m=None: None)
    files, missing = list_window(http, sats, t0_utc, t1_utc, cache_dir, lambda f, m: report(0.05 * f, m))
    if not files:
        names = ", ".join(f"GOES-{s[1:]}" for s in sats)
        raise ValueError(f"no GLM files found for {_fmt(t0_utc)} to {_fmt(t1_utc)} ({names}); "
                         "the satellites may not have been operating then")
    strikes: list[dict] = []
    n_done = 0
    pool = ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="glm")
    try:
        futures = {pool.submit(_get_and_read, http, f, cache_dir, bbox, t0_utc, t1_utc): f for f in files}
        for fut in as_completed(futures):
            strikes += fut.result()
            n_done += 1
            if n_done % 5 == 0 or n_done == len(files):
                report(0.05 + 0.93 * n_done / len(files),
                       f"Downloading GLM files {n_done}/{len(files)} ({len(strikes)} flashes in the area)")
    except BaseException:
        pool.shutdown(wait=False, cancel_futures=True)
        raise
    pool.shutdown(wait=True)
    strikes = _dedupe(strikes)
    used = sorted({f.sat for f in files})
    info = {"satellites": used, "n_files": len(files), "missing_hours": missing,
            "slot": SLOT_NAMES[SLOT[used[0]]] if used else None}
    return assign_ids(strikes), info


def _get_and_read(http: _Http, f: GlmFile, cache_dir: Path, bbox: dict, t0: float, t1: float) -> list[dict]:
    path = _download(http, f, cache_dir)
    try:
        fl = read_flashes(path, (f.t_start, f.t_end))
    except OSError:
        path.unlink(missing_ok=True)       # a corrupt cached file: fetch it again next time
        fl = read_flashes(_download(http, f, cache_dir), (f.t_start, f.t_end))
    keep = in_bbox(fl["lat"], fl["lon"], bbox) & (fl["t_utc"] >= t0) & (fl["t_utc"] <= t1)
    out = []
    for i in np.flatnonzero(keep):
        q = fl["quality"][i]
        sigma = SIGMA_GOOD_M if (not np.isfinite(q) or q == 0) else SIGMA_DEGRADED_M
        e = fl["energy_j"][i]
        out.append(make_strike(fl["t_utc"][i], fl["lat"][i], fl["lon"][i], sigma,
                               float(e) if np.isfinite(e) else None, "flash", f"glm-{f.sat}"))
    return out


def _dedupe(strikes: list[dict]) -> list[dict]:
    seen = set()
    out = []
    for s in strikes:
        k = (round(s["t_utc"], 3), round(s["lat"], 3), round(s["lon"], 3))
        if k not in seen:
            seen.add(k)
            out.append(s)
    return out
