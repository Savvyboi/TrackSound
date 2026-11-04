"""Rebuild us_counties.csv and marine_zones.csv from public-domain US government files.

    python build_tables.py [download_dir]

Sources (all US federal works, public domain):

- Census 2026 Gazetteer counties: names and internal points of current counties.
- Census 2021, 2015 and 2010 Gazetteers: counties since retired (Connecticut's 8 counties,
  old Alaska census areas, ...) that NWS still lists as SAME codes.
- Census TIGER/Line 2025 counties (only its .dbf is read, via HTTP range requests): American
  Samoa, Guam, Northern Mariana Islands and the US Virgin Islands.
- Census state codes (state.txt): state names.
- NWS SameCode.txt: every county code NWS uses in SAME; names for codes the Census lacks.
- NWS marine area and zone codes for EAS and NWR (marnwr / mareas / marst files).

State-wide codes (CCC = 000) get the land-area-weighted mean of their counties' internal
points (averaged as unit vectors, so Alaska across the antimeridian works).
"""
from __future__ import annotations

import csv
import io
import math
import struct
import sys
import tempfile
import zipfile
from pathlib import Path

import requests

GAZ = "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/"
GAZETTEERS = {   # year -> (url, member name, field separator, encoding)
    2026: (GAZ + "2026_Gazetteer/2026_Gaz_counties_national.zip", "2026_Gaz_counties_national.txt", "|", "utf-8"),
    2021: (GAZ + "2021_Gazetteer/2021_Gaz_counties_national.zip", "2021_Gaz_counties_national.txt", "\t", "utf-8"),
    2015: (GAZ + "2015_Gazetteer/2015_Gaz_counties_national.zip", "2015_Gaz_counties_national.txt", "\t", "latin-1"),
    2010: (GAZ + "Gaz_counties_national.zip", "Gaz_counties_national.txt", "\t", "latin-1"),
}
TIGER_COUNTY = "https://www2.census.gov/geo/tiger/TIGER2025/COUNTY/tl_2025_us_county.zip"
CENSUS_STATES = "https://www2.census.gov/geo/docs/reference/state.txt"
NWS_SAME_CODES = "https://www.weather.gov/source/nwr/SameCode.txt"
NWS_WSOM = "https://www.weather.gov/source/gis/Shapefiles/WSOM/"
NWS_MARINE = ["marnwr18mr25.txt", "mareas18mr25.txt"]   # zone codes for NWR, then for EAS
NWS_MARINE_AREAS = "marst14fe25.txt"

FREELY_ASSOCIATED = {"64": ("FM", "Federated States of Micronesia"), "68": ("MH", "Marshall Islands"), "70": ("PW", "Palau")}
# retired codes with no Gazetteer entry: centroid from the areas that replaced them
SUCCESSORS = {"02201": ["02198"], "02232": ["02105", "02230"], "02280": ["02195", "02275"], "51560": ["51005"]}
DEC = 3   # ~100 m: plenty for a county centroid


def fetch(url: str, dest: Path) -> Path:
    if not dest.exists():
        r = requests.get(url, timeout=120)
        r.raise_for_status()
        dest.write_bytes(r.content)
    return dest


def read_gazetteer(zip_path: Path, member: str, sep: str, encoding: str) -> dict[str, dict]:
    with zipfile.ZipFile(zip_path) as z:
        text = z.read(member).decode(encoding, errors="replace")
    lines = text.splitlines()
    head = [h.strip() for h in lines[0].split(sep)]
    out = {}
    for line in lines[1:]:
        row = dict(zip(head, (v.strip() for v in line.split(sep))))
        if row.get("GEOID"):
            out[row["GEOID"]] = {"state": row["USPS"], "name": row["NAME"], "lat": float(row["INTPTLAT"]),
                                 "lon": float(row["INTPTLONG"]), "aland": float(row["ALAND"])}
    return out


class _RangeFile(io.RawIOBase):
    """Seekable read-only view of a remote file via HTTP range requests."""

    def __init__(self, url: str):
        self.url, self.pos, self.session = url, 0, requests.Session()
        self.size = int(self.session.head(url, allow_redirects=True, timeout=60).headers["content-length"])

    def seekable(self): return True
    def readable(self): return True
    def tell(self): return self.pos

    def seek(self, offset, whence=0):
        self.pos = offset if whence == 0 else self.pos + offset if whence == 1 else self.size + offset
        return self.pos

    def readinto(self, b):
        if self.pos >= self.size or len(b) == 0:
            return 0
        end = min(self.size, self.pos + len(b)) - 1
        r = self.session.get(self.url, headers={"Range": f"bytes={self.pos}-{end}"}, timeout=120)
        r.raise_for_status()
        n = len(r.content)
        b[:n] = r.content
        self.pos += n
        return n


def read_tiger_dbf(url: str, dest: Path) -> list[dict]:
    if not dest.exists():
        with zipfile.ZipFile(io.BufferedReader(_RangeFile(url), buffer_size=1 << 20)) as z:
            dest.write_bytes(z.read(next(n for n in z.namelist() if n.endswith(".dbf"))))
    raw = dest.read_bytes()
    n_rec, hdr_len, rec_len = struct.unpack("<IHH", raw[4:12])
    fields, pos = [], 32
    while raw[pos] != 0x0D:
        fields.append((raw[pos:pos + 11].split(b"\0")[0].decode(), raw[pos + 16]))
        pos += 32
    rows = []
    for i in range(n_rec):
        rec = raw[hdr_len + i * rec_len: hdr_len + (i + 1) * rec_len]
        p, row = 1, {}
        for name, length in fields:
            row[name] = rec[p:p + length].decode("utf-8", errors="replace").strip()
            p += length
        rows.append(row)
    return rows


