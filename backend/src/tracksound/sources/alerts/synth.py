"""Synthetic SAME alerts (EAS / NOAA Weather Radio), for tests and demos.

The encoder follows 47 CFR 11.31(a) and NWS Instruction 10-1712 Appendix A: 16 preamble
bytes of 0xAB, then 8-bit ASCII bytes (eighth bit 0) sent least significant bit first with no
start or stop bits, as phase-continuous AFSK at 520.83 bit/s (mark 2083.3 Hz = 1,
space 1562.5 Hz = 0). An alert is three header bursts one second apart, an attention signal,
a message, and three end-of-message bursts (preamble + "NNNN") one second apart.
"""
from __future__ import annotations

import numpy as np

from .same import BAUD, F_MARK, F_SPACE, PREAMBLE_BYTE, bits_of


def burst_bits(text: str, preamble_bytes: int = 16) -> np.ndarray:
    """Preamble then the ASCII text, as bits in transmission order."""
    return bits_of(bytes([PREAMBLE_BYTE] * preamble_bytes) + text.encode("ascii"))


def afsk(bits: np.ndarray, sr: int, speed: float = 1.0, baud_error: float = 0.0, phase0: float = 0.0) -> np.ndarray:
    """Phase-continuous AFSK for ``bits`` at sample rate ``sr``.

    speed: playback speed factor (scales bit rate and tones together).
    baud_error: relative error of the bit clock alone, e.g. 0.004 for an encoder that rounds
    the samples per bit.
    """
    baud = BAUD * speed * (1 + baud_error)
    n = int(np.ceil(len(bits) * sr / baud))
    idx = np.minimum((np.arange(n) * baud / sr).astype(np.int64), len(bits) - 1)
    f = np.where(bits[idx] == 1, F_MARK, F_SPACE) * speed
    phase = phase0 + 2 * np.pi * np.cumsum(f) / sr
    return np.sin(phase).astype(np.float32)


def tones(freqs: tuple[float, ...], seconds: float, sr: int) -> np.ndarray:
    t = np.arange(int(seconds * sr)) / sr
    return (sum(np.sin(2 * np.pi * f * t) for f in freqs) / len(freqs)).astype(np.float32)


def alert_waveform(header: str, sr: int = 44100, attention: str = "eas", attention_s: float = 8.0,
                   message_s: float = 3.0, gap_s: float = 1.0, speed: float = 1.0, baud_error: float = 0.0,
                   seed: int = 0) -> tuple[np.ndarray, dict]:
    """A complete alert at full scale: 3 headers, attention signal, message, 3 EOMs.

    attention: "eas" (853 + 960 Hz), "nws" (1050 Hz) or "none". The message is a
    speech-like noise. Returns (samples, info) where info gives burst start times (s).
    """
    rng = np.random.default_rng(seed)
    gap = np.zeros(int(gap_s * sr), np.float32)
    parts, t, info = [], 0.0, {"headers": [], "eoms": [], "attention": None}

    def add(x: np.ndarray, key: str | None = None):
        nonlocal t
        if key:
            info[key].append(t)
        parts.append(x)
        t += len(x) / sr

    for _ in range(3):
        add(afsk(burst_bits(header), sr, speed, baud_error, rng.uniform(0, 2 * np.pi)), "headers")
        add(gap)
    if attention != "none":
        freqs = (853.0, 960.0) if attention == "eas" else (1050.0,)
        info["attention"] = (t, attention_s)
        add(tones(tuple(f * speed for f in freqs), attention_s, sr))
        add(gap)
    add(speech_like(message_s, sr, rng))
    add(gap)
    for _ in range(3):
        add(afsk(burst_bits("NNNN"), sr, speed, baud_error, rng.uniform(0, 2 * np.pi)), "eoms")
        add(gap)
    return np.concatenate(parts), info


def speech_like(seconds: float, sr: int, rng: np.random.Generator) -> np.ndarray:
    """Noise shaped by three wandering formants and a ~4 Hz syllable envelope."""
    from scipy.signal import butter, sosfilt
    n = int(seconds * sr)
    t = np.arange(n) / sr
    out = np.zeros(n)
    for centre in (500.0, 1500.0, 2500.0):
        lo, hi = centre * 0.8, centre * 1.25
        out += sosfilt(butter(2, [lo, hi], btype="band", fs=sr, output="sos"), rng.standard_normal(n)) * (500 / centre)
    env = np.clip(np.sin(2 * np.pi * 4.0 * t + rng.uniform(0, 6)) + 0.3, 0, None)
    out *= env
    return (out / (np.max(np.abs(out)) + 1e-12) * 0.5).astype(np.float32)


def music_like(seconds: float, sr: int, seed: int = 1) -> np.ndarray:
    """A crude band: chords of harmonic notes (some partials land on the SAME tones), a bass
    line and noisy drum hits. Peak about 0.5."""
    rng = np.random.default_rng(seed)
    n = int(seconds * sr)
    t = np.arange(n) / sr
    out = np.zeros(n)
    beat = 0.5
    for start in np.arange(0, seconds, beat * 2):
        a, b = int(start * sr), min(n, int((start + beat * 2) * sr))
        tt = t[a:b] - start
        root = 110.0 * 2 ** (rng.integers(0, 12) / 12)
        env = np.exp(-tt * 1.5)
        for k in (1.0, 1.25, 1.5, 2.0):                     # a major chord plus octave
            for h in range(1, 12):                          # harmonics up to several kHz
                f = root * k * h
                if f < sr / 2:
                    out[a:b] += env * np.sin(2 * np.pi * f * tt + rng.uniform(0, 6)) * 0.4 / h
        out[a:b] += 0.6 * env * np.sin(2 * np.pi * root / 2 * tt)
    for start in np.arange(0, seconds, beat):               # drums: decaying noise bursts
        a = int(start * sr)
        m = min(n - a, int(0.15 * sr))
        out[a:a + m] += rng.standard_normal(m) * np.exp(-np.arange(m) / (0.03 * sr)) * 0.8
    return (out / (np.max(np.abs(out)) + 1e-12) * 0.5).astype(np.float32)


def mix(signal: np.ndarray, background: np.ndarray, at_s: float, sr: int, gain: float = 1.0) -> np.ndarray:
    """Add ``signal * gain`` into a copy of ``background`` starting at ``at_s`` seconds."""
    out = background.astype(np.float32).copy()
    a = int(round(at_s * sr))
    b = min(len(out), a + len(signal))
    out[a:b] += gain * signal[: b - a]
    return out
