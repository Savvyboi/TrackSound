"""Matching an ENF trace against a reference grid-frequency series.

Both series are on a 1 s grid with NaN for missing values. The reference is first smoothed with
the same Hann window the extractor used (so both see the grid frequency through the same
"lens"), then both are high-passed (a masked moving average is subtracted) so the correlation
keys on the fine structure of the frequency wander rather than on slow drifts that many
stretches of the reference share.

For every candidate start time the Pearson correlation is computed over the samples where both
series are valid. All offsets are evaluated at once with FFT cross-correlations of the masked
sums (n, sum x, sum y, sum x^2, sum y^2, sum xy), so an hour of recording against a month of
reference takes about a second.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from scipy.fft import irfft, next_fast_len, rfft


@dataclass(frozen=True)
class MatchParams:
    smooth_s: float = 8.0          # Hann window applied to the reference (= extraction window)
    highpass_s: float = 120.0      # length of the moving average removed from both series (0: off)
    min_overlap_s: float = 120.0   # an offset needs at least this many common valid seconds
    min_overlap_frac: float = 0.5  # ... and this fraction of the recording's valid seconds
    exclude_s: float = 60.0        # peaks closer than this are the same peak
    n_top: int = 5
    max_overlay_points: int = 4000


@dataclass
class MatchResult:
    start_utc: float | None
    corr: float | None
    z: float | None
    top: list[dict]
    overlay: dict | None
    n_offsets: int
    overlap_s: int
    notes: list[str] = field(default_factory=list)


# ---- masked filters ------------------------------------------------------------------------------

def masked_smooth(x: np.ndarray, kernel: np.ndarray) -> np.ndarray:
    """Weighted moving average ignoring NaN (NaN where the window holds no data)."""
    ok = np.isfinite(x)
    xv = np.where(ok, x, 0.0)
    num = _conv_same(xv, kernel)
    den = _conv_same(ok.astype(float), kernel)
    with np.errstate(invalid="ignore", divide="ignore"):
        out = num / den
    out[den < 0.5 * kernel.sum() * 0.2] = np.nan
    return out


def _conv_same(x: np.ndarray, k: np.ndarray) -> np.ndarray:
    n = len(x) + len(k) - 1
    nf = next_fast_len(n, real=True)
    y = irfft(rfft(x, nf) * rfft(k, nf), nf)[:n]
    a = (len(k) - 1) // 2
    return y[a:a + len(x)]


def smooth_like_extractor(ref: np.ndarray, window_s: float) -> np.ndarray:
    """What the extractor would see: the reference averaged over its Hann analysis window."""
    L = max(1, int(round(window_s)))
    if L <= 1:
        return ref.astype(float)
    k = np.hanning(L + 2)[1:-1]
    return masked_smooth(ref.astype(float), k / k.sum())


def highpass(x: np.ndarray, length_s: float) -> np.ndarray:
    """Subtract a centred masked moving average of ``length_s`` samples (keeps NaN as NaN)."""
    if length_s <= 0:
        return x - np.nanmean(x)
    L = int(round(length_s)) | 1
    trend = masked_smooth(x, np.ones(L))
    out = x - trend
    out[~np.isfinite(x)] = np.nan
    return out


def preprocess(x: np.ndarray, p: MatchParams, is_reference: bool) -> np.ndarray:
    y = smooth_like_extractor(x, p.smooth_s) if is_reference else x.astype(float)
    return highpass(y, p.highpass_s)


# ---- correlation over all offsets ----------------------------------------------------------------

def masked_xcorr(rec: np.ndarray, ref: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Pearson correlation of ``rec`` against ``ref[L:L+len(rec)]`` for every L in
    ``[-(len(rec)-1), len(ref)-1]`` over the jointly valid samples. Returns (corr, n_common),
    both of length ``len(ref) + len(rec) - 1``; index i is offset ``L = i - (len(rec) - 1)``."""
    N, M = len(rec), len(ref)
    mr, mg = np.isfinite(rec), np.isfinite(ref)
    r = np.where(mr, rec, 0.0)
    g = np.where(mg, ref, 0.0)
    # centre for numerical stability (correlation is invariant to it)
    r = np.where(mr, r - (r[mr].mean() if mr.any() else 0.0), 0.0)
    g = np.where(mg, g - (g[mg].mean() if mg.any() else 0.0), 0.0)
    nf = next_fast_len(N + M - 1, real=True)
    # correlation = convolution with the reversed recording
    R = [rfft(a[::-1], nf) for a in (mr.astype(float), r, r * r)]
    G = [rfft(a, nf) for a in (mg.astype(float), g, g * g)]

    def xc(Gi, Ri):
        return irfft(Gi * Ri, nf)[:N + M - 1]

    n = np.rint(xc(G[0], R[0]))
    sx = xc(G[0], R[1])
    sy = xc(G[1], R[0])
    sxx = xc(G[0], R[2])
    syy = xc(G[2], R[0])
    sxy = xc(G[1], R[1])
    with np.errstate(invalid="ignore", divide="ignore"):
        cov = sxy - sx * sy / n
        vx = sxx - sx * sx / n
        vy = syy - sy * sy / n
        c = cov / np.sqrt(vx * vy)
    bad = (n < 3) | ~(vx > 1e-18) | ~(vy > 1e-18)
    c[bad] = np.nan
    return np.clip(c, -1.0, 1.0), n.astype(int)


