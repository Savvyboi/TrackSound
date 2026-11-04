"""Fixed windows of GPS-synchronised 1 s recordings from the Power Grid Frequency database.

Jumar, Maass, Schaefer, Rydin Gorjao & Hagenmeyer, "Database of power grid frequency
measurements" (arXiv:2006.01771), https://power-grid-frequency.org/database/ , files on OSF
(project by5hu, CC BY 4.0). Each file is ``Time;f50_XX;QI_XX`` (UTC; deviation from nominal in
mHz; quality indicator, 0 = good). These are short campaigns (days to weeks, 2017-2019), but they
are the only open second-level records found for several grids, including two North American
interconnections. Very large files (Mallorca 324 MB, London 2019-11 135 MB) are left out.
"""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .common import DAY, DayCache, Progress, RefInfo, Series, assemble, clean, days_between, download, samples_to_day

LICENCE = "CC BY 4.0 (Jumar et al., Power Grid Frequency database)"
DB_URL = "https://power-grid-frequency.org/database/"


@dataclass(frozen=True)
class Campaign:
    id: str
    place: str
    grid: str
    nominal_hz: int
    osf: str
    first: str          # first and last day (UTC) of the recording
    last: str
    size_mb: float


CAMPAIGNS = [
    Campaign("pgf-us-west-2019-05", "Salt Lake City, Utah, USA", "North America (Western Interconnection)", 60, "8rp4v",
             "2019-05-19", "2019-05-25", 16.5),
    Campaign("pgf-us-texas-2019-05a", "College Station, Texas, USA", "North America (Texas Interconnection)", 60, "t5wxz",
             "2019-05-15", "2019-05-16", 3.7),
    Campaign("pgf-us-texas-2019-05b", "College Station, Texas, USA", "North America (Texas Interconnection)", 60, "zngy8",
             "2019-05-20", "2019-05-23", 9.6),
    Campaign("pgf-ce-lauris-2019-04", "Lauris, France", "Continental Europe", 50, "hfsrz", "2019-04-16", "2019-04-27", 41.2),
    Campaign("pgf-ce-split-2019-04", "Split, Croatia", "Continental Europe", 50, "r9eh6", "2019-04-09", "2019-04-12", 13.5),
    Campaign("pgf-ce-erice-2019-07", "Erice, Sicily, Italy", "Continental Europe", 50, "c754b", "2019-07-02", "2019-07-06", 17.1),
    Campaign("pgf-ce-krakow-2019-04", "Krakow, Poland", "Continental Europe", 50, "wq3te", "2019-04-04", "2019-04-07", 13.6),
    Campaign("pgf-ce-lisbon-2018-02", "Lisbon, Portugal", "Continental Europe", 50, "5zgwn", "2018-02-14", "2018-02-21", 16.8),
    Campaign("pgf-gb-london-2019-03", "London, United Kingdom", "Great Britain", 50, "cfv47", "2019-03-04", "2019-03-07", 9.2),
    Campaign("pgf-nordic-stockholm-2019-05", "Stockholm, Sweden", "Nordic", 50, "e2xfb", "2019-05-06", "2019-05-13", 23.1),
    Campaign("pgf-baltic-tallinn-2019-03", "Tallinn, Estonia", "Baltic (then synchronous with Russia)", 50, "t5ske",
             "2019-03-25", "2019-04-17", 79.0),
    Campaign("pgf-ru-stpetersburg-2019-04", "St. Petersburg, Russia", "Russia (IPS/UPS)", 50, "tvsyc", "2019-04-30",
             "2019-05-12", 44.5),
    Campaign("pgf-za-capetown-2017-11", "Cape Town, South Africa", "Southern Africa", 50, "gzk7d", "2017-11-19",
             "2017-11-28", 27.0),
    Campaign("pgf-is-reykjavik-2017-10", "Reykjavik, Iceland", "Iceland", 50, "sxph8", "2017-10-14", "2017-10-20", 15.4),
    Campaign("pgf-fo-vestmanna-2019-11", "Vestmanna, Faroe Islands", "Faroe Islands", 50, "a7h5b", "2019-11-03",
             "2019-11-10", 24.5),
    Campaign("pgf-es-grancanaria-2018-02", "Las Palmas, Gran Canaria, Spain", "Gran Canaria", 50, "wz42b", "2018-02-04",
             "2018-02-10", 16.2),
]
BY_ID = {c.id: c for c in CAMPAIGNS}


