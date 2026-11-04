"""Shared plumbing for reference grid-frequency data.

Every reference is served as a 1 s series: ``values[i]`` is the grid frequency (Hz) at UTC
``t0 + i`` with NaN for missing data. Built-in sources download public data on demand and keep
one float32 file of 86 400 values per UTC day under ``<cache>/enf/<source id>/``.
"""
from __future__ import annotations

import datetime as dt
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np
import requests

log = logging.getLogger("tracksound.enf")

DAY = 86400
Progress = Callable[[float, str], None]
USER_AGENT = "TrackSound/0.1 (local ENF analysis; https://github.com/)"
STUCK_RUN_S = 30          # identical values for this long mean a frozen meter, not the grid


class SourceUnavailable(Exception):
    """The source has no data for the requested period (not an error in our code)."""


@dataclass
class RefInfo:
    """Description of a reference (the EnfReference document of docs/API.md plus extras)."""
    id: str
    name: str
    grid: str
    nominal_hz: int
    source: str                              # "builtin" | "import"
    resolution_s: float
    coverage: list[dict] | None
    licence: str | None
    url: str | None
    available_from: float | None = None      # builtin: earliest UTC with data (approximate)
    available_note: str | None = None
    extra: dict = field(default_factory=dict)

    def public(self) -> dict:
        return {"id": self.id, "name": self.name, "grid": self.grid, "nominal_hz": self.nominal_hz,
                "source": self.source, "resolution_s": self.resolution_s, "coverage": self.coverage,
                "licence": self.licence, "url": self.url}


@dataclass
class Series:
    """A 1 s reference window."""
    t0: int
    values: np.ndarray
    notes: list[str] = field(default_factory=list)

    @property
    def t1(self) -> int:
        return self.t0 + len(self.values)

    def valid_fraction(self) -> float:
        return float(np.mean(np.isfinite(self.values))) if len(self.values) else 0.0


# ---- time helpers ---------------------------------------------------------------------------------

def day_start(t_utc: float) -> int:
    return int(t_utc // DAY * DAY)


def days_between(t0_utc: float, t1_utc: float) -> list[int]:
    """UTC midnights of every day touching [t0_utc, t1_utc)."""
    out, d = [], day_start(t0_utc)
    while d < t1_utc:
        out.append(d)
        d += DAY
    return out


def iso_day(day0: int) -> str:
    return dt.datetime.fromtimestamp(day0, dt.timezone.utc).strftime("%Y-%m-%d")


def utc_datetime(t: float) -> dt.datetime:
    return dt.datetime.fromtimestamp(t, dt.timezone.utc)


# ---- HTTP -----------------------------------------------------------------------------------------

_session: requests.Session | None = None


def session() -> requests.Session:
    global _session
    if _session is None:
        s = requests.Session()
        s.headers["User-Agent"] = USER_AGENT
        _session = s
    return _session


def http_get(url: str, *, params: dict | None = None, timeout: float = 120.0, stream: bool = False,
             retries: int = 3, ok_404: bool = False) -> requests.Response | None:
    """GET with a few retries on network errors and 5xx/409/429. Returns None on 404 when
    ``ok_404`` (the file does not exist yet)."""
    last: Exception | None = None
    for attempt in range(retries):
        try:
            r = session().get(url, params=params, timeout=timeout, stream=stream)
            if r.status_code == 404 and ok_404:
                return None
            if r.status_code in (409, 429) or r.status_code >= 500:
                raise requests.HTTPError(f"HTTP {r.status_code} from {url}", response=r)
            r.raise_for_status()
            return r
        except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as e:
            resp = getattr(e, "response", None)
            if resp is not None and 400 <= resp.status_code < 500 and resp.status_code not in (409, 429):
                raise
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"download failed after {retries} attempts: {last}")


def download(url: str, dst: Path, progress: Progress | None = None, label: str = "Downloading",
             ok_404: bool = False) -> Path | None:
    """Stream ``url`` to ``dst`` (atomically). None if 404 and ``ok_404``."""
    r = http_get(url, stream=True, ok_404=ok_404, timeout=300.0)
    if r is None:
        return None
    total = int(r.headers.get("content-length") or 0)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".part")
    got = 0
    with open(tmp, "wb") as fh:
        for chunk in r.iter_content(1 << 20):
            fh.write(chunk)
            got += len(chunk)
            if progress and total:
                progress(got / total, f"{label} ({got >> 20} of {total >> 20} MB)")
    tmp.replace(dst)
    return dst


# ---- day cache ----------------------------------------------------------------------------------------

