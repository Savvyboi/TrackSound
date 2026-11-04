"""ENF extraction on synthetic recordings: which grid, how accurate, where unreliable."""
from __future__ import annotations

import numpy as np
import pytest

from tracksound.sources.enf import extract as ex
from tracksound.sources.enf import synth
from tracksound.sources.enf.match import smooth_like_extractor

SR = 4000


def recording(enf: np.ndarray, seconds: float, hum: float, offset: float = 100.0, seed: int = 0,
              ppm: float = 0.0, white: float = 1.0, rumble: float = 1.0) -> np.ndarray:
    y = hum * synth.hum_signal(enf, SR, seconds, offset_s=offset, seed=seed, clock_ppm=ppm)
    return y + synth.room_noise(len(y), SR, seed=seed, white=white, rumble=rumble)


@pytest.fixture(scope="module")
def enf50():
    return synth.random_walk_enf(4000, 50.0, seed=11)


def truth_at(enf: np.ndarray, tr: ex.EnfTrace, offset: float, window_s: float) -> np.ndarray:
    t = offset + tr.t0 + np.arange(len(tr.f)) * tr.dt
    return np.interp(t, np.arange(len(enf)), smooth_like_extractor(enf, window_s))


def test_detects_50_hz_and_follows_the_grid(enf50):
    y = recording(enf50, 600, hum=0.1, seed=1)
    tr = ex.extract_trace(ex.AudioSource(y, SR))
    assert tr.nominal_hz == 50 and tr.confidence > 0.7
    assert tr.quality > 0.95
    used = {h.n for h in tr.harmonics if h.used}
    assert {2, 3} <= used                         # the strong harmonics carry the trace
    err = tr.f - truth_at(enf50, tr, 100.0, ex.EnfParams().window_s)
    err = err[np.isfinite(err)]
    assert np.sqrt(np.mean((err - np.median(err)) ** 2)) < 0.003     # a few mHz at this SNR
    assert any("clock" in n for n in tr.notes)


def test_detects_60_hz():
    enf60 = synth.random_walk_enf(1200, 60.0, seed=5, std_hz=0.02)
    y = recording(enf60, 400, hum=0.1, seed=2)
    tr = ex.extract_trace(ex.AudioSource(y, SR))
    assert tr.nominal_hz == 60 and tr.quality > 0.9
    assert all(abs(h.freq_hz - 60 * h.n) < 0.2 * h.n for h in tr.harmonics)


def test_no_hum_gives_null():
    rng = np.random.default_rng(3)
    y = synth.room_noise(400 * SR, SR, seed=3) + 0.0 * rng.standard_normal(400 * SR)
    tr = ex.extract_trace(ex.AudioSource(y, SR))
    assert tr.nominal_hz is None
    assert tr.quality == 0.0 and not np.isfinite(tr.f).any()


def test_forced_nominal_and_clock_error(enf50):
    # a recorder clock 40 ppm fast lowers every frequency by 40 ppm (2 mHz): a constant offset
    y = recording(enf50, 400, hum=0.2, seed=4, ppm=40.0)
    tr = ex.extract_trace(ex.AudioSource(y, SR), nominal=50)
    truth = truth_at(enf50, tr, 100.0, ex.EnfParams().window_s)
    d = (tr.f - truth)[np.isfinite(tr.f)]
    assert abs(np.median(d) + 50 * 40e-6) < 0.001
    assert np.std(d) < 0.002


def test_gap_without_hum_is_null(enf50):
    y = recording(enf50, 600, hum=0.15, seed=6)
    hum_only = 0.15 * synth.hum_signal(enf50, SR, 600, offset_s=100.0, seed=6)
    a, b = 250 * SR, 400 * SR
    y[a:b] -= hum_only[a:b]                       # the hum disappears for 150 s
    tr = ex.extract_trace(ex.AudioSource(y, SR))
    t = tr.t0 + np.arange(len(tr.f))
    inside = (t > 270) & (t < 380)
    outside = (t < 230) | (t > 420)
    assert np.isfinite(tr.f[inside]).mean() < 0.2
    assert np.isfinite(tr.f[outside]).mean() > 0.9


def test_reads_long_files_in_blocks(tmp_path, enf50):
    import soundfile as sf
    y = recording(enf50, 800, hum=0.1, seed=7)
    p = tmp_path / "long.wav"
    sf.write(str(p), y.astype(np.float32), SR, subtype="FLOAT")
    p_small = ex.EnfParams(block_s=200.0, detect_max_s=300.0)      # several blocks, sampled detection
    tr = ex.extract_trace(ex.AudioSource(path=p), nominal=50, p=p_small)
    assert len(tr.f) == len(ex.frame_centers(800, p_small)) and tr.quality > 0.95
    err = tr.f - truth_at(enf50, tr, 100.0, p_small.window_s)
    assert np.nanstd(err) < 0.003


def test_too_short_is_an_error():
    with pytest.raises(ValueError):
        ex.extract_trace(ex.AudioSource(np.zeros(20 * SR), SR))


def test_viterbi_follows_a_smooth_ridge():
    rng = np.random.default_rng(0)
    K, G = 200, 101
    truth = (50 + 20 * np.sin(np.arange(K) / 30)).astype(int)
    C = rng.exponential(1.0, (K, G))
    C[np.arange(K), truth] += 3.0
    path = ex.viterbi(C, 3, 0.1 * np.arange(-3, 4) ** 2)
    assert np.mean(np.abs(path - truth) <= 1) > 0.9
