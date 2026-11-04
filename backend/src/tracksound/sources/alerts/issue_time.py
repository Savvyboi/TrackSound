"""The issue time of a SAME header (JJJHHMM: UTC day of year, hour, minute) as unix seconds.

The header has no year. It is taken, in this order, from:

1. the case ``date`` (the UTC day the recording was made): the year that puts the issue time
   closest to that day, so day 365 with a case date of 2 January lands in the previous year;
2. the file's creation time from its metadata (ffprobe tags such as ``creation_time``), with
   the same closest-year rule;
3. otherwise the most recent occurrence before the scan (the current year, or the previous
   one if that day has not come yet this year).
"""
from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from ... import media

CREATION_TAGS = ("creation_time", "com.apple.quicktime.creationdate", "date", "origination_date", "icrd",
                 "date_recorded", "year")


def issue_datetime(jjj: int, hh: int, mm: int, year: int) -> datetime | None:
    """UTC datetime for day-of-year ``jjj`` of ``year`` (None if the day does not exist)."""
    start = datetime(year, 1, 1, tzinfo=timezone.utc)
    t = start + timedelta(days=jjj - 1, hours=hh, minutes=mm)
    return t if t.year == year else None


def closest_year(jjj: int, hh: int, mm: int, ref: datetime) -> datetime | None:
    cands = [issue_datetime(jjj, hh, mm, y) for y in (ref.year - 1, ref.year, ref.year + 1)]
    cands = [c for c in cands if c is not None]
    return min(cands, key=lambda c: abs((c - ref).total_seconds()), default=None)


def latest_before(jjj: int, hh: int, mm: int, ref: datetime) -> datetime | None:
    """The most recent occurrence not after ``ref`` (one day of slack for clock skew)."""
    for y in (ref.year, ref.year - 1, ref.year - 2, ref.year - 3, ref.year - 4):
        t = issue_datetime(jjj, hh, mm, y)
        if t is not None and t <= ref + timedelta(days=1):
            return t
    return None


def parse_timestamp(value: str) -> datetime | None:
    """A UTC datetime from common metadata date formats (None for unset or odd values)."""
    v = value.strip()
    m = re.match(r"^(\d{4})[-:/](\d{2})[-:/](\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?(?:\.\d+)?\s*(Z|[+-]\d{2}:?\d{2})?)?", v)
    if not m:
        m2 = re.match(r"^(\d{4})$", v)
        return datetime(int(m2.group(1)), 7, 2, tzinfo=timezone.utc) if m2 and 1990 <= int(m2.group(1)) <= 2100 else None
    y, mo, d, hh, mi, ss, tz = m.groups()
    try:
        t = datetime(int(y), int(mo), int(d), int(hh or 12), int(mi or 0), int(ss or 0))
    except ValueError:
        return None
    if tz and tz != "Z":
        sign = 1 if tz[0] == "+" else -1
        off = timedelta(hours=int(tz[1:3]), minutes=int(tz[-2:]))
        t = t - sign * off
    if not 1990 <= t.year <= 2100:                     # 1904/1970 placeholders mean "unset"
        return None
    return t.replace(tzinfo=timezone.utc)


def creation_time(path: str | Path) -> str | None:
    """The file's creation time from its container or stream tags (ISO 8601 UTC), if any."""
    try:
        info = media.probe(path)
    except (OSError, ValueError, subprocess.SubprocessError, json.JSONDecodeError):
        return None
    tag_sets = [info.get("format", {}).get("tags") or {}] + [s.get("tags") or {} for s in info.get("streams", [])]
    for key in CREATION_TAGS:
        for tags in tag_sets:
            for k, v in tags.items():
                if k.lower() == key and isinstance(v, str):
                    t = parse_timestamp(v)
                    if t is not None:
                        return t.strftime("%Y-%m-%dT%H:%M:%SZ")
    return None


def resolve(jjj: int, hh: int, mm: int, case_date: str | None, created: str | None,
            scanned_at: float) -> tuple[float | None, str]:
    """(issue time in unix seconds, which reference gave the year: "case date",
    "file creation time" or "scan time")."""
    if case_date:
        try:
            ref = datetime.strptime(case_date, "%Y-%m-%d").replace(hour=12, tzinfo=timezone.utc)
            t = closest_year(jjj, hh, mm, ref)
            if t is not None:
                return t.timestamp(), "case date"
        except ValueError:
            pass
    if created:
        ref = parse_timestamp(created)
        if ref is not None:
            t = closest_year(jjj, hh, mm, ref)
            if t is not None:
                return t.timestamp(), "file creation time"
    t = latest_before(jjj, hh, mm, datetime.fromtimestamp(scanned_at, tz=timezone.utc))
    return (t.timestamp() if t else None), "scan time"
