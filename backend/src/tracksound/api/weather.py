"""Air temperature for a case (it sets the speed of sound), from the Open-Meteo archive.

Open-Meteo is free for non-commercial use (data CC BY 4.0). The archive (ERA5 reanalysis)
lags real time by about five days, so recent dates fall back to the forecast API, which also
serves the recent past.
"""
from __future__ import annotations

from datetime import datetime, timezone

import requests
from fastapi import APIRouter, Depends, HTTPException

from ..deps import get_store
from ..physics import speed_of_sound
from ..store import Store

router = APIRouter(prefix="/api", tags=["weather"])

ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
FORECAST = "https://api.open-meteo.com/v1/forecast"
ATTRIBUTION = "Weather data by Open-Meteo.com (CC BY 4.0)"


def _hourly(url: str, lat: float, lon: float, date: str) -> list[dict]:
    r = requests.get(url, params={"latitude": round(lat, 4), "longitude": round(lon, 4), "start_date": date,
                                  "end_date": date, "hourly": "temperature_2m", "timezone": "UTC"}, timeout=30)
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if not r.ok or body.get("error"):
        raise ValueError(body.get("reason") or f"HTTP {r.status_code}")
    h = body.get("hourly") or {}
    out = []
    for t, v in zip(h.get("time", []), h.get("temperature_2m", [])):
        if v is None:
            continue
        ts = datetime.fromisoformat(t).replace(tzinfo=timezone.utc).timestamp()
        out.append({"t_utc": ts, "temperature_c": float(v)})
    return out


@router.get("/cases/{case_id}/weather")
def case_weather(case_id: str, store: Store = Depends(get_store)):
    """Hourly 2 m temperature at the region centre on the case date, and a suggested value
    (at the recording start if known, else the day's mean)."""
    case = store.get_case(case_id)
    if not case.get("date") or not case.get("region"):
        raise HTTPException(400, "set the case date and region first")
    reg = case["region"]
    lat, lon = (reg["lat_min"] + reg["lat_max"]) / 2, (reg["lon_min"] + reg["lon_max"]) / 2
    errors = []
    hourly = []
    for name, url in (("archive", ARCHIVE), ("forecast", FORECAST)):
        try:
            hourly = _hourly(url, lat, lon, case["date"])
        except (requests.RequestException, ValueError) as e:
            errors.append(f"{name}: {e}")
            continue
        if hourly:
            source = name
            break
    if not hourly:
        raise HTTPException(502, "no temperature available from Open-Meteo (" + "; ".join(errors) + ")")
    start = case.get("start_utc")
    if start is not None:
        dur = (case.get("recording") or {}).get("duration_s") or 0.0
        mid = start + dur / 2
        suggested = min(hourly, key=lambda h: abs(h["t_utc"] - mid))["temperature_c"]
        basis = "nearest hour to the middle of the recording"
    else:
        suggested = sum(h["temperature_c"] for h in hourly) / len(hourly)
        basis = "mean of the day (set the start time for a better value)"
    return {"source": f"open-meteo-{source}", "lat": lat, "lon": lon, "date": case["date"], "hourly": hourly,
            "suggested_c": round(suggested, 1), "basis": basis,
            "sound_speed_mps": round(speed_of_sound(suggested), 2), "attribution": ATTRIBUTION}
