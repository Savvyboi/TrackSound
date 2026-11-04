"""Countries: outlines, a "recorded in this country" filter, and the power grids each belongs to.

A case can name the countries a recording is known to come from (``case["countries"]``, ISO
3166-1 alpha-2 codes). Every source then only considers that area: searches put grid points
inside those countries only, catalogue downloads default to the countries' bounding boxes, and
the mains-hum source only offers references for the countries' power grids.

Outlines are Natural Earth 1:10m admin-0 countries (public domain), simplified to about 400 m
and bundled as ``data/countries.geojson.gz`` (built by ``scripts/build_countries.py``). Outlines
are still generalised, so points within ``MARGIN_M`` of a country count as inside it.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import numpy as np
from matplotlib.path import Path as MplPath

DATA = Path(__file__).resolve().parent / "data" / "countries.geojson.gz"
MARGIN_M = 2000.0
EARTH_M_PER_DEG = 111_320.0
MAX_LATTICE = 20_000_000          # grid points allocated before masking
MAX_COUNTRY_KM2 = 1_000_000       # larger countries need a region drawn inside them
_AREA_CACHE: dict[tuple, float] = {}


@dataclass
class Part:
    """One polygon of a country: exterior ring and holes, with its bounding box."""
    exterior: MplPath
    holes: list[MplPath]
    bbox: dict
    area_km2: float

    def contains(self, lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
        pts = np.column_stack([lon, lat])
        inside = self.exterior.contains_points(pts)
        for h in self.holes:
            if inside.any():
                inside &= ~h.contains_points(pts)
        return inside


@dataclass
class Country:
    code: str
    iso3: str
    name: str
    short_name: str
    sovereign: str | None
    type: str | None
    continent: str | None
    mains_hz: int | str | None
    parts: list[Part] = field(repr=False)
    geometry: dict = field(repr=False)

    @property
    def bbox(self) -> dict:
        return union_bbox([p.bbox for p in self.parts])

    @property
    def area_km2(self) -> float:
        return float(sum(p.area_km2 for p in self.parts))

    def summary(self) -> dict:
        return {"code": self.code, "iso3": self.iso3, "name": self.name, "short_name": self.short_name,
                "sovereign": self.sovereign, "type": self.type, "continent": self.continent,
                "mains_hz": self.mains_hz, "bbox": self.bbox, "n_parts": len(self.parts),
                "area_km2": round(self.area_km2), "grids": grids_for([self.code])}


def _ring_area_km2(ring: np.ndarray) -> float:
    """Approximate area of a lon/lat ring (equirectangular at the ring's mean latitude)."""
    lat0 = math.radians(float(np.mean(ring[:, 1])))
    x = ring[:, 0] * EARTH_M_PER_DEG * math.cos(lat0)
    y = ring[:, 1] * EARTH_M_PER_DEG
    return abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))) / 2 / 1e6


def _part(poly: list) -> Part:
    ext = np.asarray(poly[0], float)
    holes = [np.asarray(h, float) for h in poly[1:]]
    bbox = {"lat_min": float(ext[:, 1].min()), "lat_max": float(ext[:, 1].max()),
            "lon_min": float(ext[:, 0].min()), "lon_max": float(ext[:, 0].max())}
    area = _ring_area_km2(ext) - sum(_ring_area_km2(h) for h in holes)
    return Part(MplPath(ext), [MplPath(h) for h in holes], bbox, max(area, 0.0))


@lru_cache(maxsize=1)
def _load() -> dict[str, Country]:
    import gzip
    doc = json.loads(gzip.decompress(DATA.read_bytes()).decode("utf-8"))
    out = {}
    for f in doc["features"]:
        p, g = f["properties"], f["geometry"]
        polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        out[p["code"]] = Country(p["code"], p["iso3"], p["name"], p["short_name"], p.get("sovereign"), p.get("type"),
                                 p.get("continent"), p.get("mains_hz"), [_part(poly) for poly in polys], g)
    return out


def all_countries() -> list[Country]:
    return sorted(_load().values(), key=lambda c: c.name)


def get(code: str) -> Country:
    c = _load().get((code or "").upper())
    if c is None:
        raise KeyError(f"unknown country code {code!r}")
    return c


