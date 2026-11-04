"""Synthetic storms for tests and the demo: a strike catalogue, thunder audio and flash times.

The model is simple but has the features that matter for the method: strikes scattered
around a storm cell that drifts past the listener; catalogue positions with Gaussian errors;
thunder that arrives ``distance / c`` after the strike, starts with a sharp crack when close
and a muffled rise when far (air absorbs high frequencies), then rumbles for seconds; some
strikes not heard; a few bursts with no catalogued strike; rain noise with drop clicks.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from scipy import signal

from .catalogue import assign_ids, make_strike
from .geo import EARTH_R


@dataclass
class SynthStrike:
    t_utc: float
    lat: float                 # true position
    lon: float
    lat_cat: float             # catalogued position (with error)
    lon_cat: float
    sigma_m: float
    distance_m: float          # true distance to the listener
    kind: str
    heard: bool = False        # its thunder is in the recording
    arrival_s: float | None = None   # recording time of the thunder onset (if heard)


@dataclass
class Storm:
    lat: float                 # true listener position
    lon: float
    start_utc: float           # true UTC of recording time 0
    duration_s: float
    c: float                   # speed of sound used
    strikes: list[SynthStrike]
    false_onsets: list[float] = field(default_factory=list)   # thunder-like bursts with no catalogue entry
    distractors: list[tuple[str, float]] = field(default_factory=list)   # ("truck" | "slam", recording s)

    def catalogue(self, source: str = "csv") -> list[dict]:
        """Strike dicts as an imported catalogue would give them (ids assigned)."""
        return assign_ids([make_strike(s.t_utc, s.lat_cat, s.lon_cat, s.sigma_m, None, s.kind, source)
                           for s in self.strikes])

    def catalogue_csv(self) -> str:
        rows = ["time,lat,lon,sigma_m,kind"]
        for s in sorted(self.strikes, key=lambda s: s.t_utc):
            rows.append(f"{s.t_utc:.3f},{s.lat_cat:.6f},{s.lon_cat:.6f},{s.sigma_m:.0f},{s.kind}")
        return "\n".join(rows) + "\n"

    def onset_times(self) -> list[float]:
        """True onset times of the heard strikes (recording s)."""
        return sorted(s.arrival_s for s in self.strikes if s.heard and s.arrival_s is not None)


def _offset(lat: float, lon: float, east_m: float, north_m: float) -> tuple[float, float]:
    return (lat + math.degrees(north_m / EARTH_R),
            lon + math.degrees(east_m / (EARTH_R * math.cos(math.radians(lat)))))


def make_storm(seed: int = 1, lat: float = 28.0395, lon: float = -81.9498, start_utc: float = 1752949200.0,
               duration_s: float = 900.0, n_strikes: int = 40, lead_s: float = 240.0, tail_s: float = 60.0,
               sigma_m: float = 300.0, c: float = 343.0, track_km: tuple[float, float, float, float] = (-12, -8, 10, 9),
               spread_km: float = 4.0, r_min_m: float = 1500.0, audible_m: float = 18000.0,
               missing_frac: float = 0.15, early_bias_s: float = 0.15, kind: str = "cg") -> Storm:
    """A storm cell drifting along ``track_km`` (east0, north0, east1, north1 relative to the
    listener) with ``n_strikes`` strikes between ``start_utc - lead_s`` and the recording end
    plus ``tail_s``. Thunder within ``audible_m`` arriving inside the recording is heard,
    except a random ``missing_frac``; first arrivals come early by an exponential amount
    (mean ``early_bias_s``: the nearest part of the channel)."""
    rng = np.random.default_rng(seed)
    t_a, t_b = start_utc - lead_s, start_utc + duration_s + tail_s
    times = np.sort(rng.uniform(t_a, t_b, n_strikes))
    strikes: list[SynthStrike] = []
    for t in times:
        f = (t - t_a) / (t_b - t_a)
        ce = track_km[0] + f * (track_km[2] - track_km[0])
        cn = track_km[1] + f * (track_km[3] - track_km[1])
        for _ in range(100):
            e, n = ce * 1000 + rng.normal(0, spread_km * 1000), cn * 1000 + rng.normal(0, spread_km * 1000)
            if math.hypot(e, n) >= r_min_m:
                break
        la, lo = _offset(lat, lon, e, n)
        ec, nc = rng.normal(0, sigma_m, 2)
        la_c, lo_c = _offset(la, lo, ec, nc)
        d = math.hypot(e, n)
        s = SynthStrike(float(t), la, lo, la_c, lo_c, sigma_m, d, kind)
        arrival = t + d / c - start_utc - float(rng.exponential(early_bias_s)) if early_bias_s > 0 else t + d / c - start_utc
        if d <= audible_m and 3.0 <= arrival <= duration_s - 8.0 and rng.random() >= missing_frac:
            s.heard, s.arrival_s = True, float(arrival)
        strikes.append(s)
    return Storm(lat, lon, start_utc, duration_s, c, strikes)


# ---- audio -----------------------------------------------------------------------------------

def thunder_wave(sr: int, distance_m: float, rng: np.random.Generator, duration_s: float | None = None) -> np.ndarray:
    """Thunder heard at ``distance_m``: crack (if close) + rumble with rolls, peak-normalised
    to a 1/r amplitude (0.25 at 2 km)."""
    dur = float(duration_s or rng.uniform(7.0, 14.0))
    n = int(dur * sr)
    t = np.arange(n) / sr
    fc = float(np.clip(1500.0 * math.exp(-distance_m / 5000.0), 120.0, 0.45 * sr))
    hp = signal.butter(2, 20.0, btype="highpass", fs=sr, output="sos")
    lp = signal.butter(2, fc, btype="lowpass", fs=sr, output="sos")
    rumble = signal.sosfilt(lp, signal.sosfilt(hp, rng.standard_normal(n)))
    roll = signal.sosfilt(signal.butter(2, [30.0, 120.0], btype="bandpass", fs=sr, output="sos"), rng.standard_normal(n))
    rumble /= rumble.std() + 1e-12
    roll /= roll.std() + 1e-12
    attack = 0.01 + distance_m / 60000.0
    env = (1.0 - np.exp(-t / attack)) * np.exp(-t / (dur / 3.0))
    for _ in range(int(rng.integers(2, 5))):
        tc, w, a = rng.uniform(1.0, 0.6 * dur), rng.uniform(0.3, 1.5), rng.uniform(0.15, 0.5)
        env += a * np.exp(-0.5 * ((t - tc) / w) ** 2) * (1.0 - np.exp(-t / attack))
    x = env * (0.6 * rumble + 0.8 * roll)
    if distance_m < 3000.0:
        nc = int(0.15 * sr)
        crack = rng.standard_normal(nc) * np.exp(-np.arange(nc) / (0.03 * sr))
        x[:nc] += 2.5 * (3000.0 - distance_m) / 3000.0 * crack
    fade = min(n, int(0.5 * sr))
    x[n - fade:] *= np.linspace(1.0, 0.0, fade)
    peak = float(np.max(np.abs(x))) or 1.0
    return (0.25 * 2000.0 / max(distance_m, 300.0)) * x / peak


def rain_noise(n: int, sr: int, rng: np.random.Generator, rms: float = 0.01, drops_per_s: float = 4.0) -> np.ndarray:
    """Rain: mostly high-frequency noise with slow intensity changes, plus sparse drop clicks."""
    hp = signal.butter(2, 400.0, btype="highpass", fs=sr, output="sos")
    x = signal.sosfilt(hp, rng.standard_normal(n))
    x += 0.15 * signal.sosfilt(signal.butter(2, 400.0, btype="lowpass", fs=sr, output="sos"), rng.standard_normal(n))
    x /= x.std() + 1e-12
    # intensity drifting by a few dB over minutes
    k = max(2, int(n / sr / 60) + 2)
    gain = np.interp(np.arange(n), np.linspace(0, n, k), 10 ** (rng.normal(0, 2.0, k) / 20))
    x *= rms * gain
    n_drops = int(drops_per_s * n / sr)
    pos = rng.integers(0, max(1, n - 40), n_drops)
    amp = rms * rng.uniform(2.0, 8.0, n_drops)
    click = np.exp(-np.arange(40) / 6.0) * np.sin(np.arange(40) * 1.9)
    for p, a in zip(pos, amp):
        x[p:p + 40] += a * click
    return x


def wind_noise(n: int, sr: int, rng: np.random.Generator, rms: float = 0.002) -> np.ndarray:
    """Low-frequency wind rumble with slow gusts."""
    lp = signal.butter(2, 60.0, btype="lowpass", fs=sr, output="sos")
    x = signal.sosfilt(lp, rng.standard_normal(n))
    x /= x.std() + 1e-12
    k = max(2, int(n / sr / 8) + 2)
    gust = np.interp(np.arange(n), np.linspace(0, n, k), np.exp(rng.normal(0, 0.4, k)))
    return rms * gust * x


def slow_rumble(sr: int, rng: np.random.Generator, seconds: float = 12.0, amp: float = 0.02) -> np.ndarray:
    """A passing truck: low-frequency noise rising and falling over several seconds."""
    n = int(seconds * sr)
    x = signal.sosfilt(signal.butter(2, [30.0, 150.0], btype="bandpass", fs=sr, output="sos"), rng.standard_normal(n))
    x /= x.std() + 1e-12
    env = np.sin(np.linspace(0, np.pi, n)) ** 2
    return amp * env * x


def door_slam(sr: int, rng: np.random.Generator, amp: float = 0.3) -> np.ndarray:
    """A short broadband bang with no rumble (the detector should reject it)."""
    n = int(0.15 * sr)
    return amp * rng.standard_normal(n) * np.exp(-np.arange(n) / (0.02 * sr))


def render_audio(storm: Storm, sr: int = 8000, seed: int = 2, n_false_thunder: int = 1, n_trucks: int = 1,
                 n_slams: int = 2, rain_rms: float = 0.01) -> np.ndarray:
    """The recording: rain + wind + thunder of every heard strike + distractors.

    Adds ``n_false_thunder`` thunder bursts with no catalogued strike (their times are
    stored in ``storm.false_onsets``), slow rumbles and door slams.
    """
    rng = np.random.default_rng(seed)
    n = int(storm.duration_s * sr)
    x = rain_noise(n, sr, rng, rms=rain_rms) + wind_noise(n, sr, rng)

    def add(burst: np.ndarray, t: float) -> None:
        i = int(round(t * sr))
        if i >= n:
            return
        m = min(len(burst), n - i)
        x[i:i + m] += burst[:m]

    for s in storm.strikes:
        if s.heard and s.arrival_s is not None:
            add(thunder_wave(sr, s.distance_m, rng), s.arrival_s)
    busy = storm.onset_times()

    def free_time(margin: float = 15.0) -> float:
        for _ in range(200):
            t = float(rng.uniform(10.0, storm.duration_s - 20.0))
            if all(abs(t - b) > margin for b in busy):
                return t
        return float(rng.uniform(10.0, storm.duration_s - 20.0))

    storm.false_onsets = []
    storm.distractors = []
    for _ in range(n_false_thunder):
        t = free_time()
        add(thunder_wave(sr, float(rng.uniform(4000, 9000)), rng), t)
        storm.false_onsets.append(t)
        busy.append(t)
    for _ in range(n_trucks):
        t = free_time()
        add(slow_rumble(sr, rng), t)
        busy.append(t)
        storm.distractors.append(("truck", t))
    for _ in range(n_slams):
        t = free_time(8.0)
        add(door_slam(sr, rng), t)
        storm.distractors.append(("slam", t))
    peak = float(np.max(np.abs(x)))
    if peak > 0.95:
        x *= 0.95 / peak
    return x.astype(np.float32)
