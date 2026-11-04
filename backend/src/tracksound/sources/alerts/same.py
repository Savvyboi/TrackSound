"""SAME (EAS / NOAA Weather Radio) AFSK demodulator: finds data bursts and their soft bits.

Signal (47 CFR 11.31(a)(1); NWS Instruction 10-1712 A.1): 520.83 bit/s, mark (1) 2083.3 Hz,
space (0) 1562.5 Hz, phase continuous, 8-bit bytes sent least significant bit first with no
start or stop bits. Every burst starts with 16 preamble bytes of 0xAB, then either a header
("ZCZC-...") or an end-of-message ("NNNN").

Pipeline for one block of audio at ``FS`` Hz:

1. ``soft_signal``: band-pass 1.1-2.7 kHz, mix to baseband at the mark and space tones
   (I/Q), integrate over one bit (boxcar), and form ``(|mark| - |space|) / local RMS``. For a
   window starting on a bit boundary this is about +1 (mark) or -1 (space) for a clean
   burst, and noise of standard deviation ~0.46 where there is no AFSK.
2. ``find_candidates``: correlate with the preamble bit pattern at three bit rates (the
   template is zero-mean, so steady tones in music cancel).
3. ``lock_burst``: for each candidate, search the sub-bit timing offset and bit period that
   maximise the decision margins ``sum |soft|``; find the payload marker (end of preamble +
   "ZCZC" or "NNNN") by soft correlation; then follow the payload with a phase-tracking loop
   (tolerates clock drift) and sample one soft bit per bit period.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.signal import butter, correlate, sosfiltfilt

FS = 11025                         # decode rate: Nyquist 5.5 kHz, ~21.2 samples per bit
BAUD = 1 / 0.00192                 # 520.833 bit/s
F_MARK = 4 * BAUD                  # 2083.33 Hz = 4 cycles per bit
F_SPACE = 3 * BAUD                 # 1562.5 Hz = 3 cycles per bit
PREAMBLE_BYTE = 0xAB
PREAMBLE_BITS = 128
MAX_PAYLOAD_BYTES = 252            # 268-byte limit (NWS 10-1712 A.1.2) minus the preamble
BAUD_HYPOTHESES = (0.99, 1.0, 1.01)
DETECT_THRESHOLD = 0.3             # preamble correlation (1 = clean); noise std ~0.07
MARKER_THRESHOLD = 0.35            # mean soft agreement with preamble tail + "ZCZC"/"NNNN" (noise std ~0.06)


@dataclass
class Burst:
    """One decoded data burst."""
    kind: str                      # "header" or "eom"
    t: float                       # recording time of the first preamble bit, s
    t_payload: float               # recording time of the first payload bit, s
    period: float                  # measured bit period, s
    soft: np.ndarray               # soft bits from the first payload bit (header: up to 252 bytes)
    n_bytes: int                   # payload bytes that still look like AFSK (energy-based length)
    quality: float                 # mean |soft| over the preamble tail and marker (~1 clean, ~0.4 noise)
    marker_score: float            # mean soft agreement with the preamble tail + marker (~1 clean)
    weight: float = 1.0            # A / sigma^2 of the soft bits over the 64 known bits (for combining)
    extra: dict = field(default_factory=dict)


def bits_of(data: bytes) -> np.ndarray:
    """Bytes as bits, least significant bit first."""
    a = np.frombuffer(bytes(data), dtype=np.uint8)
    return ((a[:, None] >> np.arange(8)) & 1).astype(np.int8).ravel()


def _boxcar(z: np.ndarray, n: int) -> np.ndarray:
    """Sum of z over the window [i, i + n) for every i (length len(z) - n + 1)."""
    c = np.concatenate(([0], np.cumsum(z)))
    return c[n:] - c[:-n]


def _moving_mean(x: np.ndarray, n: int) -> np.ndarray:
    """Centred moving mean of length n, same length as x (edges use partial windows)."""
    c = np.concatenate(([0.0], np.cumsum(x, dtype=np.float64)))
    i = np.arange(len(x))
    lo = np.clip(i - n // 2, 0, len(x))
    hi = np.clip(i + n - n // 2, 0, len(x))
    return (c[hi] - c[lo]) / np.maximum(hi - lo, 1)


_SOS_CACHE: dict[tuple[int, float, float], np.ndarray] = {}


def bandpass(x: np.ndarray, fs: float, lo: float, hi: float) -> np.ndarray:
    key = (int(fs), lo, hi)
    if key not in _SOS_CACHE:
        _SOS_CACHE[key] = butter(4, [lo, hi], btype="band", fs=fs, output="sos")
    return sosfiltfilt(_SOS_CACHE[key], x)


def soft_signal(x: np.ndarray, fs: float = FS) -> tuple[np.ndarray, np.ndarray]:
    """Soft bit and tone energy for a one-bit window starting at each sample.

    Returns (soft, energy), each of length len(x) - L + 1 (L = samples per bit):
    soft = (|mark| - |space|) / sqrt(local mean of |mark|^2 + |space|^2 over 16 bits),
    energy = |mark|^2 + |space|^2 (absolute, for telling burst from background).
    """
    n_bit = int(round(fs / BAUD))
    xb = bandpass(np.asarray(x, dtype=np.float64), fs, 1100.0, 2700.0)
    w = 2 * np.pi * np.arange(len(xb)) / fs
    am = np.abs(_boxcar(xb * np.exp(-1j * F_MARK * w), n_bit))
    asp = np.abs(_boxcar(xb * np.exp(-1j * F_SPACE * w), n_bit))
    e = am * am + asp * asp
    p = _moving_mean(e, 16 * n_bit)
    floor = 1e-9 * float(np.median(p)) + 1e-30
    return ((am - asp) / np.sqrt(p + floor)).astype(np.float32), e.astype(np.float32)


def sample(s: np.ndarray, t: np.ndarray) -> np.ndarray:
    """Linear interpolation of s at fractional sample positions t (0 outside the array)."""
    i = np.floor(t).astype(np.int64)
    f = t - i
    ok = (i >= 0) & (i + 1 < len(s))
    i = np.where(ok, i, 0)
    return np.where(ok, s[i] * (1 - f) + s[np.minimum(i + 1, len(s) - 1)] * f, 0.0)


def _template(bits: np.ndarray, period: float, mask: np.ndarray | None = None) -> np.ndarray:
    """Sparse correlation kernel: zero-mean ±1 weights at the bit start positions."""
    w = (2.0 * bits - 1.0)
    if mask is not None:
        w = w * mask
    used = w != 0
    w[used] -= w[used].mean()
    k = np.zeros(int(np.ceil(period * (len(bits) - 1))) + 1)
    k[np.round(np.arange(len(bits)) * period).astype(int)] += w
    return k / np.sum(w * w)


def find_candidates(s: np.ndarray, fs: float = FS, threshold: float = DETECT_THRESHOLD) -> list[tuple[int, float, float]]:
    """Sample positions where a preamble (6 bytes of 0xAB) likely starts.

    Returns (index, bit period in samples, score), at most one per 0.25 s, best first.
    """
    bits = bits_of(bytes([PREAMBLE_BYTE] * 6))
    best = np.full(len(s), -np.inf, dtype=np.float32)
    per = np.zeros(len(s), dtype=np.float32)
    for r in BAUD_HYPOTHESES:
        period = fs / (BAUD * r)
        k = _template(bits, period)
        if len(k) >= len(s):
            continue
        c = correlate(s, k, mode="valid", method="fft").astype(np.float32)
        c = np.concatenate((c, np.full(len(s) - len(c), -np.inf, np.float32)))
        better = c > best
        best[better] = c[better]
        per[better] = period
    out = []
    idx = np.flatnonzero(best > threshold)
    if len(idx) == 0:
        return out
    order = idx[np.argsort(best[idx])[::-1]]
    taken = np.zeros(len(s), bool)
    gap = int(0.25 * fs)
    for i in order:
        if taken[i]:
            continue
        taken[max(0, i - gap): i + gap] = True
        out.append((int(i), float(per[i]), float(best[i])))
    return out


def _margin_search(s: np.ndarray, t0: float, period: float, n_bits: int, k0: int,
                   phase_span: float, phase_steps: int, rel_span: float, rel_steps: int) -> tuple[float, float]:
    """Grid search of (t0 offset, period) maximising sum |soft| over bits k0..k0+n_bits-1."""
    k = np.arange(k0, k0 + n_bits, dtype=np.float64)
    offs = np.linspace(-phase_span, phase_span, phase_steps)
    rels = np.linspace(-rel_span, rel_span, rel_steps) if rel_steps > 1 else np.zeros(1)
    best = (-1.0, t0, period)
    for rel in rels:
        p = period * (1 + rel)
        pos = t0 + offs[:, None] + k[None, :] * p
        m = np.abs(sample(s, pos.ravel())).reshape(pos.shape).sum(axis=1)
        j = int(np.argmax(m))
        if m[j] > best[0]:
            best = (float(m[j]), t0 + offs[j], p)
    return best[1], best[2]


_MARKERS = {"header": b"ZCZC", "eom": b"NNNN"}


def _marker_templates() -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """±1 patterns for 4 preamble bytes + marker, with the eighth bit of marker bytes masked."""
    out = {}
    for kind, marker in _MARKERS.items():
        bits = bits_of(bytes([PREAMBLE_BYTE] * 4) + marker)
        mask = np.ones(len(bits))
        mask[32 + 7::8] = 0.0                    # bit 7 of each marker byte may be 0 or 1 (11.31(a)(1))
        out[kind] = (2.0 * bits - 1.0, mask)
    return out


MARKER_TEMPLATES = _marker_templates()


def _find_marker(b: np.ndarray) -> tuple[str, int, float]:
    """(kind, index of the first payload bit, agreement) for the best marker position in b."""
    best = ("", -1, -1.0)
    for kind, (sign, mask) in MARKER_TEMPLATES.items():
        n = len(sign)
        if len(b) < n:
            continue
        win = np.lib.stride_tricks.sliding_window_view(b, n)
        agree = win @ (sign * mask) / mask.sum()     # absolute: ~1 clean burst, 0 ± 0.06 noise, 0 silence
        j = int(np.argmax(agree))
        if agree[j] > best[2]:
            best = (kind, j + 32, float(agree[j]))
    return best


def _track(s: np.ndarray, t0: float, period: float, n_bits: int, block: int = 16, gain: float = 0.25) -> np.ndarray:
    """Bit start positions for n_bits from t0, following slow timing drift.

    Per block, the offset (within ±period/4) that maximises the margins over the block and
    the one before it is measured, and a fraction ``gain`` of it is applied.
    """
    pos = np.empty(n_bits)
    offs = np.linspace(-0.25 * period, 0.25 * period, 17)
    k = np.arange(-block, block, dtype=np.float64)
    t = t0
    for b0 in range(0, n_bits, block):
        m = np.abs(sample(s, (t + offs[:, None] + k[None, :] * period).ravel())).reshape(len(offs), -1)
        if b0 == 0:
            m = m[:, block:]                       # nothing before the first block
        t += gain * offs[int(np.argmax(m.sum(axis=1)))]
        n = min(block, n_bits - b0)
        pos[b0:b0 + n] = t + np.arange(n) * period
        t += block * period
    return pos


def _afsk_bytes(e: np.ndarray, pos: np.ndarray, t_start: float, period: float) -> int:
    """Number of leading payload bytes whose tone energy is still burst-like.

    The burst level is the median energy over the first 8 payload bytes, the background
    the median over the 0.3 s before the preamble; a byte ends the burst when it and the next
    are closer to the background (geometric midpoint).
    """
    nb = len(pos) // 8
    per_byte = np.median(sample(e, pos[: nb * 8]).reshape(nb, 8), axis=1)
    burst = float(np.median(per_byte[:8]))
    before = sample(e, t_start - np.arange(1, int(0.3 * FS / period) + 1) * period)
    bg = float(np.median(before[before > 0])) if np.any(before > 0) else 0.0
    if burst <= 0:
        return 0
    thr = np.sqrt(burst * max(bg, burst * 1e-6))
    low = per_byte < thr
    for i in range(nb - 1):
        if low[i] and low[i + 1]:
            return i
    return nb


def lock_burst(s: np.ndarray, e: np.ndarray, cand: tuple[int, float, float], fs: float = FS,
               t_offset: float = 0.0) -> Burst | None:
    """Turn a preamble candidate into a Burst (or None if no payload marker is found).

    ``s`` and ``e`` are the soft signal and tone energy of a block that starts at recording
    time ``t_offset``.
    """
    i0, period, _ = cand
    # 1. timing over the candidate preamble stretch and what follows it
    t0, period = _margin_search(s, float(i0), period, 176, 0, 0.5 * period, 17, 0.006, 25)
    # 2. payload marker: the preamble may have started up to 10 bytes before the candidate
    k = np.arange(-80, 176 + 32)
    b = sample(s, t0 + k * period)
    kind, m, agree = _find_marker(b)
    if agree < MARKER_THRESHOLD:
        return None
    t_pay = t0 + k[m] * period
    if kind == "eom":
        soft = sample(s, t_pay + np.arange(32) * period)
        n_bytes = 4
    else:
        # 3. refine the period over the preamble tail and ~40 payload bytes, then track
        t_pay, period = _margin_search(s, t_pay, period, 32 + 320, -32, 0.25 * period, 17, 0.003, 31)
        pos = _track(s, t_pay, period, MAX_PAYLOAD_BYTES * 8)
        soft = sample(s, pos)
        n_bytes = _afsk_bytes(e, pos, t_pay - PREAMBLE_BITS * period, period)
    if t_pay + 8 * period < 0:
        return None
    pre = sample(s, t_pay + np.arange(-32, 32) * period)
    sign, mask = MARKER_TEMPLATES[kind]
    u = (pre * sign)[mask > 0]                     # known bits: mean is the signal level A
    weight = float(max(np.mean(u), 0.0) / max(np.var(u), 1e-4))
    t_start = t_pay - PREAMBLE_BITS * period
    return Burst(kind=kind, t=t_offset + t_start / fs, t_payload=t_offset + t_pay / fs, period=period / fs,
                 soft=np.asarray(soft, dtype=np.float32), n_bytes=n_bytes, quality=float(np.mean(np.abs(pre))),
                 marker_score=agree, weight=weight)


def find_bursts(x: np.ndarray, fs: float = FS, t_offset: float = 0.0, own: tuple[float, float] | None = None) -> list[Burst]:
    """All bursts whose candidate preamble lies in ``own`` (block-relative seconds; default all)."""
    s, e = soft_signal(x, fs)
    lo, hi = (0, len(s)) if own is None else (int(own[0] * fs), int(own[1] * fs))
    found: list[Burst] = []
    for cand in find_candidates(s, fs):
        if not lo <= cand[0] < hi:
            continue
        t_cand = t_offset + cand[0] / fs
        if any(bu.t - 0.05 <= t_cand <= bu.t_payload + bu.n_bytes * 8 * bu.period for bu in found):
            continue                               # inside a burst already decoded
        bu = lock_burst(s, e, cand, fs, t_offset)
        if bu is not None and not any(abs(bu.t_payload - o.t_payload) < 0.05 for o in found):
            found.append(bu)
    return sorted(found, key=lambda bu: bu.t)