def validate(codes: list[str]) -> list[str]:
    """Upper-cased, de-duplicated, known codes; raises ValueError on an unknown one."""
    out = []
    for code in codes:
        code = (code or "").strip().upper()
        if code not in _load():
            raise ValueError(f"unknown country code {code!r} (use ISO 3166-1 alpha-2, e.g. PT)")
        if code not in out:
            out.append(code)
    return out


# ---- bounding boxes ---------------------------------------------------------------------------

def union_bbox(boxes: list[dict]) -> dict:
    return {"lat_min": min(b["lat_min"] for b in boxes), "lat_max": max(b["lat_max"] for b in boxes),
            "lon_min": min(b["lon_min"] for b in boxes), "lon_max": max(b["lon_max"] for b in boxes)}


def intersect_bbox(a: dict, b: dict) -> dict | None:
    box = {"lat_min": max(a["lat_min"], b["lat_min"]), "lat_max": min(a["lat_max"], b["lat_max"]),
           "lon_min": max(a["lon_min"], b["lon_min"]), "lon_max": min(a["lon_max"], b["lon_max"])}
    return box if box["lat_min"] < box["lat_max"] and box["lon_min"] < box["lon_max"] else None


def _grow(b: dict, m: float) -> dict:
    dlat = m / EARTH_M_PER_DEG
    dlon = m / (EARTH_M_PER_DEG * max(math.cos(math.radians((b["lat_min"] + b["lat_max"]) / 2)), 1e-6))
    return {"lat_min": b["lat_min"] - dlat, "lat_max": b["lat_max"] + dlat,
            "lon_min": b["lon_min"] - dlon, "lon_max": b["lon_max"] + dlon}


# ---- the search area of a case ------------------------------------------------------------------

