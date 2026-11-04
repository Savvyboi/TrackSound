"""Local geometry for the lightning solver.

Distances that matter here are at most a few tens of kilometres, so a local plane is
accurate enough. Points are stored as ``y = R * (lat - lat0)`` and ``u = R * (lon - lon0)``
(metres, radians times the Earth radius). The east-west distance between two points uses the
cosine of their *mean* latitude (second-order Taylor expansion around ``lat0``), which keeps
the error below about 0.05 % for 25 km baselines anywhere outside the polar regions.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

EARTH_R = 6371000.0


@dataclass(frozen=True)
class LocalFrame:
    lat0: float
    lon0: float

    @property
    def _phi0(self) -> float:
        return math.radians(self.lat0)

    def to_yu(self, lat, lon) -> tuple[np.ndarray, np.ndarray]:
        lat = np.asarray(lat, dtype=np.float64)
        lon = np.asarray(lon, dtype=np.float64)
        return EARTH_R * np.radians(lat - self.lat0), EARTH_R * np.radians(lon - self.lon0)

    def to_latlon(self, y, u) -> tuple[np.ndarray, np.ndarray]:
        return (self.lat0 + np.degrees(np.asarray(y) / EARTH_R),
                self.lon0 + np.degrees(np.asarray(u) / EARTH_R))

    def east_scale(self, y_mean) -> np.ndarray:
        """cos(latitude) at the mean y of a pair (Taylor expansion around lat0)."""
        d = np.asarray(y_mean) / EARTH_R
        c0, s0 = math.cos(self._phi0), math.sin(self._phi0)
        return c0 - s0 * d - 0.5 * c0 * d * d

    def dist(self, y1, u1, y2, u2) -> np.ndarray:
        """Distance in metres between (y1, u1) and (y2, u2); broadcasts."""
        dy = y1 - y2
        dx = (u1 - u2) * self.east_scale(0.5 * (y1 + y2))
        return np.sqrt(dx * dx + dy * dy)

    def east_north(self, y, u) -> tuple[np.ndarray, np.ndarray]:
        """Approximate east/north metres from the frame origin (for Jacobians and extents)."""
        return np.asarray(u) * self.east_scale(np.asarray(y) * 0.5), np.asarray(y)


def frame_for_bbox(bbox: dict) -> LocalFrame:
    return LocalFrame(0.5 * (bbox["lat_min"] + bbox["lat_max"]), 0.5 * (bbox["lon_min"] + bbox["lon_max"]))


def bbox_size_m(bbox: dict) -> tuple[float, float]:
    """(east-west, north-south) extent of a bbox in metres."""
    lat_c = math.radians(0.5 * (bbox["lat_min"] + bbox["lat_max"]))
    ns = EARTH_R * math.radians(bbox["lat_max"] - bbox["lat_min"])
    ew = EARTH_R * math.radians(bbox["lon_max"] - bbox["lon_min"]) * max(math.cos(lat_c), 1e-6)
    return ew, ns


def grid_points(bbox: dict, spacing_m: float, frame: LocalFrame) -> tuple[np.ndarray, np.ndarray, int, int]:
    """Regular grid over the bbox: (y, u) arrays in the frame, plus (n_rows, n_cols)."""
    lat_c = math.radians(0.5 * (bbox["lat_min"] + bbox["lat_max"]))
    dlat = math.degrees(spacing_m / EARTH_R)
    dlon = math.degrees(spacing_m / (EARTH_R * max(math.cos(lat_c), 1e-6)))
    lats = np.arange(bbox["lat_min"] + dlat / 2, bbox["lat_max"], dlat)
    lons = np.arange(bbox["lon_min"] + dlon / 2, bbox["lon_max"], dlon)
    if len(lats) == 0:
        lats = np.array([0.5 * (bbox["lat_min"] + bbox["lat_max"])])
    if len(lons) == 0:
        lons = np.array([0.5 * (bbox["lon_min"] + bbox["lon_max"])])
    la, lo = np.meshgrid(lats, lons, indexing="ij")
    y, u = frame.to_yu(la.ravel(), lo.ravel())
    return y, u, len(lats), len(lons)


def square_bbox(lat: float, lon: float, half_m: float) -> dict:
    """Square bbox of +-half_m metres around a point."""
    dlat = math.degrees(half_m / EARTH_R)
    dlon = math.degrees(half_m / (EARTH_R * max(math.cos(math.radians(lat)), 1e-6)))
    return {"lat_min": lat - dlat, "lat_max": lat + dlat, "lon_min": lon - dlon, "lon_max": lon + dlon}


def normalize_lon(lon):
    """Longitude in [-180, 180)."""
    return (np.asarray(lon, dtype=np.float64) + 180.0) % 360.0 - 180.0


def in_bbox(lat, lon, bbox: dict) -> np.ndarray:
    lat = np.asarray(lat, dtype=np.float64)
    lon = np.asarray(lon, dtype=np.float64)
    return ((lat >= bbox["lat_min"]) & (lat <= bbox["lat_max"])
            & (lon >= bbox["lon_min"]) & (lon <= bbox["lon_max"]))