def _day0(s: str) -> float:
    return dt.datetime.fromisoformat(s).replace(tzinfo=dt.timezone.utc).timestamp()


def info_for(c: Campaign) -> RefInfo:
    return RefInfo(
        id=c.id, name=f"{c.grid} - {c.place} {c.first[:7]} (PGF database, 1 s)", grid=c.grid,
        nominal_hz=c.nominal_hz, source="builtin", resolution_s=1.0,
        coverage=[{"t0_utc": _day0(c.first), "t1_utc": _day0(c.last) + DAY}],
        licence=LICENCE, url=f"https://osf.io/{c.osf}/", available_from=_day0(c.first),
        available_note=f"{c.first} to {c.last} only (a measurement campaign; download about {c.size_mb:.0f} MB)",
        extra={"download_mb": c.size_mb})


def parse_campaign_csv(path: Path) -> tuple[np.ndarray, np.ndarray, int]:
    """(unix s, Hz, nominal) from a PGF database file."""
    df = pd.read_csv(path, sep=";")
    fcol = next(c for c in df.columns if c.lower().startswith("f"))
    qcol = next((c for c in df.columns if c.upper().startswith("QI")), None)
    nominal = 60 if fcol.lower().startswith("f60") else 50
    t = pd.to_datetime(df[df.columns[0]], format="%Y-%m-%d %H:%M:%S", utc=True, errors="coerce")
    f = nominal + pd.to_numeric(df[fcol], errors="coerce").to_numpy(float) / 1000.0
    ok = t.notna().to_numpy() & np.isfinite(f)
    if qcol is not None:
        ok &= pd.to_numeric(df[qcol], errors="coerce").fillna(1).to_numpy() == 0
    secs = t[ok].dt.as_unit("ns").astype("int64").to_numpy() / 1e9
    return secs, f[ok], nominal


class PgfSource:
    def __init__(self, cache_root: Path, ref_id: str):
        self.c = BY_ID[ref_id]
        self.info = info_for(self.c)
        self.root = cache_root / "pgf" / ref_id
        self.cache = DayCache(self.root)

    def _done(self) -> bool:
        return (self.root / "done.json").exists()

    def _fetch(self, progress: Progress) -> None:
        tmp = self.root / "tmp" / f"{self.c.osf}.csv"
        download(f"https://osf.io/{self.c.osf}/download", tmp, progress, label=f"PGF {self.c.place}")
        try:
            t, f, _ = parse_campaign_csv(tmp)
        finally:
            tmp.unlink(missing_ok=True)
        for d0 in days_between(t.min(), t.max() + 1):
            sel = (t >= d0) & (t < d0 + DAY)
            self.cache.put(d0, clean(samples_to_day(t[sel], f[sel], d0), self.c.nominal_hz))
        (self.root / "done.json").write_text(json.dumps({"n": int(len(t)), "t0": float(t.min()), "t1": float(t.max())}))

    def load(self, t0_utc: float, t1_utc: float, progress: Progress) -> Series:
        cov = self.info.coverage[0]
        if t1_utc <= cov["t0_utc"] or t0_utc >= cov["t1_utc"]:
            s = assemble({}, t0_utc, t1_utc)
            s.notes.append(f"This campaign only covers {self.c.first} to {self.c.last}.")
            return s
        if not self._done():
            self._fetch(progress)
        out = {}
        for d in days_between(max(t0_utc, cov["t0_utc"]), min(t1_utc, cov["t1_utc"])):
            arr = self.cache.get(d)
            if arr is not None:
                out[d] = arr
        return assemble(out, t0_utc, t1_utc)