@dataclass
class Area:
    """Where a case's searches look: the region, the countries, or the region inside the countries."""
    region: dict | None
    countries: list[Country]
    margin_m: float = MARGIN_M

    @property
    def codes(self) -> list[str]:
        return [c.code for c in self.countries]

    def _parts(self) -> list[tuple[Part, dict]]:
        """Country parts that matter, each with its bbox clipped to the region."""
        out = []
        for c in self.countries:
            for p in c.parts:
                box = _grow(p.bbox, self.margin_m)
                if self.region is not None:
                    box = intersect_bbox(box, self.region)
                if box is not None:
                    out.append((p, box))
        return out

    @property
    def bbox(self) -> dict | None:
        if not self.countries:
            return self.region
        parts = self._parts()
        return union_bbox([b for _, b in parts]) if parts else None

    def contains(self, lat, lon) -> np.ndarray:
        """True where a point is inside the area (countries with a margin, and the region)."""
        lat = np.atleast_1d(np.asarray(lat, float))
        lon = np.atleast_1d(np.asarray(lon, float))
        ok = np.ones(lat.shape, bool)
        if self.region is not None:
            r = self.region
            ok &= (lat >= r["lat_min"]) & (lat <= r["lat_max"]) & (lon >= r["lon_min"]) & (lon <= r["lon_max"])
        if not self.countries:
            return ok
        inside = np.zeros(lat.shape, bool)
        # the margin: a point counts if it or any of 8 points around it (margin_m away) is inside
        dlat = self.margin_m / EARTH_M_PER_DEG
        offsets = [(0.0, 0.0)] + [(dlat * math.sin(a), dlat * math.cos(a)) for a in np.arange(8) * math.pi / 4]
        coslat = np.maximum(np.cos(np.radians(lat)), 1e-6)
        for p, box in self._parts():
            near = ok & ~inside & (lat >= box["lat_min"]) & (lat <= box["lat_max"]) & \
                (lon >= box["lon_min"]) & (lon <= box["lon_max"])
            if not near.any():
                continue
            idx = np.flatnonzero(near)
            hit = np.zeros(len(idx), bool)
            for dy, dx in offsets:
                todo = ~hit
                if not todo.any():
                    break
                j = idx[todo]
                hit[todo] = p.contains(lat[j] + dy, lon[j] + dx / coslat[j])
            inside[idx[hit]] = True
        return ok & inside

    def lattice_step(self, spacing_m: float) -> tuple[float, float]:
        """Degrees per grid step. One lattice for the whole area (aligned to multiples of the
        step), so grids of neighbouring islands line up and overlapping margins don't duplicate."""
        b = self.bbox
        lat_mid = 0.0 if b is None else (b["lat_min"] + b["lat_max"]) / 2
        dlat = spacing_m / EARTH_M_PER_DEG
        return dlat, spacing_m / (EARTH_M_PER_DEG * max(math.cos(math.radians(lat_mid)), 0.05))

    def _boxes(self) -> list[dict]:
        return ([self.region] if self.region else []) if not self.countries else [b for _, b in self._parts()]

    def lattice_size(self, spacing_m: float) -> int:
        """Grid points before the country mask (what must be allocated)."""
        dlat, dlon = self.lattice_step(spacing_m)
        return int(sum((math.floor(b["lat_max"] / dlat) - math.ceil(b["lat_min"] / dlat) + 1) *
                       (math.floor(b["lon_max"] / dlon) - math.ceil(b["lon_min"] / dlon) + 1) for b in self._boxes()))

    def grid(self, spacing_m: float, max_points: int = MAX_LATTICE) -> tuple[np.ndarray, np.ndarray]:
        """(lat, lon) of a regular grid over the area (inside the countries, with the margin)."""
        n = self.lattice_size(spacing_m)
        if n > max_points:
            raise ValueError(f"the search area is too large for a {spacing_m:.0f} m grid ({n:,} points). Use a "
                             f"coarser grid (at least {suggest_spacing(self, max_points):.0f} m) or a smaller region.")
        dlat, dlon = self.lattice_step(spacing_m)
        ks, ms = [], []
        for b in self._boxes():
            k = np.arange(math.ceil(b["lat_min"] / dlat), math.floor(b["lat_max"] / dlat) + 1)
            m = np.arange(math.ceil(b["lon_min"] / dlon), math.floor(b["lon_max"] / dlon) + 1)
            if len(k) and len(m):
                K, M = np.meshgrid(k, m, indexing="ij")
                ks.append(K.ravel()); ms.append(M.ravel())
        if not ks:
            return np.zeros(0), np.zeros(0)
        km = np.unique(np.column_stack([np.concatenate(ks), np.concatenate(ms)]), axis=0)
        lat, lon = km[:, 0] * dlat, km[:, 1] * dlon
        if self.countries:
            keep = self.contains(lat, lon)
            lat, lon = lat[keep], lon[keep]
        return lat, lon

    def key(self) -> tuple:
        r = self.region
        return (tuple(self.codes), None if r is None else tuple(round(r[k], 6) for k in ("lat_min", "lat_max", "lon_min", "lon_max")))

    def area_km2(self) -> float:
        """Land area searched (countries clipped to the region), from a coarse grid count."""
        k = self.key()
        if k not in _AREA_CACHE:
            _AREA_CACHE[k] = self._area_km2()
            if len(_AREA_CACHE) > 256:
                _AREA_CACHE.pop(next(iter(_AREA_CACHE)))
        return _AREA_CACHE[k]

    def _area_km2(self) -> float:
        if not self.countries and self.region is None:
            return 0.0
        b = self.bbox
        if b is None:
            return 0.0
        step = max(1000.0, math.sqrt(max(_bbox_km2(b), 1.0) * 1e6 / 400_000))
        lat, _ = self.grid(step, max_points=50_000_000)
        return float(len(lat) * (step / 1000) ** 2)

    def too_large(self) -> bool:
        """Countries bigger than about France need a drawn region: a whole-country search or
        download would be too heavy, and some (US, Russia, Fiji) cross the 180th meridian."""
        if not self.countries or self.region is not None:
            return False
        b = self.bbox
        return b is None or sum(c.area_km2 for c in self.countries) > MAX_COUNTRY_KM2 or (b["lon_max"] - b["lon_min"]) > 60

    def center(self) -> tuple[float, float] | None:
        """A representative point: the region's centre, else the centre of the largest landmass
        of the countries (the union box of Portugal with the Azores is mid-Atlantic)."""
        if self.region is not None:
            r = self.region
            return (r["lat_min"] + r["lat_max"]) / 2, (r["lon_min"] + r["lon_max"]) / 2
        if not self.countries:
            return None
        part = max((p for c in self.countries for p in c.parts), key=lambda p: p.area_km2)
        v = part.exterior.vertices
        return float(v[:, 1].mean()), float(v[:, 0].mean())

    def describe(self) -> str:
        names = ", ".join(c.short_name for c in self.countries)
        if names and self.region is not None:
            return f"the search region inside {names}"
        return names or "the search region"


