"""Synthetic prop flights and the recording a listener would make of them.

Used by the demo case (``tracksound demo``) and the end-to-end tests: everything is generated,
so no ADS-B download is needed and the true answer is known.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from dopplertrack.adsb import Trace
from dopplertrack.doppler import curve_from_trajectory
from dopplertrack.geo import enu_to_lla

PROP_TYPES = [("C172", "Cessna 172"), ("PA28", "Piper PA-28"), ("SR22", "Cirrus SR22"), ("PC12", "Pilatus PC-12"),
              ("B350", "Beech King Air 350"), ("DA40", "Diamond DA40"), ("BE58", "Beech Baron"), ("C208", "Cessna Caravan")]


@dataclass
class PlannedPass:
    """A flight that passes the listener: closest approach at t_cpa (s after the recording
    starts), lateral_m off the listener's position, at alt_m, speed v_mps, heading deg."""
    t_cpa: float
    lateral_m: float
    alt_m: float
    v_mps: float
    heading_deg: float
    turn_dps: float = 0.0          # gentle turn rate, deg/s (real flights are not straight)
    f0: float = 110.0              # blade-pass fundamental, Hz


def _path(t_rel: np.ndarray, p: PlannedPass) -> np.ndarray:
    """ENU positions (m) relative to the listener at times t_rel (s from CPA)."""
    th0 = np.radians(p.heading_deg)
    om = np.radians(p.turn_dps)
    dt = np.diff(t_rel, prepend=t_rel[0])
    # integrate heading forward and backward from CPA so the CPA geometry stays as planned
    th = th0 + om * t_rel
    ve, vn = p.v_mps * np.sin(th), p.v_mps * np.cos(th)
    e = np.cumsum(ve * dt); n = np.cumsum(vn * dt)
    i0 = int(np.argmin(np.abs(t_rel)))
    e -= e[i0]; n -= n[i0]
    # lateral offset perpendicular to the heading at CPA
    e += p.lateral_m * np.cos(th0); n -= p.lateral_m * np.sin(th0)
    u = np.full_like(t_rel, p.alt_m)
    return np.stack([e, n, u], -1)


def _trace(icao: str, reg: str, typ: tuple[str, str], t: np.ndarray, enu: np.ndarray, lat0: float, lon0: float,
           v: float, rng) -> Trace:
    noisy = enu + rng.normal(0, 4.0, enu.shape)            # ADS-B position noise
    lat, lon, alt = enu_to_lla(noisy[:, 0], noisy[:, 1], noisy[:, 2], lat0, lon0)
    return Trace(icao, reg, typ[0], typ[1], t.astype(float), lat, lon, alt, np.full(len(t), v), np.zeros(len(t), bool))


def make_world(lat0: float, lon0: float, rec_start_utc: float, duration_s: float, passes: list[PlannedPass],
               n_background: int = 24, region_half_m: float = 30_000.0, seed: int = 7):
    """Traces for the planned passes plus background flights elsewhere in the region.
    Returns (traces, dense planned trajectories for synthesis [(t_unix, enu, f0)])."""
    rng = np.random.default_rng(seed)
    traces, dense = [], []
    for k, p in enumerate(passes):
        t_rel = np.arange(-1500.0, 1500.0, 1.0)
        enu = _path(t_rel, p)
        t_unix = rec_start_utc + p.t_cpa + t_rel
        dense.append((t_unix, enu, p.f0))
        sel = np.arange(0, len(t_rel), 2)                    # ADS-B every ~2 s
        typ = PROP_TYPES[k % len(PROP_TYPES)]
        traces.append(_trace(f"a{k:05x}", f"N{100 + k}TS", typ, t_unix[sel], enu[sel], lat0, lon0, p.v_mps, rng))
    for k in range(n_background):
        # a random straight-ish line across the region, airborne at a random time
        heading = rng.uniform(0, 360)
        lateral = rng.uniform(-region_half_m, region_half_m)
        if abs(lateral) < 9000:                              # keep background flights inaudible at the listener
            lateral = np.sign(lateral or 1) * rng.uniform(9000, region_half_m)
        p = PlannedPass(t_cpa=rng.uniform(-1800, duration_s + 1800), lateral_m=lateral, alt_m=rng.uniform(500, 3000),
                        v_mps=rng.uniform(50, 110), heading_deg=heading, turn_dps=rng.uniform(-0.03, 0.03))
        t_rel = np.arange(-1500.0, 1500.0, 2.0)
        enu = _path(t_rel, p)
        typ = PROP_TYPES[(k + 3) % len(PROP_TYPES)]
        traces.append(_trace(f"b{k:05x}", f"N{500 + k}BG", typ, rec_start_utc + p.t_cpa + t_rel, enu, lat0, lon0, p.v_mps, rng))
    return traces, dense


def synth_audio(dense, rec_start_utc: float, duration_s: float, sr: int = 4000, c: float = 340.3,
                noise: float = 0.3, tone: float = 0.06, harmonic2: float = 0.5, seed: int = 3) -> np.ndarray:
    """The listener at the ENU origin: pinkish noise plus each flight's Doppler-shifted
    blade-pass tone and second harmonic, loudness falling off with distance.

    With the defaults a pass at 2 km is about 15 dB above the noise in a 0.5 Hz bin, and one
    at 4-5 km about 9 dB: visible on the spectrogram, faint enough to need the whitening."""
    rng = np.random.default_rng(seed)
    n = int(duration_s * sr)
    white = rng.standard_normal(n).astype(np.float32)
    # gentle low-pass tilt so the noise is not perfectly white
    from scipy.signal import lfilter
    x = (noise * lfilter([1.0], [1.0, -0.6], white) / 1.6).astype(np.float32)
    tt = rec_start_utc + np.arange(n) / sr
    obs = np.zeros(3)
    for t_unix, enu, f0 in dense:
        to, ratio, r = curve_from_trajectory(t_unix, enu, obs, f0=1.0, c=c)
        if np.nanmin(r) > 9000:
            continue
        finst = np.interp(tt, to, ratio * f0, left=np.nan, right=np.nan)
        amp = np.interp(tt, to, np.clip(2000.0 / r, 0, 3.0), left=0.0, right=0.0) * tone
        m = np.isfinite(finst)
        if not m.any():
            continue
        ph = 2 * np.pi * np.cumsum(np.nan_to_num(finst, nan=0.0)) / sr
        x[m] += (amp[m] * (np.sin(ph[m]) + harmonic2 * np.sin(2 * ph[m] + 0.7))).astype(np.float32)
    return x


DEMO_PASSES = [
    PlannedPass(t_cpa=420.0, lateral_m=2600.0, alt_m=900.0, v_mps=62.0, heading_deg=70.0, turn_dps=0.02, f0=118.0),
    PlannedPass(t_cpa=1180.0, lateral_m=-3400.0, alt_m=1300.0, v_mps=88.0, heading_deg=200.0, turn_dps=-0.015, f0=96.0),
    PlannedPass(t_cpa=1830.0, lateral_m=1800.0, alt_m=700.0, v_mps=55.0, heading_deg=310.0, turn_dps=0.01, f0=132.0),
    PlannedPass(t_cpa=2280.0, lateral_m=-4200.0, alt_m=1700.0, v_mps=96.0, heading_deg=15.0, turn_dps=0.0, f0=104.0),
]
