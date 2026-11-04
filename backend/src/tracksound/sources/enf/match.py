"""Matching an ENF trace against a reference grid-frequency series.

Both series are on a 1 s grid with NaN for missing values. The reference is first smoothed with
the same Hann window the extractor used, so both see the grid frequency through the same "lens".
Then both go through the same preprocessing, chosen from the trace quality *before* matching
(so no selection among several tries inflates the significance):

* ``diff`` (good traces): first differences. Grid frequency wanders like a random walk, so its
  differences are nearly white; correlating them keys on the fine structure and gives the
  sharpest, most unique peak. On synthetic tests with real GB frequency this discriminated best
  (true-match z about 10-11 for 30 min against 3 days, versus 8-9 for a 60 s high-pass).
* ``highpass`` (patchy traces): a 300 s moving average is removed; more robust to noise.
* ``raw`` (weak traces): only the mean is removed. It still finds the right offset when the hum is
  weak, because the slow drifts survive, but many offsets look alike, so its z-score says little.

For every candidate start time the Pearson correlation is computed over the samples where both
series are valid (at least ``min_overlap_s`` and half of the recording's valid seconds). All
offsets are evaluated at once with FFT cross-correlations of the masked sums (n, sum x, sum y,
sum x^2, sum y^2, sum xy), so an hour against a month of reference takes about a second.

The z-score compares the best peak with the correlations at all other offsets (excluding the
peak's neighbourhood): how unique the match is. With many offsets the best of pure chance grows
like sqrt(2 ln n), which ``interpret`` takes into account.
"""
from __future__ import annotations

import math
import warnings
from dataclasses import dataclass, field

import numpy as np
from scipy.fft import irfft, next_fast_len, rfft

METHODS = ("diff", "highpass", "raw")


@dataclass(frozen=True)
class MatchParams:
    method: str = "diff"           # "diff" | "highpass" | "raw"
    smooth_s: float = 16.0         # Hann window applied to the reference (= extraction window)
    highpass_s: float = 300.0      # moving average removed by the "highpass" method
    min_overlap_s: float = 120.0   # an offset needs at least this many common valid seconds
    min_overlap_frac: float = 0.5  # ... and this fraction of the recording's valid seconds
    exclude_s: float = 60.0        # peaks closer than this are the same peak
    n_top: int = 5
    max_overlay_points: int = 4000


def params_for_trace(f: np.ndarray, smooth_s: float) -> MatchParams:
    """The preprocessing for a trace, from its fraction of usable seconds (decided before matching).

    On synthetic tests differences discriminated best whenever the trace was clean, or patchy but
    long (at least 600 usable seconds); a 300 s high-pass held up better on short patchy traces;
    only raw traces still found the offset when most seconds were lost."""
    ok = np.isfinite(np.asarray(f, float))
    q, n_valid = float(ok.mean()) if len(ok) else 0.0, int(ok.sum())
    if q >= 0.75 or (q >= 0.45 and n_valid >= 600):
        method = "diff"
    elif q >= 0.3:
        method = "highpass"
    else:
        method = "raw"
    return MatchParams(method=method, smooth_s=smooth_s)


@dataclass
class MatchResult:
    start_utc: float | None
    corr: float | None
    z: float | None
    top: list[dict]
    overlay: dict | None
    n_offsets: int
    overlap_s: int
    method: str
    notes: list[str] = field(default_factory=list)


# ---- masked filters ------------------------------------------------------------------------------

def masked_smooth(x: np.ndarray, kernel: np.ndarray) -> np.ndarray:
    """Weighted moving average ignoring NaN (NaN where less than a quarter of the window has data)."""
    ok = np.isfinite(x)
    num = _conv_same(np.where(ok, x, 0.0), kernel)
    den = _conv_same(ok.astype(float), kernel)
    with np.errstate(invalid="ignore", divide="ignore"):
        out = num / den
    out[den < 0.25 * kernel.sum()] = np.nan
    return out


def _conv_same(x: np.ndarray, k: np.ndarray) -> np.ndarray:
    n = len(x) + len(k) - 1
    nf = next_fast_len(n, real=True)
    y = irfft(rfft(x, nf) * rfft(k, nf), nf)[:n]
    a = (len(k) - 1) // 2
    return y[a:a + len(x)]