def _bbox_km2(b: dict) -> float:
    return ((b["lat_max"] - b["lat_min"]) * EARTH_M_PER_DEG / 1000) * \
        ((b["lon_max"] - b["lon_min"]) * EARTH_M_PER_DEG * math.cos(math.radians((b["lat_max"] + b["lat_min"]) / 2)) / 1000)


def suggest_spacing(area: Area, max_points: int) -> float:
    """Smallest round grid spacing whose lattice stays under max_points."""
    for s in (100, 150, 200, 250, 300, 400, 500, 750, 1000, 1500, 2000, 3000, 5000, 7500, 10000, 20000):
        if area.lattice_size(s) <= max_points:
            return float(s)
    return 50000.0


def case_area(case: dict) -> Area | None:
    """The case's search area, or None when it has neither region nor countries."""
    codes = case.get("countries") or []
    region = case.get("region")
    if not codes and not region:
        return None
    return Area(region, [get(c) for c in codes])


# ---- power grids (for mains-hum references) ----------------------------------------------------

_CE = "Continental Europe"
GRIDS: dict[str, list[str]] = {
    **{c: [_CE] for c in ("AD", "AL", "AT", "BA", "BE", "BG", "CH", "CZ", "DE", "ES", "FR", "GR", "HR", "HU", "IT",
                          "LI", "LU", "MC", "ME", "MK", "NL", "PL", "PT", "RO", "RS", "SI", "SK", "SM", "TR", "VA",
                          "XK", "MA", "DZ", "TN")},
    "DK": [_CE, "Nordic"],                       # Jutland/Funen: Continental Europe; Zealand: Nordic
    "NO": ["Nordic"], "SE": ["Nordic"], "FI": ["Nordic"], "AX": ["Nordic"],
    "GB": ["Great Britain", "Ireland (all-island)"],   # Northern Ireland is on the all-island grid
    "IE": ["Ireland (all-island)"], "IM": ["Great Britain"], "JE": [_CE], "GG": [_CE],
    "IS": ["Iceland"], "FO": ["Faroe Islands"],
    "UA": [_CE], "MD": [_CE],                    # synchronised with Continental Europe since 2022-03-16
    "EE": [_CE], "LV": [_CE], "LT": [_CE],       # since 2025-02-09; before that, the Russian-led IPS/UPS
    "RU": ["Russia (IPS/UPS)"], "BY": ["Russia (IPS/UPS)"], "KZ": ["Russia (IPS/UPS)"],
    "ZA": ["Southern Africa"], "BW": ["Southern Africa"], "NA": ["Southern Africa"], "ZW": ["Southern Africa"],
    "MZ": ["Southern Africa"], "LS": ["Southern Africa"], "SZ": ["Southern Africa"], "ZM": ["Southern Africa"],
    "US": ["North America (Eastern Interconnection)", "North America (Western Interconnection)",
           "North America (Texas Interconnection)", "Alaska", "Hawaii"],
    "CA": ["North America (Eastern Interconnection)", "North America (Western Interconnection)", "Quebec"],
    "MX": ["Mexico", "North America (Western Interconnection)"],
    "AU": ["Australia (NEM mainland)", "Australia (Tasmania)", "Western Australia (SWIS)"],
    "JP": ["Japan (East, 50 Hz)", "Japan (West, 60 Hz)"],
}
BALTIC_SWITCH_UTC_DATE = "2025-02-09"


def grids_for(codes: list[str], date: str | None = None) -> list[str]:
    """Synchronous power grids that cover these countries (on ``date``, YYYY-MM-DD, if given)."""
    out: list[str] = []
    for code in codes:
        gs = list(GRIDS.get(code.upper(), []))
        if code.upper() in ("EE", "LV", "LT") and date and date < BALTIC_SWITCH_UTC_DATE:
            gs = ["Baltic (then synchronous with Russia)", "Russia (IPS/UPS)"]
        for g in gs:
            if g not in out:
                out.append(g)
    return out


def grid_matches(reference_grid: str, grids: list[str]) -> bool:
    """Does a reference's grid name belong to one of these grids? (Names are compared loosely:
    'Continental Europe' matches 'Continental Europe (France)'.)"""
    rg = (reference_grid or "").lower()
    return any(g.lower() in rg or rg in g.lower() for g in grids if g)
