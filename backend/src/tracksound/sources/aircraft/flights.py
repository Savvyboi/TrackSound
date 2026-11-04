"""Historical ADS-B flights for a case: download (adsb.lol day archives), cache, summaries, GeoJSON.

dopplertrack streams a whole UTC day (about 4 GB, split into ``.tar.aa``, ``.tar.ab``, ...) and
keeps the traces touching a bounding box. Here that runs as a job with progress and
cancellation, and the result is cached under ``<data>/cache/flights/<date>/`` so a later case
for the same day and an overlapping region reuses it.

ADS-B history © adsb.lol contributors, Open Database License (ODbL 1.0).
"""
from __future__ import annotations

import hashlib
import io
import json
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Callable

import numpy as np
import requests
from dopplertrack.adsb import AircraftTypes, Trace, load_traces, save_traces
from dopplertrack.fetch import _ChainedHTTP, iter_tar_traces

ATTRIBUTION = "ADS-B history: adsb.lol contributors (ODbL 1.0)"
API = "https://api.github.com/repos/adsblol/globe_history_{year}/releases/tags/{tag}"
DOWNLOAD = "https://github.com/adsblol/globe_history_{year}/releases/download/{tag}/{name}"
PODS = ("planes-readsb-prod-0", "planes-readsb-staging-0")


# ---- locating the archive ----------------------------------------------------------------

def _tag(date: str, pod: str) -> str:
    y, m, d = date.split("-")
    return f"v{y}.{m}.{d}-{pod}"


def _assets_from_api(date: str, pod: str, timeout: float = 30.0) -> list[tuple[str, int]]:
    import os
    y = date[:4]
    headers = {"Accept": "application/vnd.github+json"}
    if os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    r = requests.get(API.format(year=y, tag=_tag(date, pod)), timeout=timeout, headers=headers)
    if r.status_code == 404:
        return []
    r.raise_for_status()
    assets = [a for a in r.json().get("assets", []) if ".tar" in a["name"]]
    assets.sort(key=lambda a: a["name"])
    return [(a["browser_download_url"], int(a.get("size") or 0)) for a in assets]


def _assets_by_probing(date: str, pod: str, timeout: float = 30.0) -> list[tuple[str, int]]:
    """The GitHub API is rate limited (60 requests/hour without a token) and blocked on some
    networks; release downloads are not. Probe the split names .tar.aa, .tar.ab, ... directly."""
    y, tag = date[:4], _tag(date, pod)
    out = []
    names = [f"{tag}.tar.a{c}" for c in "abcdefghijklmnopqrstuvwxyz"]
    for name in names:
        url = DOWNLOAD.format(year=y, tag=tag, name=name)
        r = requests.head(url, allow_redirects=True, timeout=timeout)
        if r.status_code == 404:
            break
        r.raise_for_status()
        out.append((url, int(r.headers.get("Content-Length") or 0)))
    if not out:
        url = DOWNLOAD.format(year=y, tag=tag, name=f"{tag}.tar")
        r = requests.head(url, allow_redirects=True, timeout=timeout)
        if r.ok:
            out.append((url, int(r.headers.get("Content-Length") or 0)))
    return out


def archive_assets(date: str) -> tuple[str, list[tuple[str, int]]]:
    """(pod, [(url, size_bytes)]) of the archive for one UTC day. Prefers prod, then staging."""
    errors = []
    for pod in PODS:
        for finder in (_assets_from_api, _assets_by_probing):
            try:
                assets = finder(date, pod)
            except requests.RequestException as e:
                errors.append(f"{finder.__name__}({pod}): {e}")
                continue
            if assets:
                return pod, assets
    raise ValueError(f"no adsb.lol archive found for {date}" + (f" ({'; '.join(errors[:2])})" if errors else "")
                     + ". Archives exist from 2023 onwards and appear about a day after the date.")


# ---- the cache ---------------------------------------------------------------------------

def _contains(outer: dict, inner: dict) -> bool:
    return (outer["lat_min"] <= inner["lat_min"] and outer["lat_max"] >= inner["lat_max"]
            and outer["lon_min"] <= inner["lon_min"] and outer["lon_max"] >= inner["lon_max"])


def _key(date: str, bbox: dict, keep_jets: bool) -> str:
    s = json.dumps([date, [round(bbox[k], 4) for k in ("lat_min", "lat_max", "lon_min", "lon_max")], keep_jets])
    return hashlib.sha1(s.encode()).hexdigest()[:12]