class DayCache:
    """One float32 array of 86 400 seconds per UTC day: ``<dir>/<YYYY-MM-DD>.npy``."""

    def __init__(self, root: Path):
        self.root = root

    def path(self, day0: int) -> Path:
        return self.root / f"{iso_day(day0)}.npy"

    def get(self, day0: int) -> np.ndarray | None:
        p = self.path(day0)
        if not p.exists():
            return None
        try:
            a = np.load(p)
        except (OSError, ValueError):
            return None
        return a if a.shape == (DAY,) else None

    def put(self, day0: int, values: np.ndarray) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        p = self.path(day0)
        tmp = p.with_name(p.name + ".tmp.npy")
        np.save(tmp, np.asarray(values, np.float32))
        tmp.replace(p)

    def merge(self, day0: int, values: np.ndarray) -> np.ndarray:
        """Fill the cached day's gaps with ``values`` (for days assembled from two files)."""
        old = self.get(day0)
        new = np.asarray(values, np.float32)
        if old is not None:
            new = np.where(np.isfinite(old), old, new)
        self.put(day0, new)
        return new


def samples_to_day(t_utc: np.ndarray, f: np.ndarray, day0: int, resolution_s: float = 1.0,
                   max_gap_s: float | None = None) -> np.ndarray:
    """Place (time, frequency) samples onto the day's 1 s grid.

    Sub-second data is averaged over ``[s - 0.5, s + 0.5)``; 1 s data is placed directly; coarser
    data (``resolution_s`` > 1) is linearly interpolated across gaps up to ``max_gap_s``.
    """
    out = np.full(DAY, np.nan)
    t = np.asarray(t_utc, float) - day0
    f = np.asarray(f, float)
    keep = np.isfinite(t) & np.isfinite(f) & (t > -max(2.0, 2 * resolution_s)) & (t < DAY + max(2.0, 2 * resolution_s))
    t, f = t[keep], f[keep]
    if len(t) == 0:
        return out
    if resolution_s < 0.99:
        idx = np.floor(t + 0.5).astype(int)
        inside = (idx >= 0) & (idx < DAY)
        s = np.bincount(idx[inside], weights=f[inside], minlength=DAY)
        n = np.bincount(idx[inside], minlength=DAY)
        with np.errstate(invalid="ignore", divide="ignore"):
            out = np.where(n > 0, s / np.maximum(n, 1), np.nan)
        return out
    if resolution_s <= 1.01:
        idx = np.rint(t).astype(int)
        inside = (idx >= 0) & (idx < DAY)
        out[idx[inside]] = f[inside]
        return out
    order = np.argsort(t)
    t, f = t[order], f[order]
    grid = np.arange(DAY, dtype=float)
    out = np.interp(grid, t, f, left=np.nan, right=np.nan)
    gap = max_gap_s if max_gap_s is not None else 2.5 * resolution_s
    # blank seconds whose neighbouring samples are too far apart
    j = np.searchsorted(t, grid)
    lo = np.clip(j - 1, 0, len(t) - 1)
    hi = np.clip(j, 0, len(t) - 1)
    span = np.where((j > 0) & (j < len(t)), t[hi] - t[lo], np.inf)
    exact = np.isclose(t[hi], grid) | np.isclose(t[lo], grid)
    out[(span > gap) & ~exact] = np.nan
    return out


def clean(values: np.ndarray, nominal_hz: float | None = None, stuck_s: int = STUCK_RUN_S) -> np.ndarray:
    """Blank implausible values: out of range, and runs of identical values (a frozen meter)."""
    x = np.asarray(values, float).copy()
    if nominal_hz:
        x[(x < nominal_hz - 2.0) | (x > nominal_hz + 2.0)] = np.nan
    if len(x) < stuck_s:
        return x
    same = np.concatenate([[False], x[1:] == x[:-1]])
    # run starts where `same` turns on: blank runs of >= stuck_s equal values
    d = np.diff(np.concatenate([[0], same.astype(int), [0]]))
    starts, ends = np.where(d == 1)[0], np.where(d == -1)[0]
    for s, e in zip(starts, ends):
        if e - s + 1 >= stuck_s:
            x[s - 1:e] = np.nan
    return x


def assemble(days: dict[int, np.ndarray], t0_utc: float, t1_utc: float) -> Series:
    """Cut [t0_utc, t1_utc) out of per-day arrays (missing days are NaN)."""
    a, b = int(np.floor(t0_utc)), int(np.ceil(t1_utc))
    out = np.full(b - a, np.nan, np.float32)
    for d0, arr in days.items():
        lo, hi = max(a, d0), min(b, d0 + DAY)
        if hi > lo and arr is not None:
            out[lo - a:hi - a] = arr[lo - d0:hi - d0]
    return Series(a, out)