def smooth_like_extractor(ref: np.ndarray, window_s: float) -> np.ndarray:
    """What the extractor would see: the reference averaged over its Hann analysis window."""
    L = 2 * int(round(window_s / 2)) + 1
    if L < 3:
        return np.asarray(ref, float)
    k = np.hanning(L)
    return masked_smooth(np.asarray(ref, float), k / k.sum())


def highpass(x: np.ndarray, length_s: float) -> np.ndarray:
    """Subtract a centred masked moving average of ``length_s`` samples (NaN stays NaN)."""
    L = int(round(length_s)) | 1
    out = x - masked_smooth(x, np.ones(L))
    out[~np.isfinite(x)] = np.nan
    return out


def first_difference(x: np.ndarray, clip_sd: float = 5.0) -> np.ndarray:
    """``d[k] = x[k] - x[k-1]`` (NaN where either is missing), same length and time base as x,
    winsorised at ``clip_sd`` robust standard deviations: a few tracking glitches or meter spikes
    must not dominate the correlation."""
    d = np.full(len(x), np.nan)
    d[1:] = x[1:] - x[:-1]
    ok = np.isfinite(d)
    if clip_sd and ok.sum() > 10:
        sd = 1.4826 * float(np.median(np.abs(d[ok] - np.median(d[ok]))))
        if sd > 0:
            d[ok] = np.clip(d[ok], -clip_sd * sd, clip_sd * sd)
    return d


def preprocess(x: np.ndarray, p: MatchParams, is_reference: bool) -> np.ndarray:
    y = smooth_like_extractor(x, p.smooth_s) if is_reference else np.asarray(x, float)
    if p.method == "diff":
        return first_difference(y)
    if p.method == "highpass":
        return highpass(y, p.highpass_s)
    if p.method == "raw":
        return y
    raise ValueError(f"unknown match method {p.method!r}")


# ---- correlation over all offsets ----------------------------------------------------------------