def distinct_peaks(c: np.ndarray, exclude: int, k: int) -> list[int]:
    """Indices of the ``k`` highest values at least ``exclude`` samples apart."""
    work = np.where(np.isfinite(c), c, -np.inf)
    out: list[int] = []
    for _ in range(k):
        i = int(np.argmax(work))
        if not np.isfinite(work[i]):
            break
        out.append(i)
        work[max(0, i - exclude):i + exclude + 1] = -np.inf
    return out


def _refine(c: np.ndarray, i: int) -> float:
    """Sub-sample position of the peak at ``i`` (quadratic through its neighbours)."""
    if 0 < i < len(c) - 1 and np.isfinite(c[i - 1]) and np.isfinite(c[i + 1]):
        a, b, d = c[i - 1], c[i], c[i + 1]
        den = a - 2 * b + d
        if den < 0:
            return float(i + np.clip(0.5 * (a - d) / den, -0.5, 0.5))
    return float(i)


# ---- the match --------------------------------------------------------------------------------------

def match_trace(rec_f: np.ndarray, rec_t0: float, ref_f: np.ndarray, ref_t0_utc: float,
                t0_utc: float, t1_utc: float, p: MatchParams = MatchParams()) -> MatchResult:
    """Best recording start time in [t0_utc, t1_utc].

    ``rec_f``: ENF per second, sample k at recording time ``rec_t0 + k``.
    ``ref_f``: reference per second, sample j at UTC ``ref_t0_utc + j``.
    """
    notes: list[str] = []
    rec = preprocess(np.asarray(rec_f, float), p, is_reference=False)
    ref = preprocess(np.asarray(ref_f, float), p, is_reference=True)
    n_valid = int(np.isfinite(rec).sum())
    need = max(p.min_overlap_s, p.min_overlap_frac * n_valid)
    if n_valid < p.min_overlap_s:
        return MatchResult(None, None, None, [], None, 0, 0,
                           [f"Only {n_valid} s of usable ENF in the recording; at least {p.min_overlap_s:.0f} s are needed."])
    c, n = masked_xcorr(rec, ref)
    N = len(rec)
    offs = np.arange(len(c)) - (N - 1)                 # reference index of recording sample 0
    start = ref_t0_utc + offs - rec_t0                 # implied recording start (UTC)
    ok = (n >= need) & (start >= t0_utc - 0.5) & (start <= t1_utc + 0.5) & np.isfinite(c)
    if not ok.any():
        return MatchResult(None, None, None, [], None, 0, 0,
                           ["The reference has no data overlapping the recording in this time window."])
    cv = np.where(ok, c, np.nan)
    excl = max(int(p.exclude_s), int(p.highpass_s // 2))
    peaks = distinct_peaks(cv, excl, p.n_top)
    best = peaks[0]
    pos = _refine(cv, best)
    start_best = float(ref_t0_utc + (pos - (N - 1)) - rec_t0)
    z, others = _zscore(cv, best, excl)
    top = [{"start_utc": float(start[i]), "corr": round(float(cv[i]), 4)} for i in peaks]
    overlay = _overlay(rec, ref, best - (N - 1), rec_t0, p.max_overlay_points)
    n_off = int(ok.sum())
    notes += _interpret(float(cv[best]), z, top, n_off, int(n[best]))
    return MatchResult(start_best, round(float(cv[best]), 4), None if z is None else round(z, 2), top, overlay,
                       n_off, int(n[best]), notes)


def _zscore(cv: np.ndarray, best: int, excl: int) -> tuple[float | None, np.ndarray]:
    others = cv.copy()
    others[max(0, best - excl):best + excl + 1] = np.nan
    others = others[np.isfinite(others)]
    if len(others) < 30:
        return None, others
    mu, sd = float(others.mean()), float(others.std())
    if sd <= 1e-12:
        return None, others
    return (float(cv[best]) - mu) / sd, others


def _overlay(rec: np.ndarray, ref: np.ndarray, L: int, rec_t0: float, max_points: int) -> dict:
    """Recording and reference (both z-scored on their common samples) at offset L."""
    N = len(rec)
    seg = np.full(N, np.nan)
    a, b = max(0, L), min(len(ref), L + N)
    if b > a:
        seg[a - L:b - L] = ref[a:b]
    both = np.isfinite(rec) & np.isfinite(seg)
    def z(x):
        if both.sum() < 2:
            return x
        m, s = x[both].mean(), x[both].std()
        return (x - m) / (s if s > 0 else 1.0)
    rz, gz = z(rec), z(seg)
    t = rec_t0 + np.arange(N, dtype=float)
    q = max(1, math.ceil(N / max_points))
    if q > 1:
        n_out = N // q
        t = t[:n_out * q].reshape(n_out, q).mean(axis=1)
        with np.errstate(invalid="ignore"):
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                rz = np.nanmean(rz[:n_out * q].reshape(n_out, q), axis=1)
                gz = np.nanmean(gz[:n_out * q].reshape(n_out, q), axis=1)
    return {"t": [round(float(v), 2) for v in t], "rec": _nullable(rz), "ref": _nullable(gz)}


def _nullable(x: np.ndarray) -> list[float | None]:
    return [round(float(v), 3) if np.isfinite(v) else None for v in x]


def _interpret(corr: float, z: float | None, top: list[dict], n_off: int, overlap: int) -> list[str]:
    notes = [f"Compared {n_off} candidate start times; best correlation {corr:.3f} over {overlap} s."]
    if z is not None:
        # the largest of n roughly independent N(0,1) scores is about sqrt(2 ln n)
        expect = math.sqrt(2 * math.log(max(n_off / 60.0, 2.0)))
        if z >= expect + 4 and corr >= 0.5:
            notes.append(f"The peak stands out clearly (z = {z:.1f}; noise alone would give about {expect:.1f}).")
        elif z >= expect + 2:
            notes.append(f"The peak is probably real (z = {z:.1f}; noise alone would give about {expect:.1f}). "
                         "Check the overlay and the runners-up.")
        else:
            notes.append(f"The peak is not significant (z = {z:.1f}; noise alone would give about {expect:.1f}); "
                         "the recording may be from another grid or time, or too short.")
    if len(top) > 1:
        notes.append(f"Runner-up: correlation {top[1]['corr']:.3f}.")
    return notes
