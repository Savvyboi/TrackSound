"""ENF extraction: find the mains hum in a recording and follow its frequency second by second.

For each harmonic ``h`` of the nominal frequency ``f_nom`` (50 or 60 Hz):

1. The audio is resampled to 1 kHz, the band around ``h * f_nom`` is shifted to 0 Hz (complex
   demodulation) and decimated to a 20 Hz complex baseband.
2. Short-time spectra: Hann window of ``window_s`` (default 16 s), 1 s hop. The band
   ``|delta| <= band_hz`` (measured at the fundamental, ``h * band_hz`` at harmonic ``h``) is
   evaluated by a direct DFT on a 1 mHz grid of fundamental deviations ``delta``; the noise floor
   per frame is the median power of the neighbouring bins.

Detection (which grid, which harmonics): the band power averaged over the whole recording is
compared with the noise floor. Noise alone gives a ratio of 1 with a spread that shrinks with the
recording length, so even a hum well below the noise in every single frame is found, and the
50 Hz family (50, 100, 150 ... Hz) is weighed against the 60 Hz family (60, 120, 180 ... Hz).

Tracking: the per-harmonic spectra in noise units are summed over the useful harmonics (the
"spectrum combining" of Hajj-Ahmad, Garg & Wu, 2013; with spectra in units of the noise floor
this sum is the joint log-likelihood of the frequency), and the most likely smooth path through
the combined spectrogram is found by dynamic programming (Viterbi), which keeps the trace on the
hum line through frames where noise peaks would win a per-frame maximum. The path is refined to
sub-mHz by quadratic interpolation. Seconds where the evidence along the path is no better than
noise produces are set to null.

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
from numpy.lib.stride_tricks import sliding_window_view
from scipy.ndimage import uniform_filter1d
from scipy.signal import resample_poly

BASE_SR = 1000          # intermediate sample rate (Hz): covers harmonics up to 400 Hz
BB_SR = 20              # complex baseband rate per harmonic (Hz)
NOMINALS = (50, 60)
SHARED_HZ = 300         # 6 x 50 = 5 x 60: says nothing about which grid it is
HANN_ENBW = 1.5         # equivalent noise bandwidth of a Hann window, in bins

Progress = Callable[[float, str], None]


@dataclass(frozen=True)
class EnfParams:
    window_s: float = 16.0        # STFT window: longer = more SNR and finer frequency, more smoothing
    hop_s: float = 1.0            # one value per second, the resolution of the references
    band_hz: float = 0.25         # largest deviation searched (at the fundamental; GB operates within +-0.2)
    grid_hz: float = 0.001        # frequency grid of the band spectra
    harmonics: int = 6            # harmonics 1..harmonics are considered
    detect_z: float = 6.0         # evidence (sum of z-scores) needed to call a grid ...
    detect_max_z: float = 4.5     # ... with at least one harmonic this clearly visible
    use_z: float = 3.0            # a harmonic joins the combination above this z-score
    noise_lo_hz: float = 0.6      # noise floor from |nu| in [h*band + noise_lo, noise_hi] at baseband
    noise_hi_hz: float = 7.0
    max_step_hz: float = 0.006    # largest change of the tracked ENF from one second to the next
    step_sigma_hz: float = 0.002  # typical change per second (Gaussian transition prior)
    min_path_snr_db: float = 2.0  # seconds whose smoothed evidence is lower are null
    min_run_s: int = 10           # valid runs shorter than this are dropped
    block_s: float = 600.0        # audio is processed in blocks of this many seconds
    detect_max_s: float = 900.0   # nominal detection looks at no more than this much audio


@dataclass
class HarmonicStats:
    n: int
    freq_hz: float
    snr_db: float
    used: bool
    z: float = 0.0


@dataclass
class EnfTrace:
    nominal_hz: int | None
    confidence: float
    harmonics: list[HarmonicStats]
    t0: float
    dt: float
    f: np.ndarray                 # Hz at the fundamental, NaN where unreliable
    snr_db: np.ndarray            # evidence along the path, per step (dB above noise)
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


def _oscillator(fc: float, n: int) -> np.ndarray:
    """exp(-2j pi fc k / BASE_SR) for k < n. For whole-Hz fc it repeats every BASE_SR/gcd samples
    (at most 1000, at most 100 for harmonics of 50 and 60 Hz), so one period is tiled."""
    if float(fc).is_integer():
        period = BASE_SR // gcd(int(fc), BASE_SR)
        one = np.exp(-2j * np.pi * fc * np.arange(period) / BASE_SR)
        return np.tile(one, n // period + 1)[:n]
    return np.exp(-2j * np.pi * fc * np.arange(n) / BASE_SR)


def _baseband(x1k: np.ndarray, fc: float) -> np.ndarray:
    z = x1k * _oscillator(fc, len(x1k))
    return resample_poly(z, 1, BASE_SR // BB_SR)


def delta_grid(p: EnfParams) -> np.ndarray:
    k = int(round(p.band_hz / p.grid_hz))
    return np.arange(-k, k + 1) * p.grid_hz


def _independent_bins(h: int, p: EnfParams) -> float:
    """Independent frequency bins in harmonic h's search band."""
    return max(1.0, 2.0 * h * p.band_hz * p.window_s / HANN_ENBW)