def masked_xcorr(rec: np.ndarray, ref: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Pearson correlation of ``rec`` against ``ref[L:L+len(rec)]`` for every L in
    ``[-(len(rec)-1), len(ref)-1]`` over the jointly valid samples. Returns (corr, n_common),
    both of length ``len(ref) + len(rec) - 1``; index i is offset ``L = i - (len(rec) - 1)``."""
    N, M = len(rec), len(ref)
    mr, mg = np.isfinite(rec), np.isfinite(ref)
    # centre for numerical stability (the correlation does not depend on it)
    r = np.where(mr, rec - (rec[mr].mean() if mr.any() else 0.0), 0.0)
    g = np.where(mg, ref - (ref[mg].mean() if mg.any() else 0.0), 0.0)
    nf = next_fast_len(N + M - 1, real=True)
    # correlation = convolution with the reversed recording
    R = [rfft(a[::-1], nf) for a in (mr.astype(float), r, r * r)]
    G = [rfft(a, nf) for a in (mg.astype(float), g, g * g)]

    def xc(Gi, Ri):
        return irfft(Gi * Ri, nf)[:N + M - 1]

    n = np.rint(xc(G[0], R[0]))
    sx, sy = xc(G[0], R[1]), xc(G[1], R[0])
    sxx, syy, sxy = xc(G[0], R[2]), xc(G[2], R[0]), xc(G[1], R[1])
    with np.errstate(invalid="ignore", divide="ignore"):
        vx = sxx - sx * sx / n
        vy = syy - sy * sy / n
        c = (sxy - sx * sy / n) / np.sqrt(vx * vy)
    c[(n < 3) | ~(vx > 1e-18) | ~(vy > 1e-18)] = np.nan
    return np.clip(c, -1.0, 1.0), n.astype(int)


def peak_halfwidth(c: np.ndarray, i: int, frac: float = 0.5) -> int:
    """Samples from peak ``i`` to where the correlation first falls below ``frac`` of the peak
    (or becomes NaN), on the wider side: the width of the peak's main lobe."""
    v = c[i]
    if not np.isfinite(v) or v <= 0:
        return 0
    thr = frac * v
    widths = []
    for seg in (c[i::-1], c[i:]):
        below = ~(seg > thr)                           # NaN counts as below
        widths.append(int(np.argmax(below)) if below.any() else len(seg))
    return max(widths)


def distinct_peaks(c: np.ndarray, exclude: int, k: int) -> list[int]:
    """Indices of the ``k`` best distinct peaks: after each pick, its main lobe (at least
    ``exclude`` samples either side) is removed, so a runner-up is never a shoulder of a better peak."""
    work = np.where(np.isfinite(c), c, -np.inf)
    out: list[int] = []
    for _ in range(k):
        i = int(np.argmax(work))
        if not np.isfinite(work[i]):
            break
        out.append(i)
        w = max(exclude, peak_halfwidth(np.where(np.isfinite(work), work, np.nan), i))
        work[max(0, i - w):i + w + 1] = -np.inf
    return out


def fisher(c: np.ndarray | float) -> np.ndarray | float:
    """Fisher's z-transform of a correlation (atanh, clipped below |r| = 1)."""
    return np.arctanh(np.clip(c, -0.9999, 0.9999))


def _refine(c: np.ndarray, i: int) -> float:
    """Sub-sample position of the peak at ``i`` (parabola through its neighbours)."""
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
    rec_raw = np.asarray(rec_f, float)
    ref_s = smooth_like_extractor(ref_f, p.smooth_s)
    rec = preprocess(rec_raw, p, is_reference=False)
    ref = preprocess(ref_f, p, is_reference=True)
    n_valid = int(np.isfinite(rec).sum())
    need = max(p.min_overlap_s, p.min_overlap_frac * n_valid)
    if n_valid < p.min_overlap_s:
        return MatchResult(None, None, None, [], None, 0, 0, p.method,
                           [f"Only {n_valid} s of usable ENF in the recording; at least {p.min_overlap_s:.0f} s are needed."])
    c, n = masked_xcorr(rec, ref)
    N = len(rec)
    offs = np.arange(len(c)) - (N - 1)                 # reference index of recording sample 0
    start = ref_t0_utc + offs - rec_t0                 # implied recording start (UTC)
    ok = (n >= need) & (start >= t0_utc - 0.5) & (start <= t1_utc + 0.5) & np.isfinite(c)
    if not ok.any():
        return MatchResult(None, None, None, [], None, 0, 0, p.method,
                           ["The reference has too little data overlapping the recording in this time window."])
    cv = np.where(ok, c, np.nan)
    excl = int(p.exclude_s)
    peaks = distinct_peaks(cv, excl, p.n_top)
    best = peaks[0]
    lobe = max(excl, peak_halfwidth(cv, best))
    pos = _refine(cv, best)
    start_best = float(ref_t0_utc + (pos - (N - 1)) - rec_t0)
    z, z2 = zscores(cv, best, lobe, peaks[1] if len(peaks) > 1 else None)
    top = [{"start_utc": float(start[i]), "corr": round(float(cv[i]), 4)} for i in peaks]
    overlay = make_overlay(rec_raw, ref_s, best - (N - 1), rec_t0, p.max_overlay_points)
    n_off = int(ok.sum())
    runner = None if len(peaks) < 2 else (float(start[peaks[1]] - start[best]), float(cv[peaks[1]]))
    notes = interpret(float(cv[best]), z, z2, n_off, int(n[best]), p.method, lobe, runner)
    return MatchResult(start_best, round(float(cv[best]), 4), None if z is None else round(z, 2), top, overlay,
                       n_off, int(n[best]), p.method, notes)


def zscores(cv: np.ndarray, best: int, lobe: int, runner: int | None) -> tuple[float | None, float | None]:
    """z-scores of the best peak and the runner-up against the correlations at all offsets outside
    the best peak's main lobe, on Fisher's scale (where a correlation's spread does not depend on
    its value, so r = 0.995 against 0.65 counts as the large difference it is)."""
    others = cv.copy()
    others[max(0, best - lobe):best + lobe + 1] = np.nan
    others = fisher(others[np.isfinite(others)])
    if len(others) < 30:
        return None, None
    mu, sd = float(others.mean()), float(others.std())
    if sd <= 1e-12:
        return None, None
    z = (float(fisher(cv[best])) - mu) / sd
    z2 = None if runner is None else (float(fisher(cv[runner])) - mu) / sd
    return z, z2


def make_overlay(rec: np.ndarray, ref_smoothed: np.ndarray, L: int, rec_t0: float, max_points: int) -> dict:
    """Recording and reference ENF (Hz) at offset L on the recording's time axis, block-averaged to
    at most ``max_points`` points. The reference is smoothed like the extractor sees it; the
    recording is shifted by the median difference over their common samples, which removes the
    recorder's constant clock offset."""
    N = len(rec)
    seg = np.full(N, np.nan)
    a, b = max(0, L), min(len(ref_smoothed), L + N)
    if b > a:
        seg[a - L:b - L] = ref_smoothed[a:b]
    both = np.isfinite(rec) & np.isfinite(seg)
    shift = float(np.median(rec[both] - seg[both])) if both.any() else 0.0
    rh, gh = rec - shift, seg
    t = rec_t0 + np.arange(N, dtype=float)
    q = max(1, math.ceil(N / max_points))
    if q > 1:
        m = N // q
        t = t[:m * q].reshape(m, q).mean(axis=1)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            rh = np.nanmean(rh[:m * q].reshape(m, q), axis=1)
            gh = np.nanmean(gh[:m * q].reshape(m, q), axis=1)
    return {"t": [round(float(v), 2) for v in t], "rec": _nullable(rh), "ref": _nullable(gh),
            "rec_shift_hz": round(shift, 6)}


def _nullable(x: np.ndarray) -> list[float | None]:
    return [round(float(v), 5) if np.isfinite(v) else None for v in x]


def chance_z(n_offsets: int, lobe_s: float) -> float:
    """Roughly the largest z that chance alone produces over this many offsets: neighbouring
    offsets are correlated over about the peak's main-lobe width, giving ``n / lobe``
    independent trials, and the best of n independent normal scores is about sqrt(2 ln n)."""
    per = float(np.clip(lobe_s, 10.0, 3600.0))
    return math.sqrt(2 * math.log(max(n_offsets / per, 2.0)))


def interpret(corr: float, z: float | None, z2: float | None, n_off: int, overlap: int, method: str,
              lobe_s: float = 60.0, runner: tuple[float, float] | None = None) -> list[str]:
    what = {"diff": "first differences", "highpass": "high-passed traces (300 s)", "raw": "raw traces"}[method]
    notes = [f"Compared {n_off} candidate start times using {what}; best correlation {corr:.3f} over {overlap} s."]
    if z is None:
        return notes
    expect = chance_z(n_off, lobe_s if method != "diff" else min(lobe_s, 20.0))
    gap = None if z2 is None else z - z2
    gap_txt = "" if gap is None else f", {gap:.1f} above the runner-up"
    if z >= expect + 3 and (gap is None or gap >= 2):
        verdict = f"The match stands out clearly (z = {z:.1f}{gap_txt}; chance alone gives about {expect:.1f})."
    elif z >= expect + 1.5:
        verdict = (f"The match is probably real (z = {z:.1f}{gap_txt}; chance alone gives about {expect:.1f}). "
                   "Check the overlay and the runners-up.")
    else:
        verdict = (f"The match is not significant (z = {z:.1f}{gap_txt}; chance alone gives about {expect:.1f}). "
                   "The recording may be from another grid or time, or too short.")
    notes.append(verdict)
    if method == "raw":
        notes.append("The hum is weak, so only slow drifts could be compared; raw traces resemble each other at "
                     "many offsets. Narrow the time window if you can.")
    if runner is not None:
        dt_s, rc = runner
        notes.append(f"Runner-up: {_human(dt_s)} {'later' if dt_s > 0 else 'earlier'}, correlation {rc:.3f}.")
    return notes


def _human(seconds: float) -> str:
    s = abs(seconds)
    if s < 120:
        return f"{s:.0f} s"
    if s < 7200:
        return f"{s / 60:.0f} min"
    if s < 2 * 86400:
        return f"{s / 3600:.1f} h"
    return f"{s / 86400:.1f} days"
