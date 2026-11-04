"""Lightning catalogues: strike records, CSV import and array views for the solver.

A *strike* is one catalogued lightning event (a GLM flash, or a ground-network stroke):
``{id, t_utc, lat, lon, sigma_m, energy, kind, source}`` (see docs/API.md ``Strike``).
``sigma_m`` is the catalogue's 1-sigma location error. It dominates the timing tolerance of
a thunder match: 4 km of GLM error is about 12 s of sound travel, 300 m about 0.9 s.
"""
from __future__ import annotations

import io
import math
import re
import time
from dataclasses import dataclass

import numpy as np

DEFAULT_CSV_SIGMA_M = 500.0     # ground networks are 0.1-1 km; used when the CSV has no sigma column
MAX_CSV_ROWS = 2_000_000

# header aliases (after normalising: lower case, units in brackets dropped, spaces -> _)
_ALIASES = {
    "time": ["time", "t", "t_utc", "time_utc", "utc", "timestamp", "datetime", "date_time", "unix", "unix_time",
             "epoch", "time_unix", "unix_s", "timestamp_utc", "datetime_utc", "time_iso", "iso_time"],
    "date": ["date", "date_utc", "day"],
    "lat": ["lat", "latitude", "lat_deg", "latitude_deg", "y"],
    "lon": ["lon", "lng", "long", "longitude", "lon_deg", "longitude_deg", "x"],
    "sigma_m": ["sigma_m", "sigma", "error_m", "err_m", "accuracy_m", "accuracy", "uncertainty_m", "uncertainty",
                "location_error_m", "loc_error_m", "error", "ellipse_m", "semi_major_m"],
    "sigma_km": ["sigma_km", "error_km", "err_km", "accuracy_km", "uncertainty_km", "location_error_km"],
    "energy": ["energy", "peak_current", "peakcurrent", "current", "peak_current_ka", "current_ka", "ka", "amplitude",
               "flash_energy", "intensity", "polarity_current"],
    "kind": ["kind", "type", "stroke_type", "flash_type", "class", "cloud", "cg_ic", "category"],
}

_KIND_CG = {"cg", "g", "ground", "cloud-to-ground", "cloud_to_ground", "+cg", "-cg", "pcg", "ncg", "return_stroke",
            "stroke"}
_KIND_IC = {"ic", "c", "cc", "cloud", "intracloud", "intra-cloud", "in-cloud", "ic/cc", "intra_cloud"}
_FLAG_COLUMNS = {"cloud", "cg_ic", "is_ic", "is_cloud", "cloud_flag", "ic"}   # 1/true means in-cloud


def normalise_kind(value, flag_column: bool = False) -> str | None:
    """'cg', 'ic', another short label, or None. For a cloud flag column, 1/true means 'ic'."""
    if value is None:
        return None
    s = str(value).strip().lower()
    if not s or s in ("nan", "none", "null"):
        return None
    if flag_column and s in ("0", "false", "f", "no", "n"):
        return "cg"
    if flag_column and s in ("1", "true", "t", "yes", "y"):
        return "ic"
    if s in _KIND_CG:
        return "cg"
    if s in _KIND_IC:
        return "ic"
    return s[:24]


def _norm_header(h: str) -> str:
    h = str(h).strip().lower().lstrip("﻿")
    h = re.sub(r"\(([^)]*)\)|\[([^\]]*)\]", lambda m: "_" + (m.group(1) or m.group(2) or ""), h)
    h = re.sub(r"[^a-z0-9+]+", "_", h).strip("_")
    return h


def _find_column(headers: dict[str, str], field: str) -> str | None:
    for alias in _ALIASES[field]:
        if alias in headers:
            return headers[alias]
    return None


