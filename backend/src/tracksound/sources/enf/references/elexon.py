"""Great Britain: Elexon BMRS ``FREQ`` dataset (system frequency every 15 s, near real time).

Endpoint: ``https://data.elexon.co.uk/bmrs/api/v1/datasets/FREQ`` with
``measurementDateTimeFrom`` / ``measurementDateTimeTo`` (UTC, at most one day per request).
No key is needed. Licensed under the Elexon BMRS data licence (free re-use with attribution).
Coverage starts in 2021; NESO's 1 s files are better where they exist, but they are published
a month or more late, so this source covers the most recent weeks.
"""
from __future__ import annotations

import datetime as dt
import time
from pathlib import Path

import numpy as np

from .common import (DAY, DayCache, Progress, RefInfo, Series, assemble, clean, days_between, http_get,
                     samples_to_day)

URL = "https://data.elexon.co.uk/bmrs/api/v1/datasets/FREQ"
FIRST_DAY = dt.datetime(2021, 1, 1, tzinfo=dt.timezone.utc).timestamp()   # approximate start of the archive

INFO = RefInfo(
    id="gb-elexon", name="Great Britain (Elexon BMRS, 15 s)", grid="Great Britain", nominal_hz=50,
    source="builtin", resolution_s=15.0, coverage=None,
    licence="Elexon BMRS data licence (free re-use with attribution: 'Contains BMRS data © Elexon Limited')",
    url="https://bmrs.elexon.co.uk/system-frequency", available_from=FIRST_DAY,
    available_note="2021 to a few minutes ago; 15 s samples interpolated to 1 s")


def fetch_samples(t0_utc: float, t1_utc: float) -> tuple[np.ndarray, np.ndarray]:
    """Raw (unix s, Hz) samples between t0 and t1 (at most one day)."""
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    params = {"measurementDateTimeFrom": dt.datetime.fromtimestamp(t0_utc, dt.timezone.utc).strftime(fmt),
              "measurementDateTimeTo": dt.datetime.fromtimestamp(t1_utc, dt.timezone.utc).strftime(fmt),
              "format": "json"}
    r = http_get(URL, params=params, timeout=60.0)
    rows = r.json().get("data", [])
    if not rows:
        return np.zeros(0), np.zeros(0)
    t = np.array([dt.datetime.fromisoformat(x["measurementTime"].replace("Z", "+00:00")).timestamp() for x in rows])
    f = np.array([float(x["frequency"]) for x in rows])
    order = np.argsort(t)
    return t[order], f[order]


class ElexonSource:
    info = INFO

    def __init__(self, cache_root: Path):
        self.cache = DayCache(cache_root / INFO.id)

    def _day(self, day0: int) -> np.ndarray:
        cached = self.cache.get(day0)
        if cached is not None:
            return cached
        t, f = fetch_samples(day0, day0 + DAY)
        arr = clean(samples_to_day(t, f, day0, resolution_s=15.0, max_gap_s=40.0), 50.0)
        if day0 + DAY < time.time() - 6 * 3600:      # complete days only
            self.cache.put(day0, arr)
        return arr.astype(np.float32)

    def load(self, t0_utc: float, t1_utc: float, progress: Progress) -> Series:
        days = days_between(t0_utc, t1_utc)
        out = {}
        for i, d in enumerate(days):
            progress(i / len(days), f"Elexon frequency, day {i + 1} of {len(days)}")
            if d + DAY <= FIRST_DAY or d > time.time():
                continue
            out[d] = self._day(d)
        s = assemble(out, t0_utc, t1_utc)
        s.notes.append("Elexon publishes GB frequency every 15 s; it was interpolated to 1 s, so sub-15 s "
                       "detail is missing and matches are less sharp than with 1 s data.")
        return s
