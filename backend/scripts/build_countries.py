"""Build src/tracksound/data/countries.geojson from Natural Earth (public domain).

Usage: uv run python scripts/build_countries.py [path/to/ne_10m_admin_0_countries.geojson]

Without a path it downloads Natural Earth 1:10m admin-0 countries. Rings are simplified
(Douglas-Peucker, 0.004 degrees, about 400 m) and rounded to 3 decimals to keep the file
small. 1:10m matters: at 1:50m, estuaries such as Lisbon's cut kilometres inland.
Each feature gets: code (ISO 3166-1 alpha-2, or Natural Earth's ADM0_A3 where there is none),
iso3, name, sovereign, type, and mains_hz from the bundled countries_mains.geojson.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import requests

URL = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_admin_0_countries.geojson"
TOLERANCE_DEG = 0.004
DATA = Path(__file__).resolve().parents[1] / "src" / "tracksound" / "data"


# Small countries and territories missing from the 1:110m mains table. Values from public
# "mains electricity by country" lists (60 Hz: US territories and most of the Caribbean that
# follows US practice; 50 Hz elsewhere). None where unknown or uninhabited.
MAINS_EXTRA = {
    "American Samoa": 60, "Guam": 60, "N. Mariana Is.": 60, "Micronesia": 60, "Palau": 60, "Marshall Is.": 60,
    "U.S. Virgin Is.": 60, "Antigua and Barb.": 60, "Anguilla": 60, "Aruba": 60, "Bermuda": 60, "British Virgin Is.": 60,
    "Cayman Is.": 60, "Montserrat": 60, "St. Kitts and Nevis": 60, "Turks and Caicos Is.": 60, "Sint Maarten": 60,
    "St-Martin": 60, "St-Barthélemy": 60, "Fr. Polynesia": 60,
    "Andorra": 50, "Bahrain": 50, "Barbados": 50, "Cabo Verde": 50, "Comoros": 50, "Cook Is.": 50, "Curaçao": 50,
    "Dominica": 50, "Faeroe Is.": 50, "Grenada": 50, "Guernsey": 50, "Hong Kong": 50, "Isle of Man": 50, "Jersey": 50,
    "Kiribati": 50, "Liechtenstein": 50, "Macao": 50, "Maldives": 50, "Malta": 50, "Mauritius": 50, "Monaco": 50,
    "Nauru": 50, "Niue": 50, "Norfolk Island": 50, "Pitcairn Is.": 50, "Saint Helena": 50, "Saint Lucia": 50,
    "St. Pierre and Miquelon": 50, "St. Vin. and Gren.": 50, "Samoa": 50, "San Marino": 50, "Seychelles": 50,
    "Singapore": 50, "São Tomé and Principe": 50, "Tonga": 50, "Tuvalu": 50, "Vatican": 50,
    "Wallis and Futuna Is.": 50, "Åland": 50, "Indian Ocean Ter.": 50,
}


def _simplify(ring: list, tol: float) -> list:
    """Douglas-Peucker on one closed ring (iterative), keeping at least 4 points."""
    import numpy as np
    pts = np.asarray(ring, float)
    if len(pts) <= 8:
        return ring
    keep = np.zeros(len(pts), bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        seg = pts[b] - pts[a]
        rel = pts[a + 1:b] - pts[a]
        norm = np.hypot(*seg)
        d = np.abs(seg[0] * rel[:, 1] - seg[1] * rel[:, 0]) / norm if norm > 0 else np.hypot(rel[:, 0], rel[:, 1])
        i = int(np.argmax(d))
        if d[i] > tol:
            keep[a + 1 + i] = True
            stack += [(a, a + 1 + i), (a + 1 + i, b)]
    out = pts[keep]
    return out.tolist() if len(out) >= 4 else ring


def _simplify_geom(coords, tol: float):
    if isinstance(coords[0][0], (int, float)):          # a ring
        return _simplify(coords, tol)
    return [_simplify_geom(c, tol) for c in coords]


def _round(coords, nd=3):
    if isinstance(coords[0], (int, float)):
        return [round(coords[0], nd), round(coords[1], nd)]
    out = [_round(c, nd) for c in coords]
    if out and isinstance(out[0][0], (int, float)):        # a ring: drop repeated points after rounding
        ring = [out[0]] + [p for i, p in enumerate(out[1:], 1) if p != out[i - 1]]
        return ring if len(ring) >= 4 else out
    return out


def main():
    src = json.loads(Path(sys.argv[1]).read_text()) if len(sys.argv) > 1 else requests.get(URL, timeout=120).json()
    mains = {}
    mp = DATA / "countries_mains.geojson"
    if mp.exists():
        for f in json.loads(mp.read_text())["features"]:
            mains[f["properties"].get("iso_a3")] = f["properties"].get("mains_hz")
    # Codes: a real ISO_A2 always wins. ISO_A2_EH fills in where ISO_A2 is "-99" (France, Norway,
    # Kosovo...) unless another feature already owns that code (Natural Earth gives leases and
    # dependencies their sovereign's EH code). Everything else falls back to ADM0_A3.
    real = {f["properties"]["ISO_A2"] for f in src["features"] if f["properties"].get("ISO_A2") not in (None, "-99")}
    used: set[str] = set()
    feats = []
    for f in src["features"]:
        p = f["properties"]
        a2, eh = p.get("ISO_A2"), p.get("ISO_A2_EH")
        if a2 not in (None, "-99"):
            code = a2
        elif eh not in (None, "-99") and eh not in real and eh not in used:
            code = eh
        else:
            code = p["ADM0_A3"]
        used.add(code)
        iso3 = p.get("ISO_A3") if p.get("ISO_A3") not in (None, "-99") else p["ADM0_A3"]
        feats.append({"type": "Feature", "properties": {
            "code": code, "iso3": iso3, "name": p.get("NAME_LONG") or p["NAME"], "short_name": p["NAME"],
            "sovereign": p.get("SOVEREIGNT"), "type": p.get("TYPE"), "continent": p.get("CONTINENT"),
            "mains_hz": mains.get(iso3, mains.get(p["ADM0_A3"], MAINS_EXTRA.get(p["NAME"])))},
            "geometry": {"type": f["geometry"]["type"],
                         "coordinates": _round(_simplify_geom(f["geometry"]["coordinates"], TOLERANCE_DEG))}})
    feats.sort(key=lambda f: f["properties"]["name"])
    codes = [f["properties"]["code"] for f in feats]
    dup = {c for c in codes if codes.count(c) > 1}
    if dup:
        raise SystemExit(f"duplicate codes: {dup}")
    import gzip
    out = DATA / "countries.geojson.gz"
    out.write_bytes(gzip.compress(json.dumps({"type": "FeatureCollection", "source": "Natural Earth 1:10m admin-0 countries (public domain), "
                               f"simplified to {TOLERANCE_DEG} degrees",
                               "features": feats}, separators=(",", ":")).encode(), compresslevel=9, mtime=0))
    print(f"wrote {out} ({out.stat().st_size / 1e6:.2f} MB, {len(feats)} countries)")


if __name__ == "__main__":
    main()
