"""One JSON document with everything known about a case, for export.

It is assembled from the public endpoints of each source (called in-process), so it stays
correct however a source stores its own files.
"""
from __future__ import annotations

import time

import httpx
from fastapi import APIRouter, Depends, Request

from .. import __version__
from ..deps import get_store
from ..store import Store

router = APIRouter(prefix="/api", tags=["report"])


async def _get(client: httpx.AsyncClient, path: str):
    try:
        r = await client.get(path)
    except Exception as e:  # noqa: BLE001 - a broken source must not break the export
        return {"error": f"{type(e).__name__}: {e}"}
    if r.status_code == 200:
        return r.json()
    return {"error": f"HTTP {r.status_code}", "detail": (r.json().get("detail") if r.headers.get("content-type", "").startswith("application/json") else None)}


def _alerts_estimate(alerts) -> dict | None:
    """The most confident decoded emergency-alert header: counties and an implied start time."""
    headers = (alerts or {}).get("headers") if isinstance(alerts, dict) else None
    if not headers:
        return None
    h = max(headers, key=lambda h: h.get("confidence") or 0)
    locs = [loc for loc in h.get("locations", []) if loc.get("lat") is not None]
    est = {"source": "alerts", "header_id": h["id"], "event": h.get("event_name") or h.get("event"),
           "station": h.get("station"), "start_utc": h.get("implied_start_utc"), "confidence": h.get("confidence"),
           "counties": [f"{loc.get('county')}, {loc.get('state')}" for loc in h.get("locations", []) if loc.get("county")]}
    if locs:
        est["lat"] = sum(loc["lat"] for loc in locs) / len(locs)
        est["lon"] = sum(loc["lon"] for loc in locs) / len(locs)
    return est


def _estimates(aircraft_searches, lightning, enf, alerts=None) -> list[dict]:
    """The best result per source, in one shape."""
    out = []
    done = [s for s in (aircraft_searches if isinstance(aircraft_searches, list) else []) if s.get("status") == "done" and s.get("best")]
    if done:
        s = done[0]                                   # newest first
        b = s["best"]
        out.append({"source": "aircraft", "search_id": s["id"], "mode": s["mode"], "lat": b["lat"], "lon": b["lon"],
                    "start_utc": b["start_utc"], "score": b["score"], "margin": s.get("margin"),
                    "flights": [f.get("reg") or f.get("icao") for f in b.get("flights", [])]})
    solves = (lightning or {}).get("solves") if isinstance(lightning, dict) else None
    if solves:
        best = next((s for s in solves if s.get("best")), None)
        if best:
            b = best["best"]
            out.append({"source": "lightning", "solve_id": best["id"], "mode": best.get("mode"), "lat": b.get("lat"),
                        "lon": b.get("lon"), "start_utc": b.get("start_utc"), "uncertainty_m": b.get("uncertainty_m"),
                        "n_matched": b.get("n_matched")})
    if isinstance(enf, dict):
        a = enf.get("analysis")
        matches = [m for m in enf.get("matches") or [] if m.get("best")]
        if a or matches:
            e = {"source": "enf", "nominal_hz": (a or {}).get("nominal_hz")}
            if matches:
                m = max(matches, key=lambda m: m["best"].get("z") or 0)
                e.update(reference_id=m["reference_id"], start_utc=m["best"]["start_utc"], corr=m["best"]["corr"], z=m["best"]["z"])
            out.append(e)
    a = _alerts_estimate(alerts)
    if a:
        out.append(a)
    return out


@router.get("/cases/{case_id}/report")
async def report(case_id: str, request: Request, store: Store = Depends(get_store)):
    case = store.get_case(case_id)
    transport = httpx.ASGITransport(app=request.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://tracksound.local") as client:
        base = f"/api/cases/{case_id}"
        events = await _get(client, f"{base}/aircraft/events")
        flights = await _get(client, f"{base}/aircraft/flights")
        searches = await _get(client, f"{base}/aircraft/searches")
        lightning = await _get(client, f"{base}/lightning")
        enf = await _get(client, f"{base}/enf")
        alerts = await _get(client, f"{base}/alerts")
    if isinstance(flights, dict) and "flights" in flights:
        flights = {k: v for k, v in flights.items() if k != "flights"}
    return {
        "generated_at": time.time(), "tracksound_version": __version__, "case": case,
        "estimates": _estimates(searches, lightning, enf, alerts),
        "aircraft": {"events": events, "flights": flights, "searches": searches},
        "lightning": lightning, "enf": enf, "alerts": alerts,
        "attribution": ["ADS-B history: adsb.lol contributors (ODbL 1.0)", "Terrain: AWS Terrain Tiles (Mapzen and partners)",
                        "Lightning: NOAA GOES GLM (public domain) where used",
                        "Seismic and explosion events: USGS ComCat (public domain) where used",
                        "Counties: US Census Gazetteer (public domain)"],
    }
