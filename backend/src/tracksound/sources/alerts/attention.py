"""Attention signals that follow SAME headers, used as supporting evidence.

- EAS: 853 Hz + 960 Hz together, 8-25 s (47 CFR 11.31(a)(2), (c)).
- NOAA Weather Radio warning alarm tone: 1050 Hz, 8-10 s, sent 1-3 s after the third
  header (NWS Instruction 10-1712 A.1.3).

Each ~0.11 s frame gets the power at each tone divided by the median power at eight nearby
frequencies (±30-75 Hz), which picks out steady tones even under noise or music. On synthetic
music the two-tone test never ran for 2 s, while a two-tone signal 10 dB below the music was
still found. A lone 1050 Hz tone is common in music (C6 is 1046.5 Hz), so it only counts when it
follows a decoded header.
"""
from __future__ import annotations

import numpy as np

from .same import bandpass

FRAME = 1225                                   # samples at 11025 Hz: 0.111 s, 9 Hz resolution
TONES = (853.0, 960.0, 1050.0)
REF_OFFSETS = (-75.0, -60.0, -45.0, -30.0, 30.0, 45.0, 60.0, 75.0)
PROMINENCE = 20.0                              # tone at least 13 dB above its neighbourhood
MIN_RUN_S = 4.0                                # music gives two-tone runs of < 2 s


def _kernel(fs: float) -> np.ndarray:
    freqs = [f for t in TONES for f in (t, *(t + o for o in REF_OFFSETS))]
    n = np.arange(FRAME)
    return np.exp(-2j * np.pi * np.outer(n, freqs) / fs) * np.hanning(FRAME)[:, None]


_KERNELS: dict[int, np.ndarray] = {}


def prominence(x: np.ndarray, fs: float) -> np.ndarray:
    """(n_frames, 3) tone-to-neighbourhood power ratios for 853, 960 and 1050 Hz."""
    k = _KERNELS.setdefault(int(fs), _kernel(fs))
    n = len(x) // FRAME
    if n == 0:
        return np.zeros((0, len(TONES)))
    p = np.abs(x[: n * FRAME].reshape(n, FRAME) @ k) ** 2
    p = p.reshape(n, len(TONES), 1 + len(REF_OFFSETS))
    return p[:, :, 0] / (np.median(p[:, :, 1:], axis=2) + 1e-30)


def block_prominence(block: np.ndarray, fs: float, own_lo: int, own_hi: int) -> np.ndarray:
    """Prominence frames for block[own_lo:own_hi], band-passed with the whole block as context."""
    xb = bandpass(np.asarray(block, dtype=np.float64), fs, 600.0, 1400.0)
    return prominence(xb[own_lo:own_hi], fs)


def segments(prom: np.ndarray, fs: float) -> list[dict]:
    """Runs of EAS (853 + 960 Hz) or NWS (1050 Hz) tones of at least MIN_RUN_S.

    Returns [{kind: "eas" | "nws", t, duration_s}] with t in seconds from frame 0.
    """
    dt = FRAME / fs
    out = []
    for kind, on in (("eas", (prom[:, 0] > PROMINENCE) & (prom[:, 1] > PROMINENCE)),
                     ("nws", (prom[:, 2] > PROMINENCE) & ~((prom[:, 0] > PROMINENCE) & (prom[:, 1] > PROMINENCE)))):
        on = on.copy()
        gaps = np.flatnonzero(~on[1:-1] & on[:-2] & on[2:]) + 1   # bridge one-frame dropouts
        on[gaps] = True
        edges = np.flatnonzero(np.diff(np.concatenate(([0], on.astype(np.int8), [0]))))
        for a, b in zip(edges[::2], edges[1::2]):
            if (b - a) * dt >= MIN_RUN_S:
                out.append({"kind": kind, "t": round(a * dt, 2), "duration_s": round((b - a) * dt, 1)})
    return sorted(out, key=lambda s: s["t"])
