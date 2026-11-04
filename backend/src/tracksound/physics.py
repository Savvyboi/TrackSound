"""Small shared physics helpers."""
from __future__ import annotations

import numpy as np

DEFAULT_TEMPERATURE_C = 15.0
SPEED_OF_LIGHT = 299_792_458.0


def speed_of_sound(temp_c: float | None = None) -> float:
    """Speed of sound in dry air (m/s) at temp_c (default 15 °C)."""
    t = DEFAULT_TEMPERATURE_C if temp_c is None else float(temp_c)
    return float(331.3 * np.sqrt(1.0 + t / 273.15))


def case_sound_speed(case: dict) -> float:
    return speed_of_sound(case.get("temperature_c"))


def haversine_m(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371000.0 * np.arcsin(np.sqrt(np.minimum(a, 1.0)))


def pad_bbox(bbox: dict, pad_m: float) -> dict:
    """Grow a {lat_min, lat_max, lon_min, lon_max} box by pad_m metres on every side."""
    lat_c = (bbox["lat_min"] + bbox["lat_max"]) / 2
    dlat = pad_m / 111320.0
    dlon = pad_m / (111320.0 * max(np.cos(np.radians(lat_c)), 1e-6))
    return {"lat_min": max(-90.0, bbox["lat_min"] - dlat), "lat_max": min(90.0, bbox["lat_max"] + dlat),
            "lon_min": bbox["lon_min"] - dlon, "lon_max": bbox["lon_max"] + dlon}


def bbox_tuple(bbox: dict) -> tuple[float, float, float, float]:
    """dopplertrack's (lat_min, lat_max, lon_min, lon_max) order."""
    return (float(bbox["lat_min"]), float(bbox["lat_max"]), float(bbox["lon_min"]), float(bbox["lon_max"]))
