"""Australia: AEMO's 4 s regional frequency used for Frequency Performance Payments (FPP).

NEMWEB publishes ``PUBLIC_FPP_RUN`` reports every 5 minutes; their ``FPP,REGION_FREQ_MEASURE``
rows give ``FREQ_DEVIATION_HZ`` from 50 Hz every 4 s for each region. Times are NEM time
(AEST, UTC+10, no daylight saving).

* Daily archives (one zip of 288 five-minute zips, about 2 MB, roughly the last 12 months):
  ``https://nemweb.com.au/Reports/Archive/FPPRUN/PUBLIC_FPP_RUN_<YYYYMMDD>.zip``
* The last two days: ``https://nemweb.com.au/Reports/Current/FPPRUN/``

The mainland regions (QLD, NSW, VIC, SA) form one synchronous grid; NSW1 is used for it.
Tasmania (TAS1) is a separate grid behind an HVDC link. FPP started in June 2025, so there is
nothing earlier. Licence: AEMO copyright permissions (free use with attribution).
"""
from __future__ import annotations

import datetime as dt
import io
import json
import re
import time
import zipfile
from pathlib import Path

import numpy as np

from .common import DAY, DayCache, Progress, RefInfo, Series, assemble, clean, days_between, http_get, log, samples_to_day

ARCHIVE = "https://nemweb.com.au/Reports/Archive/FPPRUN/PUBLIC_FPP_RUN_{ymd}.zip"
CURRENT = "https://nemweb.com.au/Reports/Current/FPPRUN/"
NEM_OFFSET_S = 10 * 3600
REGIONS = {"au-nem-aemo": "NSW1", "au-tas-aemo": "TAS1"}
LICENCE = "AEMO copyright permissions (free use with attribution)"
DOC_URL = "https://nemweb.com.au/Reports/Archive/FPPRUN/"
START = dt.datetime(2025, 6, 8, tzinfo=dt.timezone.utc).timestamp()

INFOS = {
    "au-nem-aemo": RefInfo(
        id="au-nem-aemo", name="Australia mainland NEM (AEMO, 4 s)", grid="Australia (NEM mainland)",
        nominal_hz=50, source="builtin", resolution_s=4.0, coverage=None, licence=LICENCE, url=DOC_URL,
        available_from=START, available_note="about the last 12 months (rolling archive); Queensland, New South "
                                             "Wales, Victoria, South Australia"),
    "au-tas-aemo": RefInfo(
        id="au-tas-aemo", name="Tasmania (AEMO, 4 s)", grid="Australia (Tasmania)", nominal_hz=50,
        source="builtin", resolution_s=4.0, coverage=None, licence=LICENCE, url=DOC_URL,
        available_from=START, available_note="about the last 12 months (rolling archive)"),
}


def parse_report(csv_text: str) -> dict[str, tuple[list[float], list[float]]]:
    """{region: (unix s, Hz)} from one FPP_RUN CSV report."""
    out: dict[str, tuple[list[float], list[float]]] = {}
    header: list[str] | None = None
    for line in csv_text.splitlines():
        if line.startswith("I,FPP,REGION_FREQ_MEASURE"):
            header = line.split(",")
            continue
        if not line.startswith("D,FPP,REGION_FREQ_MEASURE") or header is None:
            continue
        row = line.split(",")
        rec = dict(zip(header, row))
        try:
            t_local = dt.datetime.strptime(rec["MEASUREMENT_DATETIME"].strip('"'), "%Y/%m/%d %H:%M:%S")
            dev = float(rec["FREQ_DEVIATION_HZ"])
        except (KeyError, ValueError):
            continue
        if rec.get("HZ_QUALITY_FLAG", "1").strip() not in ("1", ""):
            continue
        t = t_local.replace(tzinfo=dt.timezone.utc).timestamp() - NEM_OFFSET_S
        ts, fs = out.setdefault(rec.get("REGIONID", "").strip('"'), ([], []))
        ts.append(t)
        fs.append(50.0 + dev)
    return out


def _reports_in_zip(raw: bytes):
    """CSV texts inside a (possibly nested) NEMWEB zip."""
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        for name in z.namelist():
            data = z.read(name)
            if name.lower().endswith(".zip"):
                yield from _reports_in_zip(data)
            elif name.lower().endswith(".csv"):
                yield data.decode("latin-1")


