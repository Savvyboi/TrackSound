"""Which mains frequency each country uses, as a GeoJSON map for the UI.

``tracksound/data/countries_mains.geojson`` is Natural Earth's 1:110m Admin-0 countries (public
domain), coordinates rounded to 0.01 degree, with ``mains_hz`` from Wikipedia's "Mains
electricity by country" (revision 1380969207, which cites the IEC World Plugs list and
IEC TR 60083). Japan is split: 50 Hz in the east (Tokyo, Tohoku, Hokkaido), 60 Hz in the west.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).resolve().parents[2] / "data" / "countries_mains.geojson"


@lru_cache(maxsize=1)
def grid_map() -> dict:
    """FeatureCollection with properties ``{name, iso_a3, mains_hz: 50 | 60 | "50/60" | null}``."""
    with open(DATA, encoding="utf-8") as fh:
        return json.load(fh)


def mains_hz_of(iso_a3: str) -> int | str | None:
    for f in grid_map()["features"]:
        if f["properties"]["iso_a3"] == iso_a3:
            return f["properties"]["mains_hz"]
    return None