def mean_point(points: list[tuple[float, float, float]]) -> tuple[float, float] | None:
    """Weighted mean of (lat, lon, weight) as unit vectors."""
    x = y = z = 0.0
    for lat, lon, w in points:
        la, lo = math.radians(lat), math.radians(lon)
        x += w * math.cos(la) * math.cos(lo)
        y += w * math.cos(la) * math.sin(lo)
        z += w * math.sin(la)
    if not points or (x == y == z == 0.0):
        return None
    return math.degrees(math.atan2(z, math.hypot(x, y))), math.degrees(math.atan2(y, x))


def build_counties(work: Path) -> list[list]:
    gaz = {}
    for year, (url, member, sep, enc) in GAZETTEERS.items():
        gaz[year] = read_gazetteer(fetch(url, work / f"gaz{year}.zip"), member, sep, enc)
    current = gaz[2026]
    states = {}
    for line in fetch(CENSUS_STATES, work / "state.txt").read_text(encoding="utf-8").splitlines()[1:]:
        fp, abbr, name, _ = line.split("|")
        states[fp] = (abbr, name)
    states.update(FREELY_ASSOCIATED)
    same = {}
    for line in fetch(NWS_SAME_CODES, work / "SameCode.txt").read_text(encoding="utf-8").splitlines():
        if line.strip():
            code, rest = line.split(",", 1)
            name, abbr = rest.rsplit(",", 1)
            same[code.strip()[1:]] = (name.strip().rstrip("*"), abbr.strip())

    rows: dict[str, list] = {}
    for fips, c in current.items():
        rows[fips] = [fips, c["state"], c["name"], c["lat"], c["lon"], ""]
    for r in read_tiger_dbf(TIGER_COUNTY, work / "tl_2025_us_county.dbf"):
        if r["GEOID"] not in rows and r["STATEFP"] in states:
            rows[r["GEOID"]] = [r["GEOID"], states[r["STATEFP"]][0], r["NAMELSAD"], float(r["INTPTLAT"]),
                                float(r["INTPTLON"]), "tiger"]
            current[r["GEOID"]] = {"lat": float(r["INTPTLAT"]), "lon": float(r["INTPTLON"]), "aland": float(r["ALAND"])}
    for fips, (name, abbr) in same.items():
        if fips in rows:
            continue
        old = next((gaz[y][fips] for y in (2021, 2015, 2010) if fips in gaz[y]), None)
        if old:
            rows[fips] = [fips, abbr, old["name"], old["lat"], old["lon"], "legacy"]
        elif fips in SUCCESSORS:
            p = mean_point([(current[s]["lat"], current[s]["lon"], current[s]["aland"]) for s in SUCCESSORS[fips]])
            rows[fips] = [fips, abbr, name, p[0], p[1], "approx"]
        else:
            rows[fips] = [fips, abbr, name, None, None, "nws"]
    for ss, (abbr, name) in states.items():
        pts = [(c["lat"], c["lon"], c["aland"]) for f, c in current.items() if f[:2] == ss]
        p = mean_point(pts)
        rows[ss + "000"] = [ss + "000", abbr, name, p[0] if p else None, p[1] if p else None, "state"]
    rows["00000"] = ["00000", "US", "United States", None, None, "national"]
    return [rows[k] for k in sorted(rows)]


def build_marine(work: Path) -> list[list]:
    rows: dict[str, list] = {}
    for f in NWS_MARINE:
        for line in fetch(NWS_WSOM + f, work / f).read_text(encoding="utf-8", errors="replace").splitlines():
            p = line.split("|")
            if len(p) >= 5 and p[1] not in rows:
                lat, lon = float(p[3]), float(p[4])
                ok = -90 <= lat <= 90 and -180 <= lon <= 180
                rows[p[1]] = [p[1], p[0], " ".join(p[2].split()), lat if ok else None, lon if ok else None]
    areas = fetch(NWS_WSOM + NWS_MARINE_AREAS, work / NWS_MARINE_AREAS).read_text(encoding="utf-8").splitlines()
    for line in areas:
        p = line.split("|")
        if len(p) >= 4:
            rows[p[1] + "000"] = [p[1] + "000", p[0], " ".join(p[3].split()), None, None]
    return [rows[k] for k in sorted(rows)]


def write_csv(path: Path, header: list[str], rows: list[list]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(header)
        for r in rows:
            w.writerow(["" if v is None else round(v, DEC) if isinstance(v, float) else v for v in r])


def main() -> None:
    work = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp(prefix="same-tables-"))
    work.mkdir(parents=True, exist_ok=True)
    here = Path(__file__).resolve().parent
    counties = build_counties(work)
    write_csv(here / "us_counties.csv", ["fips", "state", "name", "lat", "lon", "src"], counties)
    marine = build_marine(work)
    write_csv(here / "marine_zones.csv", ["code", "area", "name", "lat", "lon"], marine)
    print(f"{len(counties)} county rows, {len(marine)} marine rows (downloads in {work})")


if __name__ == "__main__":
    main()