def parse_times(values) -> np.ndarray:
    """Unix seconds from ISO-8601 strings or numbers (s, ms or µs since 1970). NaN where unreadable.

    Naive ISO times are taken as UTC.
    """
    import pandas as pd
    s = pd.Series(values)
    num = pd.to_numeric(s, errors="coerce")
    out = np.full(len(s), np.nan)
    is_num = num.notna().to_numpy()
    if is_num.any():
        v = num.to_numpy(dtype=np.float64)[is_num]
        # a plain number is unix seconds; very large ones are ms or µs
        v = np.where(np.abs(v) > 1e14, v / 1e6, np.where(np.abs(v) > 1e11, v / 1e3, v))
        out[is_num] = v
    rest = ~is_num
    if rest.any():
        txt = s[rest].astype(str).str.strip()
        dt = pd.to_datetime(txt, utc=True, errors="coerce", format="ISO8601")
        if dt.isna().any():
            dt2 = pd.to_datetime(txt[dt.isna()], utc=True, errors="coerce", format="mixed")
            dt = dt.where(dt.notna(), dt2)
        ok = dt.notna().to_numpy()
        vals = np.full(len(txt), np.nan)
        if ok.any():
            vals[ok] = (dt[ok] - pd.Timestamp("1970-01-01", tz="UTC")).dt.total_seconds().to_numpy()
        out[rest] = vals
    return out


def parse_csv(data: bytes | str, default_sigma_m: float = DEFAULT_CSV_SIGMA_M, source: str = "csv") -> list[dict]:
    """Strikes from a CSV file. Required columns: time (ISO-8601 or unix s), lat, lon.

    Optional: sigma_m (or sigma_km), energy, kind. Common aliases are accepted
    (latitude/longitude, timestamp, peak_current, type ...); a separate date column is joined
    with a time-of-day column. Rows with unreadable time or position are skipped; a file with
    no usable rows raises ValueError.
    """
    import pandas as pd
    if isinstance(data, bytes):
        text = data.decode("utf-8-sig", errors="replace")
    else:
        text = data
    if not text.strip():
        raise ValueError("the CSV file is empty")
    try:
        df = pd.read_csv(io.StringIO(text), sep=None, engine="python", dtype=str, skipinitialspace=True,
                         comment="#", nrows=MAX_CSV_ROWS)
    except Exception as e:  # noqa: BLE001 - pandas raises many kinds of parse errors
        raise ValueError(f"could not read the CSV: {e}") from e
    headers = {_norm_header(c): c for c in df.columns}
    c_time, c_date = _find_column(headers, "time"), _find_column(headers, "date")
    c_lat, c_lon = _find_column(headers, "lat"), _find_column(headers, "lon")
    missing = [n for n, c in (("time", c_time or c_date), ("lat", c_lat), ("lon", c_lon)) if c is None]
    if missing:
        raise ValueError(f"the CSV needs columns time, lat, lon (missing: {', '.join(missing)}; "
                         f"found: {', '.join(map(str, df.columns))})")
    if c_time is not None and c_date is not None and c_date != c_time:
        tod = df[c_time].astype(str).str.strip()
        has_date = tod.str.contains(r"\d{4}-\d{2}-\d{2}|^\d{9,}(?:\.\d*)?$", regex=True)
        joined = df[c_date].astype(str).str.strip() + "T" + tod
        times = parse_times(np.where(has_date, tod, joined))
    else:
        times = parse_times(df[c_time or c_date])
    lat = pd.to_numeric(df[c_lat], errors="coerce").to_numpy(dtype=np.float64)
    lon = pd.to_numeric(df[c_lon], errors="coerce").to_numpy(dtype=np.float64)

    c_sig, c_sigkm = _find_column(headers, "sigma_m"), _find_column(headers, "sigma_km")
    if c_sig is not None:
        sigma = pd.to_numeric(df[c_sig], errors="coerce").to_numpy(dtype=np.float64)
    elif c_sigkm is not None:
        sigma = pd.to_numeric(df[c_sigkm], errors="coerce").to_numpy(dtype=np.float64) * 1000.0
    else:
        sigma = np.full(len(df), np.nan)
    sigma = np.where(np.isfinite(sigma) & (sigma > 0), sigma, default_sigma_m)
    c_en, c_kind = _find_column(headers, "energy"), _find_column(headers, "kind")
    energy = (pd.to_numeric(df[c_en], errors="coerce").to_numpy(dtype=np.float64) if c_en is not None
              else np.full(len(df), np.nan))
    kinds = df[c_kind].tolist() if c_kind is not None else [None] * len(df)
    flag_col = c_kind is not None and _norm_header(c_kind) in _FLAG_COLUMNS

    ok = np.isfinite(times) & np.isfinite(lat) & np.isfinite(lon) & (np.abs(lat) <= 90) & (np.abs(lon) <= 360)
    if not ok.any():
        raise ValueError("no usable rows in the CSV (check the time, lat and lon columns)")
    lon = np.where(lon > 180, lon - 360, lon)
    strikes = [make_strike(times[i], lat[i], lon[i], sigma[i],
                           energy[i] if np.isfinite(energy[i]) else None, normalise_kind(kinds[i], flag_col), source)
               for i in np.flatnonzero(ok)]
    return assign_ids(strikes)


