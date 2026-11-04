"""References imported from CSV files (``time,frequency``), stored under ``<data>/enf/references``.

``time`` is ISO-8601 (an explicit offset is honoured; times without one are taken as UTC) or unix
seconds; ``frequency`` is in Hz. Comma, semicolon or tab separated, with or without a header.
Any resolution is accepted: sub-second data is averaged to 1 s, coarser data is interpolated
across gaps of up to 2.5 sampling intervals.
"""
from __future__ import annotations

import io
import json
import re
import time
from pathlib import Path

import numpy as np
import pandas as pd

from ....store import NotFound, new_id
from .common import RefInfo, Series, clean

MAX_SPAN_DAYS = 120
COVERAGE_GAP_S = 600       # gaps shorter than this do not split the coverage list
TIME_NAMES = ("time", "timestamp", "datetime", "date", "dtm", "utc", "t")
FREQ_NAMES = ("frequency", "freq", "f", "hz", "value", "frequency_hz")


def _dir(data_dir: Path) -> Path:
    return data_dir / "enf" / "references"


def parse_csv(raw: bytes) -> tuple[np.ndarray, np.ndarray]:
    """(unix s, Hz), sorted, from CSV bytes."""
    text = raw.decode("utf-8-sig", errors="replace")
    first = next((ln for ln in text.splitlines() if ln.strip()), "")
    sep = ";" if first.count(";") > first.count(",") else ("\t" if "\t" in first else ",")
    has_header = not re.match(r"\s*[-+]?\d", first.split(sep)[0].strip().strip('"'))
    df = pd.read_csv(io.StringIO(text), sep=sep, header=0 if has_header else None, dtype=str, skipinitialspace=True)
    if df.shape[1] < 2:
        raise ValueError("the CSV needs two columns: time and frequency")
    cols = [str(c).strip().lower() for c in df.columns]
    ti = next((i for i, c in enumerate(cols) if c in TIME_NAMES), 0)
    fi = next((i for i, c in enumerate(cols) if c in FREQ_NAMES and i != ti), 1 if ti == 0 else 0)
    ts, fs = df.iloc[:, ti].str.strip().str.strip('"'), pd.to_numeric(df.iloc[:, fi], errors="coerce")
    num = pd.to_numeric(ts, errors="coerce")
    if num.notna().mean() > 0.95:
        t = num.to_numpy(float)
        if np.nanmedian(t) > 1e11:          # milliseconds
            t = t / 1000.0
    else:
        dtv = pd.to_datetime(ts, utc=True, errors="coerce", format="mixed")
        if dtv.notna().mean() < 0.95:
            bad = ts[dtv.isna()].iloc[0] if dtv.isna().any() else ""
            raise ValueError(f"could not read the time column (e.g. {bad!r}); use ISO-8601 or unix seconds")
        t = dtv.dt.as_unit("ns").astype("int64").to_numpy().astype(float) / 1e9
        t[dtv.isna().to_numpy()] = np.nan
    f = fs.to_numpy(float)
    ok = np.isfinite(t) & np.isfinite(f)
    if ok.sum() < 60:
        raise ValueError("the CSV has fewer than 60 usable rows")
    t, f = t[ok], f[ok]
    order = np.argsort(t, kind="stable")
    return t[order], f[order]


def to_one_second(t: np.ndarray, f: np.ndarray) -> tuple[int, np.ndarray, float]:
    """(t0, 1 s values with NaN gaps, native resolution in s)."""
    dt_ = np.diff(t)
    pos = dt_[dt_ > 0]
    res = float(np.median(pos)) if len(pos) else 1.0
    t0 = int(np.floor(t[0]))
    t1 = int(np.ceil(t[-1])) + 1
    if (t1 - t0) > MAX_SPAN_DAYS * 86400:
        raise ValueError(f"the reference spans more than {MAX_SPAN_DAYS} days; split it into several files")
    n = t1 - t0
    if res < 0.99:
        idx = np.floor(t - t0 + 0.5).astype(int)
        idx = np.clip(idx, 0, n - 1)
        s = np.bincount(idx, weights=f, minlength=n)
        c = np.bincount(idx, minlength=n)
        with np.errstate(invalid="ignore", divide="ignore"):
            out = np.where(c > 0, s / np.maximum(c, 1), np.nan)
    elif res <= 1.01:
        out = np.full(n, np.nan)
        out[np.clip(np.rint(t - t0).astype(int), 0, n - 1)] = f
    else:
        grid = t0 + np.arange(n, dtype=float)
        out = np.interp(grid, t, f)
        j = np.searchsorted(t, grid)
        lo, hi = np.clip(j - 1, 0, len(t) - 1), np.clip(j, 0, len(t) - 1)
        span = t[hi] - t[lo]
        exact = np.isclose(t[hi], grid) | np.isclose(t[lo], grid)
        out[(span > 2.5 * res) & ~exact] = np.nan
    return t0, out, res