def find_cached(flights_dir: Path, date: str, bbox: dict, keep_jets: bool) -> dict | None:
    """A cached flight set for this day whose box contains ``bbox``."""
    d = flights_dir / date
    if not d.exists():
        return None
    best = None
    for meta_path in d.glob("*.json"):
        try:
            meta = json.loads(meta_path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if meta.get("keep_jets") != keep_jets or not (d / meta["file"]).exists():
            continue
        if _contains(meta["bbox"], bbox):
            area = (meta["bbox"]["lat_max"] - meta["bbox"]["lat_min"]) * (meta["bbox"]["lon_max"] - meta["bbox"]["lon_min"])
            if best is None or area < best[0]:
                best = (area, meta)
    return best[1] if best else None


def save_cached(flights_dir: Path, date: str, bbox: dict, keep_jets: bool, traces: list[Trace], extra: dict) -> dict:
    d = flights_dir / date
    d.mkdir(parents=True, exist_ok=True)
    key = _key(date, bbox, keep_jets)
    save_traces(traces, d / f"{key}.npz")
    meta = {"date": date, "bbox": bbox, "keep_jets": keep_jets, "file": f"{key}.npz", "n_traces": len(traces),
            "fetched_at": time.time(), "attribution": ATTRIBUTION, **extra}
    (d / f"{key}.json").write_text(json.dumps(meta, indent=1))
    return meta


def cache_file(flights_dir: Path, meta: dict) -> Path:
    return flights_dir / meta["date"] / meta["file"]


def attach_traces(store, case_id: str, date: str, bbox: dict, traces: list[Trace], extra: dict | None = None) -> dict:
    """Store a flight set (e.g. synthetic or imported traces) and link it to a case, exactly as
    a fetch would."""
    meta = save_cached(store.settings.flights_dir, date, bbox, False, traces, extra or {})
    store.write_json(case_id, "aircraft/flights.json", {**meta, "linked_at": time.time()})
    return meta


def _parse_batch(args) -> list[Trace]:
    """Worker: parse a batch of raw trace files, keep those touching the box."""
    from dopplertrack.adsb import in_bbox, parse_trace_bytes
    items, box = args
    out = []
    for icao, raw in items:
        try:
            tr = parse_trace_bytes(raw, icao)
        except Exception:  # noqa: BLE001 - a malformed file is skipped, as dopplertrack does
            continue
        if tr is not None and in_bbox(tr, box):
            out.append(tr)
    return out


def iter_tar_traces_parallel(fileobj, box, progress=None, workers: int | None = None, batch_size: int = 400):
    """Like dopplertrack.fetch.iter_tar_traces, but the gunzip + JSON parsing (the slow part,
    about 8 MB/s on one core) runs in worker processes while this thread reads the tar."""
    import os
    import tarfile
    from collections import deque
    from concurrent.futures import ProcessPoolExecutor
    from dopplertrack.fetch import TRACE_RE
    workers = workers or max(1, (os.cpu_count() or 2) - 1)
    if workers == 1:
        yield from iter_tar_traces(fileobj, box, progress)
        return
    from ...procs import mp_context
    # forkserver (spawn on Windows), not fork: forking a multi-threaded server can deadlock a child
    ex = ProcessPoolExecutor(workers, mp_context=mp_context())
    pending: deque = deque()
    batch: list[tuple[str, bytes]] = []
    n_seen = 0
    try:
        with tarfile.open(fileobj=fileobj, mode="r|") as tf:
            for m in tf:
                if not m.isfile():
                    continue
                mm = TRACE_RE.search(m.name)
                if not mm:
                    continue
                batch.append((mm.group(1), tf.extractfile(m).read()))
                n_seen += 1
                if len(batch) >= batch_size:
                    pending.append(ex.submit(_parse_batch, (batch, box)))
                    batch = []
                    while len(pending) > 2 * workers:          # bound memory: wait for the oldest batch
                        yield from pending.popleft().result()
                if progress and n_seen % 2000 == 0:
                    progress(n_seen, getattr(fileobj, "total", 0))
        if batch:
            pending.append(ex.submit(_parse_batch, (batch, box)))
        while pending:
            yield from pending.popleft().result()
    finally:
        ex.shutdown(wait=False, cancel_futures=True)


def fetch_day(date: str, bbox: dict, keep_jets: bool = False,
              progress: Callable[[float, str], None] | None = None, workers: int | None = None) -> tuple[list[Trace], dict]:
    """Stream one UTC day of adsb.lol history and keep the traces touching bbox (props only
    unless keep_jets). ``progress(fraction, message)`` may raise to cancel."""
    pod, assets = archive_assets(date)
    total = sum(s for _, s in assets) or 1
    stream = _ChainedHTTP([u for u, _ in assets])
    t0 = time.time()

    def prog(n_seen, _):
        if progress:
            mb_s = stream.total / 1e6 / max(time.time() - t0, 1e-3)
            progress(min(stream.total / total, 0.999),
                     f"Scanned {n_seen:,} aircraft, {stream.total / 1e9:.2f} of {total / 1e9:.2f} GB ({mb_s:.0f} MB/s)")

    box = (bbox["lat_min"], bbox["lat_max"], bbox["lon_min"], bbox["lon_max"])
    traces = list(iter_tar_traces_parallel(io.BufferedReader(stream, 1 << 22), box, prog, workers))
    n_all = len(traces)
    if not keep_jets:
        types = AircraftTypes()
        traces = [t for t in traces if types.is_prop(t.typ)]
    return traces, {"pod": pod, "bytes": stream.total, "n_touching": n_all, "elapsed_s": time.time() - t0}


# ---- loading and presenting ----------------------------------------------------------------

class _TraceCache:
    def __init__(self, size: int = 3):
        self.size = size
        self._d: OrderedDict[str, tuple[float, list[Trace]]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, path: Path) -> list[Trace]:
        key, mtime = str(path), path.stat().st_mtime
        with self._lock:
            hit = self._d.get(key)
            if hit and hit[0] == mtime:
                self._d.move_to_end(key)
                return hit[1]
        traces = load_traces(path)
        with self._lock:
            self._d[key] = (mtime, traces)
            while len(self._d) > self.size:
                self._d.popitem(last=False)
        return traces


_TRACES = _TraceCache()
_TYPES: AircraftTypes | None = None


def load(path: Path) -> list[Trace]:
    return _TRACES.get(path)


def aircraft_types() -> AircraftTypes | None:
    global _TYPES
    if _TYPES is None:
        try:
            _TYPES = AircraftTypes()
        except Exception:  # noqa: BLE001 - offline: engine classes are a nicety
            return None
    return _TYPES


def summaries(traces: list[Trace]) -> list[dict]:
    types = aircraft_types()
    out = []
    for i, tr in enumerate(traces):
        row = types.types.get(tr.typ or "") if types else None
        out.append({"idx": i, "icao": tr.icao, "reg": tr.reg, "type": tr.typ, "desc": tr.desc,
                    "engine": row["EngineType"] if row else None,
                    "category": row["AircraftDescription"] if row else None,   # LandPlane, Helicopter, ...
                    "t_start_utc": float(tr.t[0]), "t_end_utc": float(tr.t[-1]), "n_points": int(len(tr.t))})
    return out


def _finite(x) -> float | None:
    x = float(x)
    return x if np.isfinite(x) else None


def geojson(traces: list[Trace], bbox: dict | None = None, t0: float | None = None, t1: float | None = None,
            step_s: float = 10.0, pad_m: float = 50_000.0) -> dict:
    """LineStrings per airborne stretch, thinned to about one vertex per step_s, clipped to
    bbox padded by pad_m (flights can cross a continent; the map only needs the region)."""
    from ...physics import pad_bbox
    box = pad_bbox(bbox, pad_m) if bbox else None
    types = aircraft_types()
    feats = []
    for i, tr in enumerate(traces):
        row = types.types.get(tr.typ or "") if types else None
        keep = np.isfinite(tr.lat) & np.isfinite(tr.lon)
        if box:
            keep &= (tr.lat >= box["lat_min"]) & (tr.lat <= box["lat_max"]) & (tr.lon >= box["lon_min"]) & (tr.lon <= box["lon_max"])
        if t0 is not None:
            keep &= tr.t >= t0
        if t1 is not None:
            keep &= tr.t <= t1
        idx = np.flatnonzero(keep)
        if len(idx) < 2:
            continue
        # split where points are missing (gaps > 90 s or a jump in index after clipping)
        brk = np.flatnonzero((np.diff(idx) > 1) | (np.diff(tr.t[idx]) > 90.0)) + 1
        for part in np.split(idx, brk):
            if len(part) < 2:
                continue
            sel = [part[0]]
            for j in part[1:]:
                if tr.t[j] - tr.t[sel[-1]] >= step_s:
                    sel.append(j)
            if sel[-1] != part[-1]:
                sel.append(part[-1])
            if len(sel) < 2:
                continue
            sel = np.asarray(sel)
            alt = np.where(np.isfinite(tr.alt[sel]), tr.alt[sel], 0.0)
            coords = np.round(np.stack([tr.lon[sel], tr.lat[sel], alt], -1), 5).tolist()
            feats.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": coords},
                          "properties": {"idx": i, "icao": tr.icao, "reg": tr.reg, "type": tr.typ, "desc": tr.desc,
                                         "category": row["AircraftDescription"] if row else None,
                                         "t_start_utc": float(tr.t[sel[0]]), "t_end_utc": float(tr.t[sel[-1]]),
                                         "times": np.round(tr.t[sel], 1).tolist()}})
    return {"type": "FeatureCollection", "features": feats}


def track(traces: list[Trace], idx: int) -> dict:
    if not 0 <= idx < len(traces):
        raise IndexError(idx)
    tr = traces[idx]
    return {"idx": idx, "icao": tr.icao, "reg": tr.reg, "type": tr.typ, "desc": tr.desc,
            "t": tr.t.tolist(), "lat": tr.lat.tolist(), "lon": tr.lon.tolist(),
            "alt_m": [_finite(a) for a in tr.alt]}