def make_strike(t_utc: float, lat: float, lon: float, sigma_m: float, energy: float | None, kind: str | None,
                source: str) -> dict:
    return {"id": "", "t_utc": round(float(t_utc), 4), "lat": round(float(lat), 6), "lon": round(float(lon), 6),
            "sigma_m": round(float(sigma_m), 1), "energy": None if energy is None else float(energy),
            "kind": kind, "source": source}


def assign_ids(strikes: list[dict]) -> list[dict]:
    """Sort by time and give stable ids (``st_00000`` ...)."""
    strikes.sort(key=lambda s: (s["t_utc"], s["lat"], s["lon"]))
    width = max(5, len(str(len(strikes))))
    for i, s in enumerate(strikes):
        s["id"] = f"st_{i:0{width}d}"
    return strikes


def strike_set(source: str, strikes: list[dict], t0_utc: float | None = None, t1_utc: float | None = None,
               bbox: dict | None = None) -> dict:
    """The StrikeSet summary stored next to the strikes."""
    ts = [s["t_utc"] for s in strikes]
    return {"source": source,
            "t0_utc": float(t0_utc if t0_utc is not None else (min(ts) if ts else 0.0)),
            "t1_utc": float(t1_utc if t1_utc is not None else (max(ts) if ts else 0.0)),
            "bbox": bbox if bbox is not None else strikes_bbox(strikes),
            "count": len(strikes), "fetched_at": time.time()}


def strikes_bbox(strikes: list[dict]) -> dict | None:
    if not strikes:
        return None
    lat = [s["lat"] for s in strikes]
    lon = [s["lon"] for s in strikes]
    return {"lat_min": min(lat), "lat_max": max(lat), "lon_min": min(lon), "lon_max": max(lon)}


# ---- array view for the solver ----------------------------------------------------------------

# extra "early arrival" allowance (m) by kind: thunder's first arrival comes from the nearest
# part of the channel, which for in-cloud or long flashes (and GLM's energy-weighted flash
# centroids) can be km closer than the catalogued point; for ground strike points it is small.
EARLY_EXTRA_M = {"cg": 250.0, "ic": 1500.0, "flash": 1500.0}
EARLY_EXTRA_DEFAULT_M = 800.0


@dataclass
class StrikeArrays:
    ids: list[str]
    t: np.ndarray          # unix s
    lat: np.ndarray
    lon: np.ndarray
    sigma_m: np.ndarray
    early_m: np.ndarray    # extra tolerance for early arrivals

    def __len__(self) -> int:
        return len(self.ids)

    def subset(self, idx) -> "StrikeArrays":
        idx = np.asarray(idx)
        return StrikeArrays([self.ids[i] for i in idx], self.t[idx], self.lat[idx], self.lon[idx],
                            self.sigma_m[idx], self.early_m[idx])


def to_arrays(strikes: list[dict]) -> StrikeArrays:
    order = sorted(range(len(strikes)), key=lambda i: strikes[i]["t_utc"])
    s = [strikes[i] for i in order]
    return StrikeArrays(
        ids=[x["id"] for x in s],
        t=np.array([x["t_utc"] for x in s], dtype=np.float64),
        lat=np.array([x["lat"] for x in s], dtype=np.float64),
        lon=np.array([x["lon"] for x in s], dtype=np.float64),
        sigma_m=np.array([max(1.0, float(x.get("sigma_m") or DEFAULT_CSV_SIGMA_M)) for x in s], dtype=np.float64),
        early_m=np.array([EARLY_EXTRA_M.get(x.get("kind") or "", EARLY_EXTRA_DEFAULT_M) for x in s], dtype=np.float64),
    )


def describe_sigma(sigma_m: np.ndarray) -> str:
    """'about 4 km' / 'about 300 m' for notes."""
    if len(sigma_m) == 0:
        return "unknown"
    med = float(np.median(sigma_m))
    if med >= 1000:
        return f"about {med / 1000:.1f} km".replace(".0 km", " km")
    return f"about {int(round(med, -1 if med >= 100 else 0))} m"


def sound_seconds(m: float, c: float) -> float:
    return float(m) / c if c > 0 else math.inf