def coverage(t0: int, values: np.ndarray, gap_s: int = COVERAGE_GAP_S, max_items: int = 200) -> list[dict]:
    ok = np.isfinite(values)
    if not ok.any():
        return []
    d = np.diff(np.concatenate([[0], ok.astype(int), [0]]))
    starts, ends = np.where(d == 1)[0], np.where(d == -1)[0]
    runs: list[list[int]] = []
    for s, e in zip(starts, ends):
        if runs and s - runs[-1][1] <= gap_s:
            runs[-1][1] = e
        else:
            runs.append([s, e])
    while len(runs) > max_items:          # merge the closest pair until the list is short enough
        gaps = [runs[i + 1][0] - runs[i][1] for i in range(len(runs) - 1)]
        i = int(np.argmin(gaps))
        runs[i][1] = runs[i + 1][1]
        del runs[i + 1]
    return [{"t0_utc": float(t0 + s), "t1_utc": float(t0 + e)} for s, e in runs]


def import_csv(data_dir: Path, raw: bytes, name: str, grid: str, nominal_hz: int) -> dict:
    if nominal_hz not in (50, 60):
        raise ValueError("nominal_hz must be 50 or 60")
    t, f = parse_csv(raw)
    med = float(np.median(f))
    if abs(med - nominal_hz) > 2.0:
        raise ValueError(f"the frequencies (median {med:.3f}) are not near {nominal_hz} Hz; "
                         "the column must be the frequency in Hz")
    t0, values, res = to_one_second(t, f)
    values = clean(values, nominal_hz)
    rid = new_id("er")
    meta = {"id": rid, "name": (name or "Imported reference").strip()[:120], "grid": (grid or "Unknown").strip()[:80],
            "nominal_hz": int(nominal_hz), "source": "import", "resolution_s": round(res, 3),
            "coverage": coverage(t0, values), "licence": None, "url": None,
            "created_at": time.time(), "n_samples": int(len(t))}
    d = _dir(data_dir)
    d.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(d / f"{rid}.npz", t0=t0, f=values.astype(np.float32))
    (d / f"{rid}.json").write_text(json.dumps(meta, indent=1))
    return meta


def list_imported(data_dir: Path) -> list[dict]:
    d = _dir(data_dir)
    out = []
    if d.exists():
        for p in sorted(d.glob("er_*.json")):
            try:
                out.append(json.loads(p.read_text()))
            except (OSError, json.JSONDecodeError):
                continue
    out.sort(key=lambda m: m.get("created_at", 0))
    return out


def get_imported(data_dir: Path, rid: str) -> dict:
    if not re.fullmatch(r"er_[a-z0-9]+", rid or ""):
        raise NotFound(rid)
    p = _dir(data_dir) / f"{rid}.json"
    if not p.exists():
        raise NotFound(rid)
    return json.loads(p.read_text())


def info_from_meta(meta: dict) -> RefInfo:
    keys = ("id", "name", "grid", "nominal_hz", "source", "resolution_s", "coverage", "licence", "url")
    return RefInfo(**{k: meta.get(k) for k in keys})


def load_imported(data_dir: Path, rid: str, t0_utc: float, t1_utc: float) -> Series:
    get_imported(data_dir, rid)
    with np.load(_dir(data_dir) / f"{rid}.npz") as z:
        r0, f = int(z["t0"]), z["f"]
    a, b = int(np.floor(t0_utc)), int(np.ceil(t1_utc))
    out = np.full(b - a, np.nan, np.float32)
    lo, hi = max(a, r0), min(b, r0 + len(f))
    if hi > lo:
        out[lo - a:hi - a] = f[lo - r0:hi - r0]
    return Series(a, out)


def delete_imported(data_dir: Path, rid: str) -> None:
    get_imported(data_dir, rid)
    for ext in ("json", "npz"):
        (_dir(data_dir) / f"{rid}.{ext}").unlink(missing_ok=True)
