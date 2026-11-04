"""Helicopter main rotors: blade-pass tones of 10-40 Hz, below the propeller band.
The spectrogram starts at 10 Hz so they can be fitted with harmonics."""
import numpy as np
from dopplertrack import audio, doppler, tracks

from tracksound.sources.aircraft import synth
from tracksound.spectrogram import SPEC_PARAMS


def test_fit_helicopter_rotor_with_harmonics():
    c, start, sr = 340.3, 1.7e9, 4000
    p = synth.PlannedPass(t_cpa=180.0, lateral_m=1500.0, alt_m=300.0, v_mps=55.0, heading_deg=40.0, turn_dps=0.01, f0=18.0)
    _, dense = synth.make_world(40.0, -74.0, start, 400.0, [p], n_background=0)
    t_unix, enu, f0 = dense[0]
    n = int(400 * sr)
    tt = start + np.arange(n) / sr
    x = (0.3 * np.random.default_rng(1).standard_normal(n)).astype(np.float32)
    to, ratio, r = doppler.curve_from_trajectory(t_unix, enu, np.zeros(3), f0=1.0, c=c)
    finst = np.interp(tt, to, ratio * f0, left=np.nan, right=np.nan)
    amp = np.interp(tt, to, np.clip(1500.0 / r, 0, 3.0), left=0, right=0) * 0.06
    m = np.isfinite(finst)
    ph = 2 * np.pi * np.cumsum(np.nan_to_num(finst)) / sr
    for h, a in ((1, 1.0), (2, 0.8), (3, 0.6)):          # rotor harmonics
        x[m] += (amp[m] * a * np.sin(h * ph[m])).astype(np.float32)
    sp = audio.compute_spectrogram(x, sr, **SPEC_PARAMS)
    assert sp.f[0] < 12
    heard = 180 + np.hypot(1500, 300) / c
    ev = tracks.fit_event(sp, heard - 100, heard + 100, 10, 60, c=c, harmonics=3)
    assert abs(ev.t_cpa - heard) < 5 and abs(ev.f0 - 18.0) < 0.5
    assert abs(ev.v - 55) < 10 and 1000 < ev.d < 2200 and ev.score > 2.5