class AemoSource:
    def __init__(self, cache_root: Path, ref_id: str):
        self.info = INFOS[ref_id]
        self.region = REGIONS[ref_id]
        self.caches = {rid: DayCache(cache_root / rid) for rid in REGIONS}
        self.root = cache_root / "au-aemo"

    def _index(self) -> dict:
        p = self.root / "nem_days.json"
        try:
            return json.loads(p.read_text()) if p.exists() else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _mark(self, ymd: str, status: str) -> None:
        d = self._index()
        d[ymd] = {"status": status, "at": time.time()}
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "nem_days.json").write_text(json.dumps(d, indent=1))

    def _ingest(self, reports) -> int:
        """Merge region series from reports into every region's day cache. Returns sample count."""
        acc: dict[str, tuple[list[float], list[float]]] = {}
        for text in reports:
            for reg, (t, f) in parse_report(text).items():
                a = acc.setdefault(reg, ([], []))
                a[0].extend(t)
                a[1].extend(f)
        n = 0
        for rid, reg in REGIONS.items():
            if reg not in acc:
                continue
            t, f = np.array(acc[reg][0]), np.array(acc[reg][1])
            n += len(t)
            for d0 in days_between(t.min(), t.max() + 1):
                sel = (t >= d0 - 8) & (t < d0 + DAY + 8)
                self.caches[rid].merge(d0, samples_to_day(t[sel], f[sel], d0, resolution_s=4.0, max_gap_s=10.0))
        return n

    def _nem_day(self, nem_date: dt.date, progress: Progress) -> None:
        ymd = nem_date.strftime("%Y%m%d")
        st = self._index().get(ymd)
        if st and (st["status"] == "done" or (st["status"] == "missing" and time.time() - st["at"] < 3600)):
            return
        r = http_get(ARCHIVE.format(ymd=ymd), ok_404=True, timeout=120.0)
        if r is not None:
            self._ingest(_reports_in_zip(r.content))
            self._mark(ymd, "done")
            return
        # not archived yet: the last two days are in Current as five-minute files
        n = self._ingest_current(ymd, progress)
        self._mark(ymd, "partial" if n else "missing")

    def _ingest_current(self, ymd: str, progress: Progress) -> int:
        try:
            listing = http_get(CURRENT, timeout=60.0).text
        except Exception as e:  # noqa: BLE001
            log.info("AEMO current listing failed: %s", e)
            return 0
        files = sorted(set(re.findall(r"(PUBLIC_FPP_RUN_%s\d{4}_\d+\.zip)" % ymd, listing)))
        texts = []
        for i, name in enumerate(files):
            progress(i / max(1, len(files)), f"AEMO {ymd}: {i + 1} of {len(files)} five-minute reports")
            r = http_get(CURRENT + name, ok_404=True, timeout=60.0)
            if r is not None:
                texts.extend(_reports_in_zip(r.content))
        return self._ingest(texts) if texts else 0

    def _day(self, day0: int, progress: Progress) -> np.ndarray | None:
        cache = self.caches[self.info.id]
        if day0 + DAY <= START or day0 > time.time():
            return None
        # a UTC day spans NEM dates D (10:00-24:00 AEST) and D+1 (00:00-10:00 AEST)
        d = dt.datetime.fromtimestamp(day0, dt.timezone.utc).date()
        for nem in (d, d + dt.timedelta(days=1)):
            self._nem_day(nem, progress)
        arr = cache.get(day0)
        return None if arr is None else clean(arr, 50.0).astype(np.float32)

    def load(self, t0_utc: float, t1_utc: float, progress: Progress) -> Series:
        days = days_between(t0_utc, t1_utc)
        out, missing = {}, 0
        for i, d in enumerate(days):
            progress(i / len(days), f"AEMO frequency, day {i + 1} of {len(days)}")
            arr = self._day(d, lambda f, m, i=i: progress((i + f) / len(days), m))
            if arr is None or not np.isfinite(arr).any():
                missing += 1
            else:
                out[d] = arr
        s = assemble(out, t0_utc, t1_utc)
        s.notes.append("AEMO publishes frequency every 4 s; it was interpolated to 1 s.")
        if missing:
            s.notes.append(f"AEMO has no data for {missing} of the {len(days)} days (the archive keeps about the "
                           "last 12 months and starts with FPP in June 2025).")
        return s