@dataclass
class _Spectra:
    centers: np.ndarray           # frame centre times (recording s)
    band: dict[int, np.ndarray]   # h -> (frames, grid) power in units of the mean noise per bin


def _block_spectra(audio: AudioSource, f_nom: float, harmonics: list[int], centers: np.ndarray,
                   p: EnfParams) -> _Spectra:
    """Band spectra (in noise units) for frames centred at ``centers`` (s), per harmonic."""
    margin = p.window_s / 2 + 2.0
    t_a = math.floor((centers[0] - margin) * BB_SR) / BB_SR
    a = int(round(t_a * audio.sr))
    b = int(round((centers[-1] + margin) * audio.sr))
    x1k = _to_base_rate(audio.read(a, b), audio.sr)
    t_start = a / audio.sr
    L = int(round(p.window_s * BB_SR))
    win = np.hanning(L).astype(np.float32)
    delta = delta_grid(p)
    n_fft = 1 << int(math.ceil(math.log2(L)))                          # noise floor: no oversampling needed
    nu = np.fft.fftfreq(n_fft, 1.0 / BB_SR)
    cidx = np.round((centers - t_start) * BB_SR).astype(int)
    idx = cidx[:, None] + np.arange(-(L // 2), L - L // 2)[None, :]
    tn = (np.arange(L) - L // 2) / BB_SR
    band = {}
    for h in harmonics:
        bb = _baseband(x1k, h * f_nom).astype(np.complex64)
        frames = bb[np.clip(idx, 0, len(bb) - 1)] * win[None, :]
        E = np.exp(-2j * np.pi * np.outer(tn, h * delta)).astype(np.complex64)   # exact DFT on the delta grid
        P = np.abs(frames @ E).astype(np.float64) ** 2
        Pn = np.abs(np.fft.fft(frames, n=n_fft, axis=1)) ** 2
        sel = (np.abs(nu) >= h * p.band_hz + p.noise_lo_hz) & (np.abs(nu) <= p.noise_hi_hz)
        noise = np.median(Pn[:, sel], axis=1) / math.log(2.0)           # mean noise power per bin
        band[h] = P / np.maximum(noise, 1e-30)[:, None]
    return _Spectra(centers=centers, band=band)


# ---- frame geometry ---------------------------------------------------------------------------

def frame_centers(duration_s: float, p: EnfParams) -> np.ndarray:
    """Centres of the analysis frames: whole seconds from ceil(window/2) on."""
    t0 = float(math.ceil(p.window_s / 2))
    last = duration_s - p.window_s / 2
    if last < t0:
        return np.zeros(0)
    n = int(math.floor((last - t0) / p.hop_s)) + 1
    return t0 + np.arange(n) * p.hop_s


def _runs(centers: np.ndarray, max_len: int) -> list[np.ndarray]:
    """Split frame centres into contiguous runs of at most ``max_len`` frames."""
    if len(centers) == 0:
        return []
    cut = np.where(np.diff(centers) > 1.5)[0] + 1
    out = []
    for run in np.split(centers, cut):
        for i in range(0, len(run), max_len):
            out.append(run[i:i + max_len])
    return out


def _detection_subset(centers: np.ndarray, p: EnfParams) -> np.ndarray:
    """At most ``detect_max_s`` seconds of frames, as runs spread over the recording."""
    n_max = int(p.detect_max_s / p.hop_s)
    if len(centers) <= n_max:
        return centers
    runs = 10
    per = n_max // runs
    starts = np.linspace(0, len(centers) - per, runs).astype(int)
    return np.concatenate([centers[s:s + per] for s in starts])


# ---- detection ---------------------------------------------------------------------------------

@dataclass
class _HarmonicEvidence:
    excess: float        # band power / noise - 1, averaged over the recording
    z: float             # excess in units of its spread under noise alone
    snr_db: float        # implied SNR of the hum line in one analysis bin


def _evidence(sp_list: list[_Spectra], h: int, p: EnfParams) -> _HarmonicEvidence:
    n_frames = sum(len(s.centers) for s in sp_list)
    mean = float(np.mean(np.concatenate([s.band[h].mean(axis=1) for s in sp_list])))
    excess = mean - 1.0
    bins = _independent_bins(h, p)
    n_indep = max(1.0, n_frames * p.hop_s / (p.window_s / 2)) * bins    # Hann frames overlap
    z = excess * math.sqrt(n_indep)
    line = excess * bins
    snr_db = 10 * math.log10(line) if line > 1e-3 else -30.0
    return _HarmonicEvidence(excess, z, snr_db)


def _discriminating(nominal: int, h: int) -> bool:
    return nominal * h != SHARED_HZ


def detect(audio: AudioSource, p: EnfParams = EnfParams()) -> tuple[int | None, float, dict[int, dict[int, _HarmonicEvidence]]]:
    """Which grid's hum is in the recording: (50 | 60 | None, confidence, evidence per nominal and
    harmonic)."""
    nom, conf, ev, _ = _detect(audio, p)
    return nom, conf, ev


def _block_len(p: EnfParams) -> int:
    return max(60, int(p.block_s / p.hop_s))


def _detect(audio: AudioSource, p: EnfParams):
    """``detect`` plus the spectra it computed ({nominal: [_Spectra per block]}) when it looked at
    every frame, so a short recording is not analysed twice."""
    all_centers = frame_centers(audio.duration_s, p)
    centers = _detection_subset(all_centers, p)
    ev: dict[int, dict[int, _HarmonicEvidence]] = {}
    if len(centers) == 0:
        return None, 0.0, {50: {}, 60: {}}, None
    hs = list(range(1, p.harmonics + 1))
    complete = len(centers) == len(all_centers)
    cache: dict[int, list[_Spectra]] = {}
    for nominal in NOMINALS:
        sps = [_block_spectra(audio, nominal, hs, run, p) for run in _runs(centers, _block_len(p))]
        ev[nominal] = {h: _evidence(sps, h, p) for h in hs}
        if complete:
            cache[nominal] = sps
    score = {nom: sum(max(0.0, e.z) for h, e in ev[nom].items() if _discriminating(nom, h)) for nom in NOMINALS}
    best = max(score, key=score.get)
    other = 60 if best == 50 else 50
    top_z = max(e.z for h, e in ev[best].items() if _discriminating(best, h))
    cache = cache or None
    if score[best] < p.detect_z or top_z < p.detect_max_z:
        return None, float(np.clip(1.0 - score[best] / p.detect_z, 0.0, 1.0)), ev, cache
    margin = (score[best] - score[other]) / score[best]
    strength = 1.0 - math.exp(-score[best] / (3 * p.detect_z))
    return best, float(np.clip(margin * strength, 0.0, 1.0)), ev, cache


# ---- tracking ----------------------------------------------------------------------------------

def viterbi(C: np.ndarray, max_step: int, penalty: np.ndarray) -> np.ndarray:
    """Index path maximising sum_k C[k, i_k] - penalty[i_k - i_{k-1} + max_step]."""
    K, G = C.shape
    J = max_step
    ptr = np.zeros((K, G), np.int8)
    score = C[0].astype(float).copy()
    for k in range(1, K):
        padded = np.concatenate([np.full(J, -np.inf), score, np.full(J, -np.inf)])
        win = sliding_window_view(padded, 2 * J + 1) - penalty[None, :]   # win[g, j]: from state g + j - J
        j = np.argmax(win, axis=1)
        score = win[np.arange(G), j] + C[k]
        score -= score.max()
        ptr[k] = j - J
    path = np.empty(K, int)
    path[-1] = int(np.argmax(score))
    for k in range(K - 1, 0, -1):
        path[k - 1] = path[k] + ptr[k, path[k]]
    return path


def _refine(C: np.ndarray, path: np.ndarray, grid_hz: float, reach: int = 2) -> np.ndarray:
    """Sub-grid position (in grid units) of the local maximum of each row near the path."""
    K, G = C.shape
    rows = np.arange(K)
    offs = np.arange(-reach, reach + 1)
    cand = np.clip(path[:, None] + offs[None, :], 0, G - 1)
    k = cand[rows, np.argmax(C[rows[:, None], cand], axis=1)]
    kk = np.clip(k, 1, G - 2)
    lc = np.log(np.maximum(C, 1e-12))
    a, b, c = lc[rows, kk - 1], lc[rows, kk], lc[rows, kk + 1]
    den = a - 2 * b + c
    with np.errstate(invalid="ignore", divide="ignore"):
        off = np.where(den < -1e-12, 0.5 * (a - c) / den, 0.0)
    return np.where((k == 0) | (k == G - 1), k, kk + np.clip(off, -0.5, 0.5)).astype(float)


def _track_block(sp: _Spectra, weights: dict[int, float], p: EnfParams) -> tuple[np.ndarray, np.ndarray]:
    """(deviation from nominal in Hz, evidence in dB above noise) for one block of frames."""
    hs = [h for h, w in weights.items() if w > 0]
    wsum = sum(weights[h] for h in hs)
    C = sum(weights[h] * sp.band[h] for h in hs) / wsum              # noise-only expectation: 1
    # frames overlap (hop << window): scale so the prior is not swamped by correlated evidence
    scale = p.hop_s / (p.window_s / 2)
    J = max(1, int(round(p.max_step_hz / p.grid_hz)))
    steps = np.arange(-J, J + 1) * p.grid_hz
    penalty = 0.5 * (steps / p.step_sigma_hz) ** 2
    path = viterbi(C * scale * wsum, J, penalty)
    pos = _refine(C, path, p.grid_hz)
    evidence = C[np.arange(len(path)), path]
    return delta_grid(p)[0] + pos * p.grid_hz, evidence


def extract_trace(audio: AudioSource, nominal: int | str = "auto", p: EnfParams = EnfParams(),
                  progress: Progress | None = None) -> EnfTrace:
    """Detect the nominal (unless given) and extract the ENF trace of the whole recording."""
    progress = progress or (lambda f, m: None)
    centers = frame_centers(audio.duration_s, p)
    if len(centers) < 30:
        raise ValueError(f"the recording is too short for ENF analysis (need at least {p.window_s + 30:.0f} s)")
    progress(0.02, "Looking for mains hum")
    det_nom, conf, ev, cache = _detect(audio, p)
    notes: list[str] = []
    if nominal == "auto":
        if det_nom is None:
            notes.append("No mains hum found at 50 or 60 Hz or their harmonics.")
            return EnfTrace(None, conf, _harmonic_stats(ev.get(50, {}), 50, {}, None, p), float(centers[0]), p.hop_s,
                            np.full(len(centers), np.nan), np.full(len(centers), np.nan), notes)
        f_nom = det_nom
    else:
        f_nom = int(nominal)
        if f_nom not in NOMINALS:
            raise ValueError("nominal must be 'auto', 50 or 60")
        if det_nom is not None and det_nom != f_nom:
            notes.append(f"The hum looks more like a {det_nom} Hz grid, but {f_nom} Hz was requested.")
        conf = conf if det_nom == f_nom else 0.0
    weights = {h: _weight(e, h, p) for h, e in ev[f_nom].items() if e.z >= p.use_z and e.excess > 0}
    if not weights:
        best_h = max(ev[f_nom], key=lambda h: ev[f_nom][h].z)
        weights = {best_h: max(_weight(ev[f_nom][best_h], best_h, p), 1e-3)}
        notes.append("No harmonic is clearly above the noise; the trace is unreliable.")
    f, snr_db = _extract(audio, f_nom, weights, centers, p, progress, (cache or {}).get(f_nom))
    trace = EnfTrace(f_nom, conf, _harmonic_stats(ev[f_nom], f_nom, weights, f, p), float(centers[0]), p.hop_s,
                     f, snr_db, notes)
    trace.notes.extend(_describe(trace, sorted(weights)))
    return trace


def _weight(e: _HarmonicEvidence, h: int, p: EnfParams) -> float:
    """Log-likelihood-ratio weight of a harmonic whose line SNR per bin is s2: s2 / (1 + s2)."""
    s2 = max(e.excess, 0.0) * _independent_bins(h, p)
    return s2 / (1.0 + s2)


def _extract(audio: AudioSource, f_nom: int, weights: dict[int, float], centers: np.ndarray, p: EnfParams,
             progress: Progress, cached: list[_Spectra] | None = None) -> tuple[np.ndarray, np.ndarray]:
    dev = np.full(len(centers), np.nan)
    evid = np.zeros(len(centers))
    blocks = _runs(centers, _block_len(p))
    if cached is not None and [len(c.centers) for c in cached] != [len(b) for b in blocks]:
        cached = None
    i0 = 0
    for bi, run in enumerate(blocks):
        progress(0.05 + 0.9 * bi / len(blocks), f"Tracking the hum ({bi + 1}/{len(blocks)})")
        sp = cached[bi] if cached is not None else _block_spectra(audio, f_nom, list(weights), run, p)
        d, e = _track_block(sp, weights, p)
        dev[i0:i0 + len(run)] = d
        evid[i0:i0 + len(run)] = e
        i0 += len(run)
    # evidence along the path, averaged over half a window, against what noise alone gives
    sm = uniform_filter1d(evid, max(1, int(p.window_s / 2 / p.hop_s)), mode="nearest")
    snr_db = 10 * np.log10(np.maximum(sm - 1.0, 1e-6))
    f = f_nom + dev
    edge = np.abs(dev) >= p.band_hz - 1.5 * p.grid_hz
    f[(snr_db < p.min_path_snr_db) | edge] = np.nan
    f = _drop_short_runs(f, p.min_run_s)
    progress(0.97, "ENF extracted")
    return f, snr_db


def _drop_short_runs(f: np.ndarray, min_run: int) -> np.ndarray:
    ok = np.isfinite(f)
    if min_run <= 1 or not ok.any():
        return f
    f = f.copy()
    d = np.diff(np.concatenate([[0], ok.astype(int), [0]]))
    for s, e in zip(np.where(d == 1)[0], np.where(d == -1)[0]):
        if e - s < min_run:
            f[s:e] = np.nan
    return f


# ---- reporting ------------------------------------------------------------------------------------

def _harmonic_stats(ev: dict[int, _HarmonicEvidence], f_nom: int, weights: dict[int, float],
                    f: np.ndarray | None, p: EnfParams) -> list[HarmonicStats]:
    med = float(np.nanmedian(f)) if f is not None and np.isfinite(f).any() else float(f_nom)
    out = []
    for h in range(1, p.harmonics + 1):
        e = ev.get(h)
        out.append(HarmonicStats(n=h, freq_hz=round(h * med, 4), snr_db=round(e.snr_db, 1) if e else -30.0,
                                 used=h in weights, z=round(e.z, 1) if e else 0.0))
    return out


def _describe(tr: EnfTrace, used: list[int]) -> list[str]:
    notes = [f"Hum of a {tr.nominal_hz} Hz grid; harmonics used: {', '.join(f'{h * tr.nominal_hz} Hz' for h in used)}."]
    q = tr.quality
    notes.append(f"{q * 100:.0f}% of the seconds gave a usable ENF value.")
    if q < 0.3:
        notes.append("Most of the recording has no usable hum; matching may fail or be ambiguous.")
    if np.isfinite(tr.f).sum() > 120:
        sd = float(np.nanstd(tr.f)) * 1000
        if sd < 1.5:
            notes.append(f"The hum frequency hardly varies (std {sd:.1f} mHz). It may come from an inverter, "
                         "a generator or a quartz-controlled device rather than the grid.")
    notes.append("The recorder's clock error (typically within +-50 ppm) shifts all frequencies by up to "
                 f"+-{tr.nominal_hz * 50e-6 * 1000:.1f} mHz; matching ignores such a constant offset.")
    return notes
