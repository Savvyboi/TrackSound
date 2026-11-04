"""Nordic grid: Fingrid's 10 Hz frequency archive (dataset 339 on data.fingrid.fi).

One ``.7z`` file per month (about 60 MB) at ``https://data.fingrid.fi/files/339/<YYYY>/<YYYY-MM>.7z``
holding daily CSVs (``Time,Value``; 10 samples per second; Finnish local time, UTC+2/UTC+3).
No API key is needed for these files (Fingrid's REST API does need one; it is not used).
Licence: CC BY 4.0, Fingrid Oyj. The archive on this path starts in June 2023; files appear
about two weeks after the month ends.

A downloaded month is decompressed with the bundled 7z reader, averaged to 1 s (bins centred on
whole UTC seconds), split into UTC days and cached; the archive itself is then deleted.
"""
from __future__ import annotations

import datetime as dt
import io
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from .common import (DAY, DayCache, Progress, RefInfo, Series, assemble, clean, days_between, download, log,
                     samples_to_day)
from .sevenzip import SevenZip

URL = "https://data.fingrid.fi/files/339/{y:04d}/{y:04d}-{m:02d}.7z"
TZ = "Europe/Helsinki"
FIRST_MONTH = (2023, 6)

INFO = RefInfo(
    id="nordic-fingrid", name="Nordic (Fingrid, 10 Hz)", grid="Nordic", nominal_hz=50, source="builtin",
    resolution_s=0.1, coverage=None, licence="CC BY 4.0 (Fingrid Oyj)",
    url="https://data.fingrid.fi/en/datasets/339",
    available_from=dt.datetime(2023, 6, 1, tzinfo=dt.timezone.utc).timestamp(),
    available_note="June 2023 to the last published month (files appear about two weeks after the month); "
                   "Finland, Sweden, Norway and eastern Denmark share this grid")

Month = tuple[int, int]


def local_months_for_day(day0: int) -> list[Month]:
    """Months of the Finnish local dates that a UTC day touches (UTC+2/+3: today and tomorrow)."""
    out: list[Month] = []
    for t in (day0, day0 + DAY - 1):
        d = pd.Timestamp(t, unit="s", tz="UTC").tz_convert(TZ)
        key = (d.year, d.month)
        if key not in out:
            out.append(key)
    return out


def parse_day_csv(raw: bytes) -> tuple[np.ndarray, np.ndarray]:
    """(unix s, Hz) from one Fingrid daily CSV (local time with DST, 10 Hz)."""
    df = pd.read_csv(io.BytesIO(raw), dtype={"Value": "float64"})
    tcol, fcol = df.columns[0], df.columns[1]
    t = pd.to_datetime(df[tcol], format="%Y-%m-%d %H:%M:%S.%f", errors="coerce")
    ok = t.notna().to_numpy() & np.isfinite(df[fcol].to_numpy(float))
    t, f = t[ok], df[fcol].to_numpy(float)[ok]
    try:
        loc = t.dt.tz_localize(TZ, ambiguous="infer", nonexistent="NaT")
    except Exception:  # noqa: BLE001 - unordered repeated hour: drop it rather than guess
        loc = t.dt.tz_localize(TZ, ambiguous="NaT", nonexistent="NaT")
    good = loc.notna().to_numpy()
    secs = loc[good].dt.tz_convert("UTC").dt.as_unit("ns").astype("int64").to_numpy() / 1e9
    return secs, f[good]


class FingridSource:
    info = INFO

    def __init__(self, cache_root: Path):
        self.root = cache_root / INFO.id
        self.cache = DayCache(self.root)

    def _index(self) -> dict:
        p = self.root / "months.json"
        try:
            return json.loads(p.read_text()) if p.exists() else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _mark(self, key: Month, status: str) -> None:
        d = self._index()
        d[f"{key[0]}-{key[1]:02d}"] = {"status": status, "at": time.time()}
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "months.json").write_text(json.dumps(d, indent=1))

    def _status(self, key: Month) -> str | None:
        e = self._index().get(f"{key[0]}-{key[1]:02d}")
        if not e:
            return None
        if e["status"] == "missing" and time.time() - e["at"] > 12 * 3600:
            return None                      # try again: the month may have been published since
        return e["status"]

    def _process_month(self, key: Month, progress: Progress) -> bool:
        """Download, decompress and cache one month. False if it is not published."""
        if key < FIRST_MONTH:
            return False
        url = URL.format(y=key[0], m=key[1])
        tmp = self.root / "tmp" / f"{key[0]}-{key[1]:02d}.7z"
        got = download(url, tmp, lambda f, m: progress(0.6 * f, m), label=f"Fingrid {key[0]}-{key[1]:02d}",
                       ok_404=True)
        if got is None:
            self._mark(key, "missing")
            return False
        try:
            arc = SevenZip(tmp)
            files = [e for e in arc.entries if e.has_stream and e.name.lower().endswith(".csv")]
            for i, (entry, raw) in enumerate(arc.files()):
                if not entry.name.lower().endswith(".csv"):
                    continue
                progress(0.6 + 0.4 * i / max(1, len(files)), f"Fingrid {key[0]}-{key[1]:02d}: {entry.name.rsplit('/', 1)[-1]}")
                t, f = parse_day_csv(raw)
                if len(t) == 0:
                    continue
                for d0 in days_between(t.min(), t.max() + 1):
                    sel = (t >= d0 - 1) & (t < d0 + DAY + 1)
                    self.cache.merge(d0, samples_to_day(t[sel], f[sel], d0, resolution_s=0.1))
        finally:
            tmp.unlink(missing_ok=True)
        self._mark(key, "done")
        return True

    def _day(self, day0: int, progress: Progress) -> np.ndarray | None:
        months = local_months_for_day(day0)
        for key in months:
            st = self._status(key)
            if st == "done":
                continue
            if st == "missing":
                continue
            self._process_month(key, progress)
        if not any(self._status(k) == "done" for k in months):
            return None
        arr = self.cache.get(day0)
        return None if arr is None else clean(arr, 50.0).astype(np.float32)

    def load(self, t0_utc: float, t1_utc: float, progress: Progress) -> Series:
        days = days_between(t0_utc, t1_utc)
        out, missing = {}, 0
        for i, d in enumerate(days):
            progress(i / len(days), f"Fingrid frequency, day {i + 1} of {len(days)}")
            try:
                arr = self._day(d, lambda f, m, i=i: progress((i + f) / len(days), m))
            except ValueError as e:
                log.warning("Fingrid archive unreadable: %s", e)
                raise
            if arr is None:
                missing += 1
            else:
                out[d] = arr
        s = assemble(out, t0_utc, t1_utc)
        if missing:
            s.notes.append(f"Fingrid has no file for {missing} of the {len(days)} days (the archive starts in "
                           "June 2023 and each month appears about two weeks after it ends).")
        return s
