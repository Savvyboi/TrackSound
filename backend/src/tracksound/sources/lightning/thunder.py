"""Thunder onset detection in the case audio (mono 4 kHz float32 WAV).

Physics in brief: thunder starts with the sound from the *nearest* part of the lightning
channel (a sharp crack for close strikes, a muffled rise for distant ones, since air absorbs
high frequencies), followed by seconds to tens of seconds of rumble from farther channel
segments. Rumble energy is mostly below 200 Hz.

Method:

1. **Band envelopes** (streamed in chunks, so multi-hour files are fine): Butterworth
   band-pass 20-200 Hz ("low") and 200-1000 Hz ("mid"), mean square per 50 ms hop.
2. **Onset strength** (dB): for the low band and the broadband (20-1000 Hz) sum,
   ``mean level over the next 1 s - background``, where the background is the lower of the
   median over the previous ~6 s and the mean over the last 0.75 s (a step detector that also
   sees new onsets during the decay of earlier rumble). The detection function is the larger
   of the two.
3. **Peak picking**: adaptive threshold ``max(min_db, median + k * MAD)`` (``k`` and
   ``min_db`` fall as ``sensitivity`` rises), non-maximum suppression within ``min_gap_s``.
4. **Thunder checks**: the low band must rise too (rejects birds, speech in the mid band) and
   stay up for over a second (rumble; rejects clicks, rain drops, handling noise).
5. **Refinement** on the raw audio at 10 ms resolution (zero-phase filters): the onset is
   where the level, going back from the main rise, drops to the background (+15 % of the
   rise). ``sigma_s`` grows with the rise time and with low contrast; ``strength`` is the rise
   in dB above background; ``duration_s`` is how long the level stays up.
"""
from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np
import soundfile as sf
from scipy import ndimage, signal

HOP_S = 0.05
BANDS: tuple[tuple[str, float, float], ...] = (("low", 20.0, 200.0), ("mid", 200.0, 1000.0))
CHUNK_S = 120.0
LEVEL_FLOOR = 1e-12            # -120 dBFS: keeps log10 finite in digital silence

BEFORE_S = 6.0                 # background window before a candidate onset
GAP_S = 0.25                   # gap between the background window and the candidate
RECENT_S = 0.75                # "level just before" window (follows decaying rumble)
AFTER_S = 1.0                  # level window after the candidate
SUSTAIN_S = (0.25, 1.5)        # rumble check window after the onset
MAX_DURATION_S = 90.0
FINE_HOP_S = 0.01
STARTUP_S = 1.0                # no onsets in the first second (filters settle, no background yet)

ProgressFn = Callable[[float, str | None], None]


@dataclass
class Envelope:
    """Mean-square band energies at ``hop_s`` resolution; frame i covers samples [i*hop, (i+1)*hop)."""
    hop_s: float
    sr: int
    n_samples: int
    names: list[str]
    lo_hz: list[float]
    hi_hz: list[float]
    ms: np.ndarray                         # (n_bands, n_frames) float32
    _db: np.ndarray | None = field(default=None, repr=False)

    @property
    def n_frames(self) -> int:
        return int(self.ms.shape[1])

    @property
    def times(self) -> np.ndarray:
        """Centre time of each frame (recording s)."""
        return (np.arange(self.n_frames) + 0.5) * self.hop_s

    def db(self) -> np.ndarray:
        if self._db is None:
            self._db = (10.0 * np.log10(self.ms.astype(np.float64) + LEVEL_FLOOR)).astype(np.float32)
        return self._db

    def broadband_db(self) -> np.ndarray:
        return (10.0 * np.log10(self.ms.astype(np.float64).sum(axis=0) + LEVEL_FLOOR)).astype(np.float32)


def _band_sos(lo: float, hi: float, sr: int, order: int = 4) -> np.ndarray:
    hi = min(hi, 0.45 * sr)
    return signal.butter(order, [lo, hi], btype="bandpass", fs=sr, output="sos")


