"""Great Britain: NESO "System Frequency" 1 s data (one file per month since January 2014).

Catalogue: CKAN ``package_show?id=system-frequency-data`` on ``api.neso.energy``. Each monthly
resource is a CSV (or a zipped CSV) with columns ``dtm`` (date-time) and ``f`` (Hz), and most are
also loaded into the CKAN datastore, which can be sliced with SQL. Licence: NESO Open Data
Licence (free re-use with attribution).

The archive is not consistent about time, which this module checks for each month:

* Some files carry explicit offsets (``2019-08-09 16:52:38 +0100``): parsed exactly.
* Most files have naive timestamps. The 2014-2018 summer files and the April-October 2025 files
  are on UK summer time (UTC+1) for the whole month; the rest are UTC. For months since mid-2021
  the offset is measured by cross-correlating an hour of the file against Elexon's FREQ data
  (stamped in UTC); before that it is inferred from the month's first time stamp (``01:00:00``
  on day 1 means the file is on UTC+1).
* The datastore copy of some months has mangled dates (March 2019 was loaded with day and month
  swapped). A month whose datastore minimum/maximum time falls outside the month is read from
  the original file instead.

Stretches where the meter froze (the same value for 30 s or more) are blanked.
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
import pandas as pd

from . import elexon
from .common import (DAY, DayCache, Progress, RefInfo, Series, SourceUnavailable, assemble, clean, days_between,
                     download, http_get, log, samples_to_day)

API = "https://api.neso.energy/api/3/action"
PACKAGE = "system-frequency-data"
FIRST_DAY = dt.datetime(2014, 1, 1, tzinfo=dt.timezone.utc).timestamp()
ELEXON_FROM = (2021, 6)          # months from here on are calibrated against Elexon
EXPLICIT = re.compile(r"[+-]\d{2}:?\d{2}$")

INFO = RefInfo(
    id="gb-neso", name="Great Britain (NESO, 1 s)", grid="Great Britain", nominal_hz=50, source="builtin",
    resolution_s=1.0, coverage=None, licence="NESO Open Data Licence",
    url="https://www.neso.energy/data-portal/system-frequency-data", available_from=FIRST_DAY,
    available_note="January 2014 to the end of the last published month (files appear about a month late)")

Month = tuple[int, int]


def _month_key(t_utc: float) -> Month:
    d = dt.datetime.fromtimestamp(t_utc, dt.timezone.utc)
    return d.year, d.month


def _month_bounds(key: Month) -> tuple[float, float]:
    y, m = key
    a = dt.datetime(y, m, 1, tzinfo=dt.timezone.utc)
    b = dt.datetime(y + (m == 12), m % 12 + 1, 1, tzinfo=dt.timezone.utc)
    return a.timestamp(), b.timestamp()


def _label(t: float) -> str:
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _to_unix(s: pd.Series) -> np.ndarray:
    return s.dt.as_unit("ns").astype("int64").to_numpy() / 1e9


def parse_time(s: str) -> float | None:
    """A NESO time stamp as unix seconds. Explicit offsets are honoured; naive stamps are read as
    if they were UTC (the caller corrects the month's offset)."""
    s = str(s).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S %z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S%z"):
        try:
            return dt.datetime.strptime(s, fmt).timestamp()
        except ValueError:
            pass
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M:%S"):
        try:
            return dt.datetime.strptime(s, fmt).replace(tzinfo=dt.timezone.utc).timestamp()
        except ValueError:
            pass
    return None


class NesoSource:
    info = INFO

    def __init__(self, cache_root: Path):
        self.root = cache_root / INFO.id
        self.cache = DayCache(self.root)

    # ---- catalogue ------------------------------------------------------------------------------
    def catalog(self, max_age_s: float = 12 * 3600) -> dict[Month, dict]:
        p = self.root / "catalog.json"
        doc = _read_json(p)
        if doc is None or time.time() - doc.get("fetched_at", 0) > max_age_s:
            try:
                r = http_get(f"{API}/package_show", params={"id": PACKAGE}, timeout=60.0)
                months = {}
                for x in r.json()["result"]["resources"]:
                    m = re.search(r"f(?:new)?[- _](\d{4})[- _](\d{1,2})\.(csv|zip)$", x.get("url", ""))
                    if m:
                        months[f"{m.group(1)}-{int(m.group(2))}"] = {
                            "id": x["id"], "url": x["url"], "datastore": bool(x.get("datastore_active"))}
                doc = {"fetched_at": time.time(), "months": months}
                _write_json(p, doc)
            except Exception as e:  # noqa: BLE001 - keep using an old catalogue if the portal is down
                if doc is None:
                    raise
                log.warning("NESO catalogue refresh failed (%s); using the cached one", e)
        return {tuple(int(v) for v in k.split("-")): v for k, v in doc["months"].items()}

    # ---- how to read a month ------------------------------------------------------------------------
    def _checks_path(self) -> Path:
        return self.root / "months.json"

    def _save_check(self, rid: str, info: dict) -> None:
        d = _read_json(self._checks_path()) or {}
        d[rid] = info
        _write_json(self._checks_path(), d)

    def _sql(self, sql: str) -> dict:
        doc = http_get(f"{API}/datastore_search_sql", params={"sql": sql}, timeout=180.0).json()
        if not doc.get("success"):
            raise RuntimeError(f"NESO datastore query failed: {doc.get('error')}")
        return doc["result"]

    def month_info(self, key: Month, res: dict, progress: Progress) -> dict:
        """{"mode": "sql"|"file", "labels": "explicit"|"naive", "offset_s": labels minus UTC}."""
        known = (_read_json(self._checks_path()) or {}).get(res["id"])
        if known:
            return known
        info = None
        if res.get("datastore"):
            try:
                info = self._check_datastore(key, res)
            except Exception as e:  # noqa: BLE001
                log.info("NESO %s: datastore check failed (%s); reading the file", key, e)
        if info is None:
            info = self._load_file(key, res, progress)
        self._save_check(res["id"], info)
        return info

    def _check_datastore(self, key: Month, res: dict) -> dict | None:
        out = self._sql(f'SELECT min("dtm") AS a, max("dtm") AS b FROM "{res["id"]}"')
        lo, hi = str(out["records"][0]["a"]), str(out["records"][0]["b"])
        t_lo, t_hi = parse_time(lo), parse_time(hi)
        a, b = _month_bounds(key)
        if t_lo is None or t_hi is None or not (a - 2 * DAY <= t_lo and t_hi <= b + 2 * DAY):
            log.info("NESO %s: datastore times look wrong (%s .. %s); reading the file", key, lo, hi)
            return None
        if EXPLICIT.search(lo):
            return {"mode": "sql", "labels": "explicit", "offset_s": 0, "method": "explicit"}
        return {"mode": "sql", "labels": "naive", **self._naive_offset(key, lo, self._sql_rows_fn(res))}

    def _sql_rows_fn(self, res: dict):
        def rows(t_lo_label: float, t_hi_label: float) -> tuple[np.ndarray, np.ndarray]:
            recs = self._sql(f'SELECT "dtm","f" FROM "{res["id"]}" WHERE "dtm" >= \'{_label(t_lo_label)}\' '
                             f'AND "dtm" < \'{_label(t_hi_label)}\' ORDER BY "dtm"')["records"]
            t = np.array([parse_time(r["dtm"]) for r in recs], dtype=float)
            f = np.array([float(r["f"]) for r in recs], dtype=float)
            return t, f
        return rows

    def _naive_offset(self, key: Month, first_label: str | None, rows) -> dict:
        """Offset of naive labels against UTC: from Elexon where it overlaps, else the first label."""
        if key >= ELEXON_FROM:
            try:
                off = _calibrate_against_elexon(key, rows)
                if off is not None:
                    return {"offset_s": off, "method": "elexon"}
            except Exception as e:  # noqa: BLE001
                log.info("NESO %s: Elexon calibration failed: %s", key, e)
        hour = int(re.search(r"[ T](\d{2}):", first_label).group(1)) if first_label else 0
        return {"offset_s": 3600 if hour == 1 else 0, "method": "first-label"}

    # ---- whole-month files -----------------------------------------------------------------------------
    def _file_array(self, rid: str) -> Path:
        return self.root / "files" / f"{rid}.npz"

    def _load_file(self, key: Month, res: dict, progress: Progress) -> dict:
        """Download and parse a month; keep it as a UTC 1 s array. Returns its check record."""
        tmp = self.root / "tmp" / res["url"].rsplit("/", 1)[-1]
        download(res["url"], tmp, progress, label=f"NESO {key[0]}-{key[1]:02d}")
        try:
            frame = read_month_file(tmp)
        finally:
            tmp.unlink(missing_ok=True)
        tl, fl = frame["t_label"].to_numpy(), frame["f"].to_numpy(float)
        if frame.attrs.get("explicit"):
            info = {"mode": "file", "labels": "explicit", "offset_s": 0, "method": "explicit"}
        else:
            def rows(a, b):
                sel = (tl >= a) & (tl < b)
                return tl[sel], fl[sel]
            info = {"mode": "file", "labels": "naive",
                    **self._naive_offset(key, _label(tl[0]) if len(tl) else None, rows)}
        t = tl - info["offset_s"]
        t0 = int(np.floor(t.min()))
        n = int(np.ceil(t.max())) - t0 + 1
        arr = np.full(n, np.nan, np.float32)
        idx = np.rint(t - t0).astype(int)
        arr[idx] = fl
        p = self._file_array(res["id"])
        p.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(p, t0=t0, f=clean(arr, 50.0).astype(np.float32))
        return info

    def _day_from_file(self, day0: int, res: dict) -> np.ndarray:
        p = self._file_array(res["id"])
        out = np.full(DAY, np.nan)
        if not p.exists():
            return out
        with np.load(p) as z:
            t0, f = int(z["t0"]), z["f"]
        lo, hi = max(day0, t0), min(day0 + DAY, t0 + len(f))
        if hi > lo:
            out[lo - day0:hi - day0] = f[lo - t0:hi - t0]
        return out

    def _day_from_sql(self, day0: int, res: dict, info: dict) -> np.ndarray:
        off = info.get("offset_s", 0)
        rows = self._sql_rows_fn(res)
        t_all, f_all = [], []
        for h in (0, 12):
            a, b = day0 + h * 3600, day0 + (h + 12) * 3600
            if info["labels"] == "explicit":
                # labels are local time with offsets: ask for a little more and filter by true time
                recs = self._sql(f'SELECT "dtm","f" FROM "{res["id"]}" WHERE "dtm" >= \'{_label(a - 3 * 3600)}\' '
                                 f'AND "dtm" < \'{_label(b + 3 * 3600)}\' ORDER BY "dtm"')["records"]
                t = np.array([parse_time(r["dtm"]) for r in recs], dtype=float)
                f = np.array([float(r["f"]) for r in recs], dtype=float)
                keep = (t >= a) & (t < b)
                t, f = t[keep], f[keep]
            else:
                t, f = rows(a + off, b + off)
                t = t - off
            t_all.append(t)
            f_all.append(f)
        return samples_to_day(np.concatenate(t_all), np.concatenate(f_all), day0)

    # ---- days ----------------------------------------------------------------------------------------------
    def _day(self, day0: int, cat: dict, progress: Progress) -> np.ndarray | None:
        cached = self.cache.get(day0)
        if cached is not None:
            return cached
        own, nxt = _month_key(day0), _month_key(day0 + DAY)
        if own not in cat:
            return None
        keys = [own] + ([nxt] if nxt != own and nxt in cat else [])
        acc = np.full(DAY, np.nan)
        for key in keys:
            res = cat[key]
            info = self.month_info(key, res, progress)
            arr = None
            if info["mode"] == "sql":
                try:
                    arr = self._day_from_sql(day0, res, info)
                except Exception as e:  # noqa: BLE001
                    log.warning("NESO %s: datastore query failed (%s); reading the file", key, e)
                    info = self._load_file(key, res, progress)
                    self._save_check(res["id"], info)
            if arr is None:
                if not self._file_array(res["id"]).exists():
                    info = self._load_file(key, res, progress)
                    self._save_check(res["id"], info)
                arr = self._day_from_file(day0, res)
            acc = np.where(np.isfinite(acc), acc, arr)
        acc = clean(acc, 50.0).astype(np.float32)
        complete = nxt == own or nxt in cat           # a UTC+1 file of the next month may hold the last hour
        if complete:
            self.cache.put(day0, acc)
        return acc

    def load(self, t0_utc: float, t1_utc: float, progress: Progress) -> Series:
        cat = self.catalog()
        if not cat:
            raise SourceUnavailable("the NESO catalogue is empty")
        last = max(cat)
        days = days_between(t0_utc, t1_utc)
        out, missing = {}, 0
        for i, d in enumerate(days):
            progress(i / len(days), f"NESO frequency, day {i + 1} of {len(days)}")
            if d + DAY <= FIRST_DAY:
                missing += 1
                continue
            arr = self._day(d, cat, lambda f, m, i=i: progress((i + f) / len(days), m))
            if arr is None:
                missing += 1
            else:
                out[d] = arr
        s = assemble(out, t0_utc, t1_utc)
        if missing:
            s.notes.append(f"NESO has no data for {missing} of the {len(days)} days (files run from January 2014 "
                           f"to {last[0]}-{last[1]:02d}). For recent days use 'Great Britain (Elexon BMRS, 15 s)'.")
        return s


def _calibrate_against_elexon(key: Month, rows) -> int | None:
    """Whole hours to subtract from the month's labels to get UTC, by correlation with Elexon."""
    a, b = _month_bounds(key)
    for day in (14, 7, 21):
        t_mid = a + (day - 1) * DAY + 12 * 3600
        if t_mid >= b:
            continue
        te, fe = elexon.fetch_samples(t_mid, t_mid + 3600)
        if len(te) < 100:
            continue
        tl, fl = rows(t_mid - 2 * 3600, t_mid + 3 * 3600)
        ok = np.isfinite(tl) & np.isfinite(fl)
        tl, fl = tl[ok], fl[ok]
        if len(tl) < 3600:
            continue
        best_c, best_lag = -2.0, 0
        for hours in (-1, 0, 1):
            for s in range(-3, 4):
                lag = hours * 3600 + s
                c = np.corrcoef(np.interp(te + lag, tl, fl), fe)[0, 1]
                if np.isfinite(c) and c > best_c:
                    best_c, best_lag = float(c), lag
        if best_c >= 0.9:
            return int(round(best_lag / 3600.0)) * 3600
    return None


def read_month_file(path: Path) -> pd.DataFrame:
    """A NESO monthly file as a frame with ``t_label`` (unix s of each label; true UTC when the file
    has explicit offsets, see ``attrs['explicit']``) and ``f`` (Hz), sorted by time."""
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
            raw = z.read(name)
    else:
        raw = path.read_bytes()
    df = pd.read_csv(io.BytesIO(raw), dtype=str)
    df.columns = [c.strip().lower() for c in df.columns]
    tcol = next(c for c in df.columns if c in ("dtm", "date", "datetime", "time"))
    fcol = next(c for c in df.columns if c in ("f", "frequency", "freq"))
    s = df[tcol].str.strip()
    sample = s.iloc[0]
    explicit = bool(EXPLICIT.search(sample))
    if explicit:
        t = pd.to_datetime(s, utc=True, format="mixed")
    elif re.match(r"\d{2}/\d{2}/\d{4}", sample):
        t = pd.to_datetime(s, format="%d/%m/%Y %H:%M:%S", utc=True)
    else:
        t = pd.to_datetime(s.str.replace("T", " ", regex=False), format="%Y-%m-%d %H:%M:%S", utc=True)
    out = pd.DataFrame({"t_label": _to_unix(t), "f": pd.to_numeric(df[fcol], errors="coerce").to_numpy()})
    out = out[np.isfinite(out["f"])].sort_values("t_label").reset_index(drop=True)
    out.attrs["explicit"] = explicit
    return out


def _read_json(p: Path) -> dict | None:
    try:
        return json.loads(p.read_text()) if p.exists() else None
    except (OSError, json.JSONDecodeError):
        return None


def _write_json(p: Path, doc: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(doc, indent=1))
    tmp.replace(p)
