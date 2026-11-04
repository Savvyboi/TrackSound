"""ENF matching: masked correlation, and recovering a recording's start time from its hum."""
from __future__ import annotations

import numpy as np
import pytest

from tracksound.sources.enf import extract as ex
from tracksound.sources.enf import match as mt
from tracksound.sources.enf import synth

SR = 4000
T0 = 1_780_000_000.0          # UTC of the reference's first sample


def test_masked_xcorr_equals_brute_force():
    rng = np.random.default_rng(1)
    ref = rng.standard_normal(400)
    rec = ref[150:230] + 0.3 * rng.standard_normal(80)
    ref[rng.random(400) < 0.1] = np.nan
    rec[rng.random(80) < 0.1] = np.nan
    c, n = mt.masked_xcorr(rec, ref)
    for L in (-20, 0, 37, 150, 300, 330):
        i = L + len(rec) - 1
        seg = np.full(len(rec), np.nan)
        a, b = max(0, L), min(len(ref), L + len(rec))
        seg[a - L:b - L] = ref[a:b]
        ok = np.isfinite(rec) & np.isfinite(seg)
        assert n[i] == ok.sum()
        assert c[i] == pytest.approx(np.corrcoef(rec[ok], seg[ok])[0, 1], abs=1e-9)
    assert int(np.nanargmax(np.where(n >= 40, c, np.nan))) - (len(rec) - 1) == 150


def test_distinct_peaks_are_separated():
    c = np.array([0, 1, 5, 4, 0, 0, 3, 0, 0, 2, 0], float)
    assert mt.distinct_peaks(c, 2, 3) == [2, 6, 9]


@pytest.fixture(scope="module")
def grid():
    """Two days of synthetic grid frequency and a 20 minute recording made from it."""
    ref = synth.random_walk_enf(2 * 86400, 50.0, seed=21, std_hz=0.06, tau_s=650)
    offset = 61_234.0
    y = 0.08 * synth.hum_signal(ref, SR, 1200, offset_s=offset, seed=3, clock_ppm=-25.0)
    y += synth.room_noise(len(y), SR, seed=3)
    y[300 * SR:420 * SR] *= 0.0                   # the recorder paused: two minutes of silence
    tr = ex.extract_trace(ex.AudioSource(y, SR))
    return ref, offset, tr


def test_recovers_start_time(grid):
    ref, offset, tr = grid
    assert tr.nominal_hz == 50
    ref = ref.copy()
    ref[100_000:100_600] = np.nan                 # gaps in the reference are fine too
    p = mt.params_for_trace(tr.f, ex.EnfParams().window_s)
    res = mt.match_trace(tr.f, tr.t0, ref, T0, T0, T0 + len(ref) - 1200, p)
    assert abs(res.start_utc - (T0 + offset)) <= 1.0
    assert res.z > 8 and res.corr > 0.5
    assert res.top[0]["start_utc"] == pytest.approx(T0 + offset, abs=1.0)
    assert len(res.top) == 5 and res.top[1]["corr"] < res.corr
    assert "stands out clearly" in " ".join(res.notes)
    ov = res.overlay
    assert len(ov["t"]) == len(ov["rec"]) == len(ov["ref"]) == len(tr.f)
    both = [(a, b) for a, b in zip(ov["rec"], ov["ref"]) if a is not None and b is not None]
    assert np.corrcoef(np.array(both).T)[0, 1] > 0.9


def test_every_method_finds_it(grid):
    ref, offset, tr = grid
    for method in mt.METHODS:
        p = mt.MatchParams(method=method)
        res = mt.match_trace(tr.f, tr.t0, ref, T0, T0, T0 + len(ref) - 1200, p)
        assert abs(res.start_utc - (T0 + offset)) <= 1.5, method


def test_window_limits_the_candidates(grid):
    ref, offset, tr = grid
    res = mt.match_trace(tr.f, tr.t0, ref, T0, T0 + 3600, T0 + 7200, mt.params_for_trace(tr.f, 16))
    assert T0 + 3600 - 0.5 <= res.start_utc <= T0 + 7200 + 0.5
    assert res.n_offsets <= 3601
    assert "not significant" in " ".join(res.notes)


def test_wrong_grid_is_not_significant(grid):
    _, _, tr = grid
    other = synth.random_walk_enf(86400, 50.0, seed=99, std_hz=0.06, tau_s=650)
    p = mt.params_for_trace(tr.f, 16)
    res = mt.match_trace(tr.f, tr.t0, other, T0, T0, T0 + len(other) - 1200, p)
    assert res.z < mt.chance_z(res.n_offsets, 10.0) + 1.5
    assert "not significant" in " ".join(res.notes)


def test_too_little_trace():
    f = np.full(1000, np.nan)
    f[:50] = 50.0
    res = mt.match_trace(f, 8.0, np.full(5000, 50.0), T0, T0, T0 + 3000)
    assert res.start_utc is None and res.notes


def test_method_follows_trace_quality():
    good = np.full(1200, 50.0)
    patchy = good.copy()
    patchy[::2] = np.nan
    bad = good.copy()
    bad[np.arange(1200) % 5 != 0] = np.nan
    assert mt.params_for_trace(good, 16).method == "diff"
    assert mt.params_for_trace(patchy, 16).method == "diff"        # 600 usable seconds
    assert mt.params_for_trace(patchy[:600], 16).method == "highpass"
    assert mt.params_for_trace(bad, 16).method == "raw"