def compute_envelope(path: str | Path, hop_s: float = HOP_S, bands=BANDS, chunk_s: float = CHUNK_S,
                     progress: ProgressFn | None = None) -> Envelope:
    """Band envelopes of a whole audio file, reading it in chunks (constant memory)."""
    with sf.SoundFile(str(path)) as f:
        sr, n = int(f.samplerate), int(f.frames)
        hop = max(1, int(round(hop_s * sr)))
        n_frames = n // hop
        sos = [_band_sos(lo, hi, sr) for _, lo, hi in bands]
        zi = [np.zeros((s.shape[0], 2)) for s in sos]
        out = np.zeros((len(bands), n_frames), dtype=np.float32)
        block = hop * max(1, int(round(chunk_s / (hop / sr))))
        i = 0
        for x in f.blocks(blocksize=block, dtype="float32", always_2d=True):
            x = x.mean(axis=1).astype(np.float64)
            m = min(len(x) // hop, n_frames - i)
            for b, s in enumerate(sos):
                y, zi[b] = signal.sosfilt(s, x, zi=zi[b])
                if m > 0:
                    out[b, i:i + m] = (y[:m * hop] ** 2).reshape(m, hop).mean(axis=1)
            i += max(m, 0)
            if progress and n_frames:
                progress(min(1.0, i / n_frames), "Computing thunder band envelopes")
    return Envelope(hop_s=hop / sr, sr=sr, n_samples=n, names=[b[0] for b in bands],
                    lo_hz=[float(b[1]) for b in bands], hi_hz=[float(min(b[2], 0.45 * sr)) for b in bands], ms=out)


# ---- cache ----------------------------------------------------------------------------------

_cache_locks: dict[str, threading.Lock] = {}
_cache_guard = threading.Lock()


def _lock_for(path: Path) -> threading.Lock:
    with _cache_guard:
        return _cache_locks.setdefault(str(path), threading.Lock())


def load_or_build_envelope(audio_path: Path, cache_path: Path, progress: ProgressFn | None = None) -> Envelope:
    """The envelope, cached in the case folder and rebuilt when the audio file changes."""
    st = audio_path.stat()
    stamp = np.array([st.st_size, st.st_mtime_ns], dtype=np.int64)
    with _lock_for(cache_path):
        if cache_path.exists():
            try:
                with np.load(cache_path, allow_pickle=False) as z:
                    if np.array_equal(z["stamp"], stamp) and float(z["hop_s"]) > 0:
                        return Envelope(hop_s=float(z["hop_s"]), sr=int(z["sr"]), n_samples=int(z["n_samples"]),
                                        names=[str(s) for s in z["names"]], lo_hz=z["lo_hz"].tolist(),
                                        hi_hz=z["hi_hz"].tolist(), ms=z["ms"])
            except (OSError, KeyError, ValueError):
                pass
        env = compute_envelope(audio_path, progress=progress)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache_path.with_name(cache_path.stem + ".tmp.npz")
        np.savez(tmp, stamp=stamp, hop_s=env.hop_s, sr=env.sr, n_samples=env.n_samples,
                 names=np.array(env.names), lo_hz=np.array(env.lo_hz), hi_hz=np.array(env.hi_hz), ms=env.ms)
        tmp.replace(cache_path)
        return env


# ---- onset strength --------------------------------------------------------------------------

def _trailing_median(x: np.ndarray, n_before: int, gap: int) -> np.ndarray:
    """Median of x over [i - gap - n_before, i - gap) for every i (edge-padded)."""
    n_before = max(1, n_before)
    m = ndimage.median_filter(x, size=n_before, mode="nearest")
    # m[j] is centred on j; the window ending at i - gap is centred at i - gap - n_before/2
    shift = gap + n_before // 2 + (n_before % 2)
    out = np.empty_like(x)
    if shift >= len(x):
        out[:] = m[0]
        return out
    out[shift:] = m[:len(x) - shift]
    out[:shift] = m[0]
    return out


def _leading_mean(x: np.ndarray, n_after: int) -> np.ndarray:
    """Mean of x over [i, i + n_after) for every i (edge-padded)."""
    n_after = max(1, n_after)
    c = np.concatenate([[0.0], np.cumsum(x, dtype=np.float64)])
    idx = np.arange(len(x))
    hi = np.minimum(idx + n_after, len(x))
    return ((c[hi] - c[idx]) / np.maximum(hi - idx, 1)).astype(np.float32)


@dataclass
class OnsetStrength:
    d: np.ndarray            # detection function, dB (max of the two below)
    d_low: np.ndarray        # step of the low band
    d_bb: np.ndarray         # step of the broadband level
    before_low: np.ndarray   # background level of the low band (dB)
    before_bb: np.ndarray
    low: np.ndarray          # low-band level (dB)
    bb: np.ndarray           # broadband level (dB)


def _background(x: np.ndarray, hop: float) -> np.ndarray:
    """Level before each frame: the lower of the ~6 s trailing median (robust to clicks) and the
    mean of the last ~0.75 s (follows the decay of earlier rumble, so a new onset during it
    still shows as a step)."""
    nb, gap, nr = int(round(BEFORE_S / hop)), int(round(GAP_S / hop)), int(round(RECENT_S / hop))
    med = _trailing_median(x, nb, gap)
    recent = np.empty_like(x)
    lead = _leading_mean(x, nr)                      # mean over [j, j + nr)
    shift = gap + nr                                 # window [i - gap - nr, i - gap)
    recent[shift:] = lead[:len(x) - shift] if shift < len(x) else lead[:0]
    recent[:min(shift, len(x))] = med[:min(shift, len(x))]
    return np.minimum(med, recent)


def onset_strength(env: Envelope) -> OnsetStrength:
    hop = env.hop_s
    na = int(round(AFTER_S / hop))
    low = env.db()[0]
    bb = env.broadband_db()
    before_low, before_bb = _background(low, hop), _background(bb, hop)
    d_low = _leading_mean(low, na) - before_low
    d_bb = _leading_mean(bb, na) - before_bb
    return OnsetStrength(d=np.maximum(d_low, d_bb), d_low=d_low, d_bb=d_bb, before_low=before_low,
                         before_bb=before_bb, low=low, bb=bb)


def thresholds(sensitivity: float) -> tuple[float, float]:
    """(k for median + k*MAD, absolute minimum dB) for a sensitivity in [0, 1]."""
    s = float(np.clip(sensitivity, 0.0, 1.0))
    return 2.0 + 6.0 * (1.0 - s), 3.0 + 6.0 * (1.0 - s)


# ---- detection ------------------------------------------------------------------------------

def detect_onsets(env: Envelope, audio_path: Path | None = None, sensitivity: float = 0.5, min_gap_s: float = 4.0,
                  progress: ProgressFn | None = None) -> list[dict]:
    """Thunder onset candidates: ``[{t, sigma_s, strength, duration_s}]`` sorted by t.

    ``audio_path`` (the 4 kHz WAV) enables the 10 ms refinement; without it the onset is
    taken from the 50 ms envelope.
    """
    if env.n_frames < 10:
        return []
    os_ = onset_strength(env)
    d = os_.d.astype(np.float64)
    k, min_db = thresholds(sensitivity)
    med = float(np.median(d))
    mad = 1.4826 * float(np.median(np.abs(d - med)))
    thr = max(min_db, med + k * mad)
    hop = env.hop_s
    w = max(3, int(round(1.0 / hop)) | 1)
    is_peak = (d >= thr) & (d >= ndimage.maximum_filter1d(d, size=w, mode="nearest"))
    is_peak[: int(round(STARTUP_S / hop))] = False     # filter start-up transient, and no background yet
    peaks = np.flatnonzero(is_peak)
    order = peaks[np.argsort(-d[peaks], kind="stable")]
    gap_frames = max(1, int(round(max(min_gap_s, 0.5) / hop)))
    accepted: list[int] = []
    taken = np.zeros(env.n_frames, dtype=bool)
    for i in order:
        if taken[max(0, i - gap_frames + 1):i + gap_frames].any():
            continue
        if not _is_thunder(os_, i, thr, hop):
            continue
        accepted.append(int(i))
        taken[i] = True
    accepted.sort()
    out = []
    for n, i in enumerate(accepted):
        if progress and accepted:
            progress(n / len(accepted), f"Refining onset {n + 1} of {len(accepted)}")
        o = _describe(env, os_, _plateau_start(d, i, hop, floor=int(round(STARTUP_S / hop))), audio_path)
        if o["t"] >= STARTUP_S:
            out.append(o)
    # refinement can move onsets; keep the min-gap rule on the final times (keep the stronger)
    out.sort(key=lambda o: o["t"])
    final: list[dict] = []
    for o in out:
        if final and o["t"] - final[-1]["t"] < min_gap_s * 0.5:
            if o["strength"] > final[-1]["strength"]:
                final[-1] = o
            continue
        final.append(o)
    return final


def _plateau_start(d: np.ndarray, i: int, hop: float, frac: float = 0.8, max_back_s: float = 4.0,
                   floor: int = 0) -> int:
    """First frame of the run, ending at peak i, where d stays above frac * d[i].

    The step detector stays high for a while after an onset (until the background window
    fills with rumble), so its maximum can sit seconds after the onset; the start of the
    plateau is the better anchor for the refinement.
    """
    lim = max(floor, i - int(round(max_back_s / hop)))
    j = i
    while j > lim and d[j - 1] >= frac * d[i]:
        j -= 1
    return j


def _is_thunder(os_: OnsetStrength, i: int, thr: float, hop: float) -> bool:
    """The low band must rise and stay up (rumble)."""
    if os_.d_low[i] < max(2.0, 0.4 * thr):
        return False
    a, b = i + int(round(SUSTAIN_S[0] / hop)), i + int(round(SUSTAIN_S[1] / hop))
    seg = os_.low[a:b]
    if len(seg) < max(3, (b - a) // 2):
        return False
    sustained = float(np.median(seg)) - float(os_.before_low[i])
    return sustained >= max(2.0, 0.3 * float(os_.d[i]))


def _describe(env: Envelope, os_: OnsetStrength, i: int, audio_path: Path | None) -> dict:
    hop = env.hop_s
    use_low = os_.d_low[i] >= os_.d_bb[i]
    level = os_.low if use_low else os_.bb
    before = float((os_.before_low if use_low else os_.before_bb)[i])
    t_coarse = float(env.times[i])
    refined = _refine(audio_path, t_coarse, use_low, env) if audio_path is not None else None
    if refined is None:
        rise = float(np.max(level[i:i + int(round(2.0 / hop)) + 1])) - before
        t_on, rise_time = t_coarse - 0.5 * hop, hop
    else:
        t_on, rise, rise_time = refined
    duration = _duration(level, before, rise, int(math.floor(t_on / hop)), hop)
    contrast = max(0.0, (12.0 - rise) / 12.0)
    sigma = float(np.clip(0.06 + 0.25 * rise_time + 0.3 * contrast, 0.06, 2.0))
    return {"t": round(max(0.0, t_on), 3), "sigma_s": round(sigma, 3), "strength": round(float(rise), 2),
            "duration_s": round(duration, 2) if duration is not None else None}


def _duration(level: np.ndarray, before: float, rise: float, i0: int, hop: float) -> float | None:
    """Seconds until the (1 s smoothed) level falls back near the background for over 1 s."""
    i0 = max(0, i0)
    n_max = int(round(MAX_DURATION_S / hop))
    seg = level[i0:i0 + n_max].astype(np.float64)
    if len(seg) < 3:
        return None
    sm = ndimage.uniform_filter1d(seg, size=max(1, int(round(1.0 / hop))), mode="nearest")
    quiet = sm < before + max(3.0, 0.25 * rise)
    run = int(round(1.0 / hop))
    # first index where the level has been quiet for `run` frames
    c = np.convolve(quiet.astype(np.int32), np.ones(run, dtype=np.int32), mode="valid")
    idx = np.flatnonzero(c >= run)
    if len(idx) == 0:
        return float(len(seg) * hop)
    return float(max(idx[0], 1) * hop)


def _refine(audio_path: Path, t_coarse: float, use_low: bool, env: Envelope) -> tuple[float, float, float] | None:
    """Onset time, rise (dB) and rise time from the raw audio around a coarse detection."""
    from ...audio_io import read_segment
    t0 = max(0.0, t_coarse - BEFORE_S)
    x, sr = read_segment(audio_path, t0, t_coarse + 3.0)
    if len(x) < sr * 2:
        return None
    x = x.astype(np.float64)
    lo, hi = (env.lo_hz[0], env.hi_hz[0]) if use_low else (env.lo_hz[0], env.hi_hz[-1])
    sos = _band_sos(lo, hi, sr)
    pad = min(len(x) - 1, int(sr * 0.5))
    y = signal.sosfiltfilt(sos, x, padlen=pad)
    hop = max(1, int(round(FINE_HOP_S * sr)))
    m = len(y) // hop
    lev = 10.0 * np.log10((y[:m * hop] ** 2).reshape(m, hop).mean(axis=1) + LEVEL_FLOOR)
    lev = ndimage.uniform_filter1d(lev, size=5, mode="nearest")
    ft = t0 + (np.arange(m) + 0.5) * hop / sr
    bg_mask = ft < t_coarse - 0.75
    if bg_mask.sum() < 20:
        return None
    bg = float(np.median(lev[bg_mask]))
    win = (ft >= t_coarse - 0.5) & (ft <= t_coarse + 2.0)
    if not win.any():
        return None
    wi = np.flatnonzero(win)
    ip = int(wi[np.argmax(lev[wi])])
    rise = float(lev[ip] - bg)
    if rise < 2.0:
        return None
    half = bg + 0.5 * rise
    j = ip
    while j > 0 and lev[j - 1] >= half:
        j -= 1
    low_level = bg + max(1.5, 0.15 * rise)
    k = j
    floor_i = max(0, int(np.searchsorted(ft, t_coarse - 3.0)))
    while k > floor_i and lev[k - 1] > low_level:
        k -= 1
    # interpolate the crossing between k-1 and k
    if k > 0 and lev[k] != lev[k - 1]:
        frac = float(np.clip((low_level - lev[k - 1]) / (lev[k] - lev[k - 1]), 0.0, 1.0))
        t_on = ft[k - 1] + frac * (ft[k] - ft[k - 1])
    else:
        t_on = ft[k]
    hi80 = bg + 0.8 * rise
    up = np.flatnonzero(lev[k:ip + 1] >= hi80)
    t80 = ft[k + int(up[0])] if len(up) else ft[ip]
    # the centred 5-frame smoothing makes an ideal step cross the 15 % level ~1.5 frames early
    t_on = float(t_on) + 1.5 * hop / sr
    return t_on, rise, float(max(t80 - t_on, hop / sr))


# ---- envelope for the browser ---------------------------------------------------------------

def pooled_envelope(env: Envelope, t0: float | None = None, t1: float | None = None,
                    max_points: int = 2000) -> dict:
    """``{t, bands: [{name, lo_hz, hi_hz, db}], onset_strength}`` max-pooled to at most max_points."""
    times = env.times
    t0 = 0.0 if t0 is None else max(0.0, float(t0))
    t1 = env.n_frames * env.hop_s if t1 is None else float(t1)
    a = int(np.searchsorted(times, t0, side="left"))
    b = int(np.searchsorted(times, t1, side="right"))
    max_points = int(max(1, max_points))
    os_ = onset_strength(env) if env.n_frames else None
    db = env.db()
    if b <= a or os_ is None:
        return {"t": [], "bands": [{"name": n, "lo_hz": lo, "hi_hz": hi, "db": []}
                                   for n, lo, hi in zip(env.names, env.lo_hz, env.hi_hz)], "onset_strength": []}
    step = int(math.ceil((b - a) / max_points))
    n = int(math.ceil((b - a) / step))

    def pool(x: np.ndarray) -> list[float]:
        seg = x[a:b].astype(np.float64)
        padn = n * step - len(seg)
        if padn:
            seg = np.concatenate([seg, np.full(padn, -np.inf)])
        return np.round(seg.reshape(n, step).max(axis=1), 2).tolist()

    tt = times[a:b]
    starts = tt[::step][:n]
    ends = np.append(tt[step - 1::step], tt[-1])[:n]
    t_out = np.round(0.5 * (starts + ends), 3).tolist()
    return {"t": t_out,
            "bands": [{"name": nm, "lo_hz": lo, "hi_hz": hi, "db": pool(db[k])}
                      for k, (nm, lo, hi) in enumerate(zip(env.names, env.lo_hz, env.hi_hz))],
            "onset_strength": pool(os_.d)}
