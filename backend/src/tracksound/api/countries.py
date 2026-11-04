"""Countries and each case's search area (docs/API.md, "Countries")."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from .. import countries as C
from ..deps import get_store
from ..store import Store

router = APIRouter(prefix="/api", tags=["countries"])


@router.get("/countries")
def list_countries():
    return [c.summary() for c in C.all_countries()]


@router.get("/countries/{code}")
def country_outline(code: str):
    try:
        c = C.get(code)
    except KeyError:
        raise HTTPException(404, f"unknown country code {code!r}")
    return {"type": "Feature", "geometry": c.geometry, "properties": c.summary()}


def area_doc(case: dict) -> dict:
    area = C.case_area(case)
    codes = case.get("countries") or []
    if area is None:
        return {"countries": [], "region": None, "bbox": None, "center": None, "area_km2": 0.0, "too_large": False,
                "description": "not set", "grids": [], "mains_hz": []}
    too_large = area.too_large()
    center = area.center()
    return {"countries": codes, "region": case.get("region"), "bbox": area.bbox,
            "center": None if center is None else {"lat": round(center[0], 5), "lon": round(center[1], 5)},
            "area_km2": 0.0 if too_large else round(area.area_km2(), 1), "too_large": too_large,
            "description": area.describe(), "grids": C.grids_for(codes, case.get("date")),
            "mains_hz": [C.get(c).mains_hz for c in codes]}


@router.get("/cases/{case_id}/area")
def case_area(case_id: str, store: Store = Depends(get_store)):
    return area_doc(store.get_case(case_id))


def require_area(case: dict, what: str = "search") -> C.Area:
    """The case's search area, or a 400 explaining what to set."""
    area = C.case_area(case)
    if area is None:
        raise HTTPException(400, f"set a search region or a country first (needed to {what})")
    if area.too_large():
        names = ", ".join(c.short_name for c in area.countries)
        raise HTTPException(400, f"{names} is too large to {what} as a whole: draw a region inside it")
    if area.bbox is None:
        raise HTTPException(400, "the search region does not overlap the selected countries")
    return area
