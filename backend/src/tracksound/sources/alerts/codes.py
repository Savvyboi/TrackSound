"""SAME code tables: originators, events and location codes.

- Originators: 47 CFR 11.31(d)(1) (the only four allowed codes).
- Events: 47 CFR 11.31(e) as amended to 2026, plus the NWS-only codes of NWS Instruction
  10-1712 Appendix A.4 (Snow Squall Warning and the NWR transmitter-control codes).
- Locations (``PSSCCC``): P is the part of the county (11.31(c)), SSCCC a county FIPS code
  (CCC = 000 for a whole state), or an NWS marine zone. Names and centroids come from the
  bundled tables in ``data/`` (US Census Gazetteer and NWS files, see data/README.md).
"""
from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"

ORIGINATORS: dict[str, str] = {
    "EAS": "EAS Participant (broadcast station or cable system)",
    "CIV": "Civil authorities",
    "WXR": "National Weather Service",
    "PEP": "United States Government (Primary Entry Point System)",
}

EVENTS: dict[str, str] = {
    # national codes (required)
    "EAN": "Emergency Action Notification", "NIC": "National Information Center", "NPT": "National Periodic Test",
    "RMT": "Required Monthly Test", "RWT": "Required Weekly Test",
    # state and local codes (optional)
    "ADR": "Administrative Message", "AVW": "Avalanche Warning", "AVA": "Avalanche Watch", "BZW": "Blizzard Warning",
    "BLU": "Blue Alert", "CAE": "Child Abduction Emergency", "CDW": "Civil Danger Warning",
    "CEM": "Civil Emergency Message", "CFW": "Coastal Flood Warning", "CFA": "Coastal Flood Watch",
    "DSW": "Dust Storm Warning", "EQW": "Earthquake Warning", "EVI": "Evacuation Immediate",
    "EWW": "Extreme Wind Warning", "FRW": "Fire Warning", "FFW": "Flash Flood Warning", "FFA": "Flash Flood Watch",
    "FFS": "Flash Flood Statement", "FLW": "Flood Warning", "FLA": "Flood Watch", "FLS": "Flood Statement",
    "HMW": "Hazardous Materials Warning", "HWW": "High Wind Warning", "HWA": "High Wind Watch",
    "HUW": "Hurricane Warning", "HUA": "Hurricane Watch", "HLS": "Hurricane Statement",
    "LEW": "Law Enforcement Warning", "LAE": "Local Area Emergency", "MEP": "Missing and Endangered Persons",
    "NMN": "Network Message Notification", "TOE": "911 Telephone Outage Emergency",
    "NUW": "Nuclear Power Plant Warning", "DMO": "Practice/Demo Warning", "RHW": "Radiological Hazard Warning",
    "SVR": "Severe Thunderstorm Warning", "SVA": "Severe Thunderstorm Watch", "SVS": "Severe Weather Statement",
    "SPW": "Shelter in Place Warning", "SMW": "Special Marine Warning", "SPS": "Special Weather Statement",
    "SSA": "Storm Surge Watch", "SSW": "Storm Surge Warning", "TOR": "Tornado Warning", "TOA": "Tornado Watch",
    "TRW": "Tropical Storm Warning", "TRA": "Tropical Storm Watch", "TSW": "Tsunami Warning", "TSA": "Tsunami Watch",
    "VOW": "Volcano Warning", "WSW": "Winter Storm Warning", "WSA": "Winter Storm Watch",
    # NOAA Weather Radio only (NWS 10-1712 A.4.1 note 4 and A.4.3)
    "SQW": "Snow Squall Warning", "TXB": "Transmitter Backup On", "TXF": "Transmitter Carrier Off",
    "TXO": "Transmitter Carrier On", "TXP": "Transmitter Primary On",
}

COUNTY_PARTS = {"0": "all", "1": "northwest", "2": "north", "3": "northeast", "4": "west", "5": "central",
                "6": "east", "7": "southwest", "8": "south", "9": "southeast"}

# 47 CFR 11.31(f): marine areas use NWS numbers, not ANSI state codes
MARINE_AREAS = frozenset({"57", "58", "59", "61", "65", "73", "75", "77", "91", "92", "93", "94", "96", "97", "98"})
DEMO_LOCATION = "999000"   # NWS 10-1712 A.3.7: matches no real area


def originator_name(code: str) -> str:
    return ORIGINATORS.get(code, f"Unknown originator {code}")


def event_name(code: str) -> str:
    return EVENTS.get(code, f"Unknown event {code}")


def _num(v: str) -> float | None:
    return float(v) if v.strip() else None


@lru_cache(maxsize=1)
def counties() -> dict[str, dict]:
    """SSCCC -> {state, name, lat, lon, src} from data/us_counties.csv (src "state" for CCC = 000)."""
    with open(DATA / "us_counties.csv", encoding="utf-8", newline="") as fh:
        return {r["fips"]: {"state": r["state"], "name": r["name"], "lat": _num(r["lat"]), "lon": _num(r["lon"]),
                            "src": r["src"]} for r in csv.DictReader(fh)}


@lru_cache(maxsize=1)
def marine_zones() -> dict[str, dict]:
    """SSCCC -> {area, name, lat, lon} from data/marine_zones.csv."""
    with open(DATA / "marine_zones.csv", encoding="utf-8", newline="") as fh:
        return {r["code"]: {"area": r["area"], "name": r["name"], "lat": _num(r["lat"]), "lon": _num(r["lon"])}
                for r in csv.DictReader(fh)}


@lru_cache(maxsize=1)
def state_codes() -> frozenset[str]:
    """Every valid SS: the ANSI state and territory codes, 00 (all US) and the marine areas."""
    return frozenset({k[:2] for k in counties()} | MARINE_AREAS)


@lru_cache(maxsize=1)
def known_location_codes() -> tuple[str, ...]:
    """Every SSCCC in the bundled tables (counties, state-wide codes, marine zones)."""
    return tuple(sorted(set(counties()) | set(marine_zones())))


def describe_location(code: str) -> dict:
    """A SameLocation (docs/API.md) for a 6-character PSSCCC code."""
    part, fips = code[:1], code[1:]
    loc = {"code": code, "part": COUNTY_PARTS.get(part, part), "fips": fips, "state": None, "county": None,
           "lat": None, "lon": None}
    if code == DEMO_LOCATION:
        loc["county"] = "Demonstration code (no real area)"
        return loc
    if fips[:2] in MARINE_AREAS:
        z = marine_zones().get(fips)
        if z:
            label = z["name"] if fips.endswith("000") else f"{z['name']} ({z['area']}Z{fips[2:]})"
            loc.update(county=label, lat=z["lat"], lon=z["lon"])
        return loc
    c = counties().get(fips)
    if c is None:
        return loc
    if fips == "00000":
        loc["county"] = "All of the United States"
    elif fips.endswith("000"):
        loc.update(state=c["state"], county="Entire state", lat=c["lat"], lon=c["lon"])
    else:
        loc.update(state=c["state"], county=c["name"], lat=c["lat"], lon=c["lon"])
    return loc
