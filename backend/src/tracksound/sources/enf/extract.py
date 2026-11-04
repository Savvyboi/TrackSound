"""ENF extraction: find the mains hum in a recording and follow its frequency second by second.

For each harmonic ``h`` of the nominal frequency ``f_nom`` (50 or 60 Hz):

1. The audio is resampled to 1 kHz, the band around ``h * f_nom`` is shifted to 0 Hz (complex
   demodulation) and decimated to a 20 Hz complex baseband.
2. Short-time spectra: Hann window of ``window_s`` (default 8 s), 1 s hop. The band
   ``|delta| <= band_hz`` around the nominal (measured at the fundamental, so ``h * band_hz`` at
   harmonic ``h``) is evaluated by a direct DFT on a 1 mHz grid of fundamental deviations.
3. Local SNR per frame and harmonic: the band peak against the noise floor (median power of
   the neighbouring bins), divided by the peak that noise alone would produce in a band of that
   width, so pure noise reads about 0 dB.
4. Spectrum combining (Hajj-Ahmad, Garg & Wu 2013): each harmonic's band spectrum is normalised
   to unit peak, weighted by its local SNR and summed; the ENF is ``f_nom`` plus the combined
   peak position, refined by quadratic interpolation of the log spectrum.
5. Unreliable seconds become null: weak combined SNR, a peak on the band edge, a jump away from
   the 31 s running median, or short islands of valid samples between gaps.

The recorder's own clock error (typically within +-50 ppm) scales every frequency it records by
the same factor: +-2.5 mHz at 50 Hz, a constant offset that the correlation in ``match`` ignores.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from math import gcd
from pathlib import Path
from typing import Callable

import numpy as np
import soundfile as sf
from scipy.ndimage import median_filter, uniform_filter1d
from scipy.signal import resample_poly

BASE_SR = 1000          # intermediate sample rate (Hz): covers harmonics up to 400 Hz
BB_SR = 20              # complex baseband rate per harmonic (Hz)
NOMINALS = (50, 60)
SHARED_HZ = 300         # 6 x 50 = 5 x 60: says nothing about which grid it is
EULER_GAMMA = 0.5772156649

Progress = Callable[[float, str], None]


@dataclass(frozen=True)
class EnfParams:
    window_s: float = 8.0         # STFT window; longer = finer frequency, more smoothing in time
    hop_s: float = 1.0            # one value per second, the resolution of the references
    band_hz: float = 0.2          # largest deviation searched (at the fundamental)
    grid_hz: float = 0.001        # frequency grid of the band spectrum (refined by interpolation)
    harmonics: int = 6            # harmonics 1..harmonics are considered
    min_snr_db: float = 3.0       # a harmonic is used when its median local SNR reaches this
    frame_snr_db: float = 3.0     # seconds whose combined SNR is lower are null
    noise_lo_hz: float = 0.6      # noise floor from |nu| in [h*band + noise_lo, noise_hi] at baseband
    noise_hi_hz: float = 7.0
    weight_smooth_s: int = 11     # SNR weights are smoothed over this many seconds
    median_s: int = 31            # running median for outlier rejection
    outlier_min_hz: float = 0.008 # never reject closer than this to the running median
    outlier_k: float = 6.0        # ... or than k robust standard deviations
    min_run_s: int = 5            # valid runs shorter than this are dropped
    block_s: float = 600.0        # audio is processed in blocks of this many seconds
    detect_max_s: float = 600.0   # nominal detection looks at no more than this much audio


@dataclass
class HarmonicStats:
    n: int
    freq_hz: float
    snr_db: float
    used: bool


@dataclass
class EnfTrace:
    nominal_hz: int | None
    confidence: float
    harmonics: list[HarmonicStats]
    t0: float
    dt: float
    f: np.ndarray                 # Hz at the fundamental, NaN where unreliable
    snr_db: np.ndarray            # combined SNR per step
    notes: list[str] = field(default_factory=list)

    @property
    def quality(self) -> float:
        return float(np.mean(np.isfinite(self.f))) if len(self.f) else 0.0


# ---- audio access --------------------------------------------------------------------------

class AudioSource:
    """Random access to mono samples, from memory or from a (possibly huge) sound file."""

    def __init__(self, x: np.ndarray | None = None, sr: int | None = None, path: str | Path | None = None):
        if path is not None:
            info = sf.info(str(path))
            self.path, self.sr, self.n, self._x = str(path), int(info.samplerate), int(info.frames), None
        else:
            if x is None or sr is None:
                raise ValueError("give either samples and a sample rate, or a path")
            self.path, self.sr, self._x = None, int(sr), np.asarray(x, dtype=np.float64)
            self.n = len(self._x)

    @property
    def duration_s(self) -> float:
        return self.n / self.sr

    def read(self, a: int, b: int) -> np.ndarray:
        """Samples [a, b) as float64, zero-padded outside the file."""
        out = np.zeros(max(0, b - a))
        lo, hi = max(a, 0), min(b, self.n)
        if hi <= lo:
            return out
        if self._x is not None:
            seg = self._x[lo:hi]
        else:
            with sf.SoundFile(self.path) as f:
                f.seek(lo)
                seg = f.read(hi - lo, dtype="float64", always_2d=True).mean(axis=1)
        out[lo - a:lo - a + len(seg)] = seg
        return out


# ---- spectra of one block --------------------------------------------------------------------

def _to_base_rate(x: np.ndarray, sr: int) -> np.ndarray:
    if sr == BASE_SR:
        return x
    g = gcd(BASE_SR, sr)
    return resample_poly(x, BASE_SR // g, sr // g)


def _baseband(x1k: np.ndarray, fc: float) -> np.ndarray:
    n = np.arange(len(x1k))
    z = x1k * np.exp(-2j * np.pi * fc * n / BASE_SR)
    return resample_poly(z, 1, BASE_SR // BB_SR)


def _noise_peak_factor(h: int, p: EnfParams) -> float:
    """Expected (band peak / noise median) for noise alone: max of M exponential bins."""
    m = max(1.0, 2.0 * h * p.band_hz * p.window_s / 1.5)   # independent bins in the band (Hann ENBW 1.5)
    return (math.log(m) + EULER_GAMMA) / math.log(2.0)


@dataclass
class _BlockSpectra:
    centers: np.ndarray           # frame centre times (recording s)
    band: dict[int, np.ndarray]   # h -> (frames, grid) power on the delta grid
    snr: dict[int, np.ndarray]    # h -> (frames,) linear local SNR (1 ~ noise)


def _frame_matrix(bb: np.ndarray, centers_idx: np.ndarray, L: int) -> np.ndarray:
    half = L // 2
    idx = centers_idx[:, None] + np.arange(-half, L - half)[None, :]
    idx = np.clip(idx, 0, len(bb) - 1)
    return bb[idx]


def _block_spectra(audio: AudioSource, f_nom: float, harmonics: list[int], centers: np.ndarray,
                   p: EnfParams) -> _BlockSpectra:
    """Band power and local SNR for frames centred at ``centers`` (s), for each harmonic."""
    margin = p.window_s / 2 + 2.0
    t_a = math.floor((centers[0] - margin) * BB_SR) / BB_SR
    t_b = centers[-1] + margin
    a = int(round(t_a * audio.sr))
    b = int(round(t_b * audio.sr))
    x1k = _to_base_rate(audio.read(a, b), audio.sr)
    t_start = a / audio.sr
    L = int(round(p.window_s * BB_SR))
    win = np.hanning(L)
    delta = _delta_grid(p)
    n_noise_fft = 512
    nu = np.fft.fftfreq(n_noise_fft, 1.0 / BB_SR)
    band, snr = {}, {}
    cidx = np.round((centers - t_start) * BB_SR).astype(int)
    tn = (np.arange(L) - L // 2) / BB_SR
    for h in harmonics:
        bb = _baseband(x1k, h * f_nom)
        frames = _frame_matrix(bb, cidx, L) * win[None, :]
        # direct DFT on the delta grid (h * delta at this harmonic), exact and cheap: L x grid
        E = np.exp(-2j * np.pi * np.outer(tn, h * delta))
        P = np.abs(frames @ E) ** 2
        # noise floor from a coarse zero-padded FFT, away from the band
        Pn = np.abs(np.fft.fft(frames, n=n_noise_fft, axis=1)) ** 2
        sel = (np.abs(nu) >= h * p.band_hz + p.noise_lo_hz) & (np.abs(nu) <= p.noise_hi_hz)
        noise = np.median(Pn[:, sel], axis=1) / math.log(2.0)        # mean noise power per bin
        noise = np.maximum(noise, 1e-30)
        band[h] = P
        snr[h] = P.max(axis=1) / noise / (_noise_peak_factor(h, p) * math.log(2.0))
    return _BlockSpectra(centers=centers, band=band, snr=snr)


def _delta_grid(p: EnfParams) -> np.ndarray:
    k = int(round(p.band_hz / p.grid_hz))
    return np.arange(-k, k + 1) * p.grid_hz


def _parabolic_peak(y: np.ndarray, grid_step: float) -> tuple[np.ndarray, np.ndarray]:
    """Peak position (in units of the grid, relative to index 0) per row, and an edge flag."""
    k = np.argmax(y, axis=1)
    n = y.shape[1]
    edge = (k == 0) | (k == n - 1)
    kk = np.clip(k, 1, n - 2)
    rows = np.arange(y.shape[0])
    ly = np.log(np.maximum(y[:, :], 1e-300))
    a, b, c = ly[rows, kk - 1], ly[rows, kk], ly[rows, kk + 1]
    den = a - 2 * b + c
    off = np.where(np.abs(den) > 1e-12, 0.5 * (a - c) / den, 0.0)
    off = np.clip(off, -0.5, 0.5)
    pos = np.where(edge, k, kk + off)
    return pos * grid_step, edge


# ---- frame geometry ---------------------------------------------------------------------------

def frame_centers(duration_s: float, p: EnfParams) -> np.ndarray:
    """Centres of the analysis frames: integer seconds from ceil(window/2) on."""
    t0 = float(math.ceil(p.window_s / 2))
    last = duration_s - p.window_s / 2
    if last < t0:
        return np.zeros(0)
    n = int(math.floor((last - t0) / p.hop_s)) + 1
    return t0 + np.arange(n) * p.hop_s


# ---- nominal detection --------------------------------------------------------------------------

def _harmonics_for(nominal: int, p: EnfParams) -> list[int]:
    return list(range(1, p.harmonics + 1))


def _discriminating(nominal: int, h: int) -> bool:
    return nominal * h != SHARED_HZ


def detect_nominal(audio: AudioSource, p: EnfParams = EnfParams()) -> tuple[int | None, float, dict[int, dict[int, float]]]:
    """Which grid's hum is in the recording: 50, 60 or None, a confidence and the per-harmonic
    median SNR (dB) for both nominals (``{nominal: {h: snr_db}}``)."""
    centers = frame_centers(audio.duration_s, p)
    if len(centers) == 0:
        return None, 0.0, {50: {}, 60: {}}
    centers = _detection_subset(centers, p)
    stats: dict[int, dict[int, float]] = {}
    for nominal in NOMINALS:
        hs = _harmonics_for(nominal, p)
        snr = {h: [] for h in hs}
        for chunk in _split_runs(centers):
            sp = _block_spectra(audio, nominal, hs, chunk, p)
            for h in hs:
                snr[h].append(sp.snr[h])
        stats[nominal] = {h: float(10 * np.log10(np.median(np.concatenate(v)))) for h, v in snr.items()}
    score = {nom: sum(max(0.0, s - p.min_snr_db) for h, s in st.items() if _discriminating(nom, h))
             for nom, st in stats.items()}
    best = max(score, key=score.get)
    other = 60 if best == 50 else 50
    best_snr = max(s for h, s in stats[best].items() if _discriminating(best, h))
    if score[best] <= 0:
        # no harmonic stands out: confidence that there is no hum
        return None, float(np.clip(1.0 - best_snr / p.min_snr_db, 0.0, 1.0)), stats
    margin = (score[best] - score[other]) / (score[best] + score[other])
    strength = float(np.clip((best_snr - p.min_snr_db) / 9.0, 0.0, 1.0))
    return best, float(np.clip(margin * (0.5 + 0.5 * strength), 0.0, 1.0)), stats


def _detection_subset(centers: np.ndarray, p: EnfParams) -> np.ndarray:
    """At most ``detect_max_s`` seconds of frames, as ten runs spread over the recording."""
    n_max = int(p.detect_max_s / p.hop_s)
    if len(centers) <= n_max:
        return centers
    runs, per = 10, n_max // 10
    starts = np.linspace(0, len(centers) - per, runs).astype(int)
    return np.concatenate([centers[s:s + per] for s in starts])


def _split_runs(centers: np.ndarray, max_len: int = 600) -> list[np.ndarray]:
    """Split frame centres into contiguous runs (gaps > 1.5 hops) of at most ``max_len`` frames."""
    if len(centers) == 0:
        return []
    cut = np.where(np.diff(centers) > 1.5 * (centers[1] - centers[0] if len(centers) > 1 else 1.0))[0] + 1
    out = []
    for run in np.split(centers, cut):
        for i in range(0, len(run), max_len):
            out.append(run[i:i + max_len])
    return out


# ---- trace extraction ---------------------------------------------------------------------------

def extract_trace(audio: AudioSource, nominal: int | str = "auto", p: EnfParams = EnfParams(),
                  progress: Progress | None = None) -> EnfTrace:
    """Detect the nominal (unless given) and extract the ENF trace of the whole recording."""
    progress = progress or (lambda f, m: None)
    centers = frame_centers(audio.duration_s, p)
    if len(centers) < 10:
        raise ValueError(f"the recording is too short for ENF analysis (need at least {p.window_s + 10:.0f} s)")
    progress(0.02, "Looking for mains hum")
    det_nom, conf, det_stats = detect_nominal(audio, p)
    notes: list[str] = []
    if nominal == "auto":
        if det_nom is None:
            notes.append("No mains hum found at 50 or 60 Hz or their harmonics.")
            return EnfTrace(None, conf, _stats_to_harmonics(det_stats, 50, p, None), float(centers[0]),
                            p.hop_s, np.full(len(centers), np.nan), np.full(len(centers), np.nan), notes)
        f_nom = det_nom
    else:
        f_nom = int(nominal)
        if f_nom not in NOMINALS:
            raise ValueError("nominal must be 'auto', 50 or 60")
        if det_nom is not None and det_nom != f_nom:
            notes.append(f"The hum looks more like a {det_nom} Hz grid, but {f_nom} Hz was requested.")
        conf = conf if det_nom == f_nom else 0.0
    st = det_stats[f_nom]
    used = [h for h in range(1, p.harmonics + 1) if st.get(h, -99) >= p.min_snr_db]
    if not used:
        used = [max(st, key=st.get)]
        notes.append("No harmonic is clearly above the noise; the trace is unreliable.")
    f, snr_db, per_h = _extract_with(audio, f_nom, used, centers, p, progress)
    harmonics = _stats_to_harmonics(det_stats, f_nom, p, per_h, used)
    trace = EnfTrace(f_nom, conf, harmonics, float(centers[0]), p.hop_s, f, snr_db, notes)
    trace.notes.extend(_describe(trace, used))
    return trace


def _extract_with(audio: AudioSource, f_nom: int, used: list[int], centers: np.ndarray, p: EnfParams,
                  progress: Progress) -> tuple[np.ndarray, np.ndarray, dict[int, tuple[np.ndarray, np.ndarray]]]:
    delta = _delta_grid(p)
    f = np.full(len(centers), np.nan)
    snr_comb = np.zeros(len(centers))
    edge_all = np.zeros(len(centers), bool)
    per_h = {h: (np.full(len(centers), np.nan), np.zeros(len(centers))) for h in used}
    per_block = max(1, int(p.block_s / p.hop_s))
    n_blocks = math.ceil(len(centers) / per_block)
    # SNR weights need neighbours: compute block spectra first, combine per block with weights
    # smoothed inside the block (blocks are long, so the edges matter little)
    for bi in range(n_blocks):
        sl = slice(bi * per_block, min(len(centers), (bi + 1) * per_block))
        progress(0.05 + 0.9 * bi / n_blocks, f"Extracting ENF ({bi + 1}/{n_blocks})")
        sp = _block_spectra(audio, f_nom, used, centers[sl], p)
        S = np.zeros_like(next(iter(sp.band.values())))
        tot = np.zeros(len(centers[sl]))
        for h in used:
            w = np.maximum(uniform_filter1d(sp.snr[h], p.weight_smooth_s, mode="nearest") - 1.0, 0.0)
            P = sp.band[h]
            S += w[:, None] * P / np.maximum(P.max(axis=1, keepdims=True), 1e-300)
            tot += np.maximum(sp.snr[h] - 1.0, 0.0)
            dh, eh = _parabolic_peak(P, p.grid_hz)
            per_h[h][0][sl] = np.where(eh, np.nan, f_nom + delta[0] + dh)
            per_h[h][1][sl] = sp.snr[h]
        pos, edge = _parabolic_peak(np.where(S > 0, S, 1e-300), p.grid_hz)
        f[sl] = f_nom + delta[0] + pos
        edge_all[sl] = edge | ~(S.max(axis=1) > 0)
        snr_comb[sl] = tot
    snr_db = 10 * np.log10(1.0 + snr_comb)
    f[(snr_db < p.frame_snr_db) | edge_all] = np.nan
    f = _reject_outliers(f, p)
    progress(0.97, "ENF extracted")
    return f, snr_db, per_h


def _reject_outliers(f: np.ndarray, p: EnfParams) -> np.ndarray:
    f = f.copy()
    for _ in range(2):
        ok = np.isfinite(f)
        if ok.sum() < p.median_s:
            break
        med = _nan_running_median(f, p.median_s)
        r = f - med
        mad = 1.4826 * np.nanmedian(np.abs(r[ok]))
        thr = max(p.outlier_min_hz, p.outlier_k * mad)
        f[np.abs(r) > thr] = np.nan
    return _drop_short_runs(f, p.min_run_s)


def _nan_running_median(x: np.ndarray, size: int) -> np.ndarray:
    """Running median that ignores NaN (NaN where the window has no finite value)."""
    ok = np.isfinite(x)
    if ok.all():
        return median_filter(x, size=size, mode="nearest")
    # fill gaps by interpolation for the filter, then blank windows without real data
    idx = np.arange(len(x))
    filled = np.interp(idx, idx[ok], x[ok])
    med = median_filter(filled, size=size, mode="nearest")
    support = uniform_filter1d(ok.astype(float), size, mode="nearest")
    med[support < 0.3] = np.nan
    return med


def _drop_short_runs(f: np.ndarray, min_run: int) -> np.ndarray:
    ok = np.isfinite(f)
    if min_run <= 1 or not ok.any():
        return f
    f = f.copy()
    d = np.diff(np.concatenate([[0], ok.astype(int), [0]]))
    starts, ends = np.where(d == 1)[0], np.where(d == -1)[0]
    for s, e in zip(starts, ends):
        if e - s < min_run:
            f[s:e] = np.nan
    return f


# ---- reporting ------------------------------------------------------------------------------------

def _stats_to_harmonics(det_stats: dict[int, dict[int, float]], f_nom: int, p: EnfParams,
                        per_h: dict[int, tuple[np.ndarray, np.ndarray]] | None,
                        used: list[int] | None = None) -> list[HarmonicStats]:
    out = []
    used = used or []
    for h in range(1, p.harmonics + 1):
        snr_db = det_stats.get(f_nom, {}).get(h, float("nan"))
        freq = float(h * f_nom)
        if per_h and h in per_h:
            fh, sh = per_h[h]
            good = np.isfinite(fh) & (sh > 2.0)
            if good.sum() >= 5:
                freq = float(h * np.median(fh[good]))
            if np.isfinite(sh).any():
                snr_db = float(10 * np.log10(max(np.median(sh), 1e-12)))
        out.append(HarmonicStats(n=h, freq_hz=round(freq, 4), snr_db=round(float(snr_db), 2) if np.isfinite(snr_db) else -99.0,
                                 used=h in used))
    return out


def _describe(tr: EnfTrace, used: list[int]) -> list[str]:
    notes = []
    hz = ", ".join(f"{h * tr.nominal_hz} Hz" for h in used)
    notes.append(f"Hum of a {tr.nominal_hz} Hz grid; harmonics used: {hz}.")
    q = tr.quality
    notes.append(f"{q * 100:.0f}% of the seconds gave a usable ENF value.")
    if q < 0.3:
        notes.append("Most of the recording has no usable hum; matching may fail or be ambiguous.")
    ok = np.isfinite(tr.f)
    if ok.sum() > 60:
        sd = float(np.nanstd(tr.f)) * 1000
        if sd < 2.0:
            notes.append(f"The hum frequency hardly varies (std {sd:.1f} mHz). It may come from an inverter, "
                         "a generator or a quartz-controlled device rather than the grid.")
    notes.append("The recorder's clock error (typically within +-50 ppm) shifts all frequencies by up to "
                 f"+-{tr.nominal_hz * 50e-6 * 1000:.1f} mHz; matching ignores such a constant offset.")
    return notes
