"""USGS ComCat events: quarry and mining blasts, explosions, sonic booms, earthquakes.

Seismic networks catalogue these with origin times (to fractions of a second) and locations,
which makes them known-time impulsive events for the same detector and solver as lightning
(see ``events`` for how each kind's sound travels). Blast coverage is mostly the US.

Data: the FDSN event web service ``https://earthquake.usgs.gov/fdsnws/event/1/query`` (free,
no account). ``format=csv`` is used rather than GeoJSON because only the CSV carries the
per-event ``horizontalError`` (km; checked 2026-11-04: the GeoJSON summary has no error
fields). Event types come in several spellings (``quarry blast``, ``quarry_blast``,
``quarry``), so friendly names expand to all of them. Results are paged with ``limit`` and
``offset`` (the service caps one response at 20 000 events).
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import math
from typing import Callable

from .catalogue import assign_ids, make_strike
from .events import canonical_kind
from .geo import in_bbox

ProgressFn = Callable[[float, str | None], None]

URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"
SOURCE = "usgs"
DEFAULT_SIGMA_M = 1000.0         # when ComCat gives no horizontal error
MIN_SIGMA_M = 100.0
PAGE = 20000
MAX_EVENTS = 100_000

# friendly name -> ComCat spellings
TYPE_GROUPS = {
    "quarry blast": ["quarry blast", "quarry_blast", "quarry"],
    "mining explosion": ["mining explosion", "mining_explosion"],
    "explosion": ["explosion", "chemical explosion", "chemical_explosion", "experimental explosion",
                  "industrial explosion", "accidental explosion", "nuclear explosion", "nuclear_explosion"],
    "sonic boom": ["sonic boom", "sonic_boom", "sonicboom"],
    "earthquake": ["earthquake", "eq"],
    "meteor": ["meteor", "meteorite"],
}
DEFAULT_TYPES = ("quarry blast", "mining explosion", "explosion", "sonic boom")
# every other ComCat type is accepted by name too (from /application.json, November 2026)
OTHER_TYPES = {"chemical explosion", "experimental explosion", "industrial explosion", "accidental explosion",
               "nuclear explosion", "induced or triggered event", "ice quake", "rock burst", "mine collapse",
               "collapse", "building collapse", "landslide", "rock slide", "debris avalanche", "snow avalanche",
               "volcanic eruption", "volcanic explosion", "train crash", "acoustic noise", "other event"}


def expand_types(types: list[str] | None) -> list[str]:
    """ComCat ``eventtype`` values for friendly type names (default: blasts, explosions, booms)."""
    names = [str(t).strip().lower() for t in (types or DEFAULT_TYPES) if str(t).strip()]
    if not names:
        names = list(DEFAULT_TYPES)
    out: list[str] = []
    for n in names:
        group = TYPE_GROUPS.get(n) or TYPE_GROUPS.get(canonical_kind(n) or "")
        if group is None:
            k = canonical_kind(n)
            if k not in OTHER_TYPES:
                raise ValueError(f"unknown USGS event type {n!r} (use {', '.join(TYPE_GROUPS)} or another ComCat type)")
            group = [k, k.replace(" ", "_")]
        out += [g for g in group if g not in out]
    return out


def _iso(t: float) -> str:
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]


def query_params(bbox: dict, t0: float, t1: float, eventtypes: list[str], offset: int = 1) -> dict:
    return {"format": "csv", "starttime": _iso(t0), "endtime": _iso(t1),
            "minlatitude": f"{max(-90.0, bbox['lat_min']):.5f}", "maxlatitude": f"{min(90.0, bbox['lat_max']):.5f}",
            "minlongitude": f"{max(-360.0, bbox['lon_min']):.5f}", "maxlongitude": f"{min(360.0, bbox['lon_max']):.5f}",
            "eventtype": ",".join(eventtypes), "orderby": "time-asc", "limit": str(PAGE), "offset": str(offset)}


def _num(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def parse_events(text: str) -> list[dict]:
    """Strike dicts from a ComCat CSV response (ids not yet assigned)."""
    out = []
    for row in csv.DictReader(io.StringIO(text)):
        lat, lon = _num(row.get("latitude")), _num(row.get("longitude"))
        try:
            t = dt.datetime.fromisoformat(str(row.get("time", "")).replace("Z", "+00:00")).timestamp()
        except ValueError:
            continue
        if lat is None or lon is None:
            continue
        herr = _num(row.get("horizontalError"))
        sigma = max(MIN_SIGMA_M, herr * 1000.0) if herr is not None and herr > 0 else DEFAULT_SIGMA_M
        depth_km = _num(row.get("depth"))
        out.append(make_strike(t, lat, lon, sigma, _num(row.get("mag")), canonical_kind(row.get("type")), SOURCE,
                               depth_m=None if depth_km is None else depth_km * 1000.0, ref=row.get("id") or None))
    return out


def fetch_events(bbox: dict, t0_utc: float, t1_utc: float, types: list[str] | None = None, http=None,
                 progress: ProgressFn | None = None) -> tuple[list[dict], dict]:
    """ComCat events of the given types in bbox between t0 and t1, as Strike dicts, plus info."""
    if t1_utc <= t0_utc:
        raise ValueError("t1_utc must be after t0_utc")
    from .glm import _Http
    http = http or _Http()
    eventtypes = expand_types(types)
    report = progress or (lambda f, m=None: None)
    events: list[dict] = []
    offset = 1
    while True:
        report(0.1, f"Querying USGS ComCat ({len(events)} events so far)")
        r = http.get(URL, params=query_params(bbox, t0_utc, t1_utc, eventtypes, offset), timeout=90)
        if r.status_code == 204:
            break
        if r.status_code != 200:
            detail = (getattr(r, "text", "") or "").strip().splitlines()
            raise ValueError(f"USGS ComCat refused the query (HTTP {r.status_code}): {' '.join(detail[:3])[:300]}")
        page = parse_events(r.text)
        events += page
        if len(page) < PAGE or len(events) >= MAX_EVENTS:
            break
        offset += PAGE
    keep = [e for e in events if in_bbox(e["lat"], e["lon"], bbox) and t0_utc <= e["t_utc"] <= t1_utc]
    kinds: dict[str, int] = {}
    for e in keep:
        kinds[e["kind"] or "unknown"] = kinds.get(e["kind"] or "unknown", 0) + 1
    report(1.0, f"{len(keep)} USGS events")
    return assign_ids(keep), {"n_events": len(keep), "kinds": kinds, "eventtypes": eventtypes,
                              "truncated": len(events) >= MAX_EVENTS}
