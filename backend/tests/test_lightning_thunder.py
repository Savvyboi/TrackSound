"""Thunder onset detection on synthetic audio."""
from __future__ import annotations

import numpy as np
import pytest
import soundfile as sf

from tracksound.sources.lightning import synth, thunder


@pytest.fixture(scope="module")
def storm_wav(tmp_path_factory):
    storm = synth.make_storm(seed=3, c=347.9)
    x = synth.render_audio(storm, sr=4000, seed=5, n_false_thunder=1, n_trucks=1, n_slams=3)
    p = tmp_path_factory.mktemp("thunder") / "storm.wav"
    sf.write(str(p), x, 4000, subtype="FLOAT")
    return storm, p


def _isolated(times, gap=6.0):
    """True onsets with no other thunder within `gap` s before them (overlaps are hard by nature)."""
    times = sorted(times)
    return [t for i, t in enumerate(times) if i == 0 or t - times[i - 1] > gap]


def test_detects_thunder_onsets_precisely(storm_wav):
    storm, p = storm_wav
    env = thunder.compute_envelope(p)
    found = thunder.detect_onsets(env, p, sensitivity=0.5, min_gap_s=4.0)
    det = np.array([o["t"] for o in found])
    truth = _isolated(storm.onset_times())
    hits = [float(np.min(np.abs(det - t))) for t in truth]
    assert sum(h < 0.3 for h in hits) >= 0.8 * len(truth)
    assert np.sqrt(np.mean([h * h for h in hits if h < 0.3])) < 0.12
    # every detection is thunder or one of the thunder-like distractors (burst, truck); slams are rejected
    known = storm.onset_times() + storm.false_onsets + [t for k, t in storm.distractors if k == "truck"]
    extra = [t for t in det if min(abs(t - k) for k in known) > 2.0]
    assert len(extra) <= 2
    slams = [t for k, t in storm.distractors if k == "slam"]
    assert all(np.min(np.abs(det - s)) > 0.5 for s in slams)
    for o in found:
        assert 0.05 <= o["sigma_s"] <= 2.0 and o["strength"] > 0
        assert o["duration_s"] is None or o["duration_s"] > 0


def test_min_gap_and_sensitivity(storm_wav):
    _, p = storm_wav
    env = thunder.compute_envelope(p)
    loose = thunder.detect_onsets(env, p, sensitivity=0.9, min_gap_s=4.0)
    strict = thunder.detect_onsets(env, p, sensitivity=0.1, min_gap_s=4.0)
    wide = thunder.detect_onsets(env, p, sensitivity=0.5, min_gap_s=30.0)
    assert len(loose) >= len(strict)
    t = [o["t"] for o in wide]
    assert all(b - a >= 15.0 for a, b in zip(t, t[1:]))


def test_clicks_and_rain_alone_give_nothing(tmp_path):
    rng = np.random.default_rng(1)
    sr = 4000
    x = synth.rain_noise(sr * 180, sr, rng, rms=0.01, drops_per_s=6.0)
    for t in (20.0, 61.5, 100.0, 140.2):
        slam = synth.door_slam(sr, rng, amp=0.4)
        i = int(t * sr)
        x[i:i + len(slam)] += slam
    p = tmp_path / "rain.wav"
    sf.write(str(p), x.astype(np.float32), sr, subtype="FLOAT")
    env = thunder.compute_envelope(p)
    assert thunder.detect_onsets(env, p, sensitivity=0.5) == []


def test_envelope_is_streamed_exactly(tmp_path):
    rng = np.random.default_rng(2)
    x = (0.1 * rng.standard_normal(4000 * 95)).astype(np.float32)
    p = tmp_path / "noise.wav"
    sf.write(str(p), x, 4000, subtype="FLOAT")
    a = thunder.compute_envelope(p, chunk_s=7.0)
    b = thunder.compute_envelope(p, chunk_s=1000.0)
    assert a.ms.shape == b.ms.shape == (2, 1900)
    np.testing.assert_allclose(a.ms, b.ms, rtol=1e-5, atol=1e-12)


def test_envelope_cache_and_pooling(storm_wav, tmp_path):
    _, p = storm_wav
    cache = tmp_path / "env.npz"
    env = thunder.load_or_build_envelope(p, cache)
    assert cache.exists()
    again = thunder.load_or_build_envelope(p, cache)
    np.testing.assert_array_equal(env.ms, again.ms)
    out = thunder.pooled_envelope(env, 100.0, 400.0, max_points=500)
    n = len(out["t"])
    assert 250 <= n <= 500 and len(out["onset_strength"]) == n
    assert [b["name"] for b in out["bands"]] == ["low", "mid"]
    assert all(len(b["db"]) == n for b in out["bands"])
    assert out["t"][0] >= 100.0 and out["t"][-1] <= 400.0
    # max pooling keeps the loudest frame
    db = env.db()[0]
    sel = (env.times >= 100.0) & (env.times <= 400.0)
    assert abs(max(out["bands"][0]["db"]) - float(db[sel].max())) < 0.01
    full = thunder.pooled_envelope(env, max_points=100000)
    assert len(full["t"]) == env.n_frames
