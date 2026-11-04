"""Synthetic ENF and mains hum, for tests and the demo case.

Real grid frequency behaves roughly like an Ornstein-Uhlenbeck process: GB data (NESO, 1 s)
has a standard deviation of about 80 mHz, 1 s steps of about 2.5 mHz and an autocorrelation
close to ``exp(-lag / 650 s)``. ``random_walk_enf`` reproduces those statistics, and
``hum_signal`` turns any 1 s frequency series into a hum waveform with a few harmonics.
"""
from __future__ import annotations

import numpy as np


def random_walk_enf(n_s: int, nominal: float = 50.0, seed: int = 0, std_hz: float = 0.05,
                    tau_s: float = 600.0, smooth_s: float = 3.0) -> np.ndarray:
    """``n_s`` one-second samples of a mean-reverting random walk around ``nominal`` (Hz)."""
    rng = np.random.default_rng(seed)
    a = float(np.exp(-1.0 / tau_s))
    # innovations are smoothed slightly: real 1 s steps are positively correlated
    k = max(1, int(round(smooth_s)))
    e = np.convolve(rng.standard_normal(n_s + k), np.ones(k) / np.sqrt(k), mode="valid")[:n_s]
    x = np.empty(n_s)
    x[0] = rng.standard_normal() * std_hz
    sig = std_hz * np.sqrt(1.0 - a * a)
    for i in range(1, n_s):
        x[i] = a * x[i - 1] + sig * e[i]
    return nominal + x


def hum_signal(enf_1s: np.ndarray, sr: int, duration_s: float, offset_s: float = 0.0,
               amplitudes: dict[int, float] | None = None, seed: int = 0,
               clock_ppm: float = 0.0) -> np.ndarray:
    """Mains hum whose fundamental follows ``enf_1s`` (sample ``i`` = frequency at ``i`` s).

    The recording starts ``offset_s`` seconds into ``enf_1s``. ``amplitudes`` maps harmonic
    number to amplitude (default: weak fundamental, strong 2nd and 3rd harmonic, as heard from
    transformers and rectifiers). ``clock_ppm`` simulates a recorder whose sample clock runs
    fast by that many parts per million (the hum then appears slightly lower in frequency).
    """
    amplitudes = amplitudes or {1: 0.3, 2: 1.0, 3: 0.6, 4: 0.2}
    n = int(round(duration_s * sr))
    # true time of each recorded sample, allowing for a recorder clock error
    t_true = offset_s + np.arange(n) / sr / (1.0 + clock_ppm * 1e-6)
    f = np.interp(t_true, np.arange(len(enf_1s), dtype=np.float64), enf_1s)
    phase = 2 * np.pi * np.cumsum(f) / sr / (1.0 + clock_ppm * 1e-6)
    rng = np.random.default_rng(seed)
    y = np.zeros(n)
    for h, a in amplitudes.items():
        y += a * np.cos(h * phase + rng.uniform(0, 2 * np.pi))
    return y


def room_noise(n: int, sr: int, seed: int = 0, white: float = 1.0, rumble: float = 1.0) -> np.ndarray:
    """Background noise: white noise plus low-frequency rumble (integrated noise, high-passed)."""
    rng = np.random.default_rng(seed + 1000)
    w = rng.standard_normal(n) * white
    if rumble > 0:
        b = np.cumsum(rng.standard_normal(n))
        # remove the drift with a simple one-pole high-pass at ~20 Hz
        from scipy.signal import lfilter
        alpha = float(np.exp(-2 * np.pi * 20.0 / sr))
        b = lfilter([1.0, -1.0], [1.0, -alpha], b)
        b *= rumble / (np.std(b) + 1e-12)
        w = w + b
    return w
