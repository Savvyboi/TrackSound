"""Lightning solver on synthetic storms: known start, flash sync and free clock."""
from __future__ import annotations

import numpy as np
import pytest

from tracksound.physics import haversine_m
from tracksound.sources.lightning import catalogue, flash_sync, solve as solver, synth
from tracksound.sources.lightning.geo import square_bbox

C = 347.9


def scenario(seed=3, sigma_m=300.0, kind="cg", n_false=2, pick_sigma=0.1):
    storm = synth.make_storm(seed=seed, sigma_m=sigma_m, n_strikes=40, c=C, kind=kind)
    rng = np.random.default_rng(seed + 100)
    onsets = [{"id": f"lo_{i}", "t": t + rng.normal(0, pick_sigma), "sigma_s": 0.15, "status": "accepted",
               "origin": "detected"} for i, t in enumerate(storm.onset_times())]
    for k in range(n_false):     # false detections
        onsets.append({"id": f"lo_x{k}", "t": float(rng.uniform(5, storm.duration_s - 5)), "sigma_s": 0.3,
                       "status": "accepted", "origin": "detected"})
    # a rejected onset must be ignored
    onsets.append({"id": "lo_rej", "t": 100.0, "sigma_s": 0.1, "status": "rejected", "origin": "detected"})
    region = square_bbox(storm.lat + 0.05, storm.lon - 0.07, 27000.0)    # 54 km box, truth off-centre
    return storm, onsets, region


def run(storm, onsets, region, flashes=(), **kw):
    res = solver.solve(onsets, list(flashes), storm.catalogue(), c=C, duration_s=storm.duration_s, region=region, **kw)
    b = res["best"]
    return res, float(haversine_m(b["lat"], b["lon"], storm.lat, storm.lon)), b["start_utc"] - storm.start_utc


def check_doc(res):
    assert set(res) == {"mode", "best", "candidates", "heat", "matches", "flash_matches", "ranges", "notes"}
    assert set(res["best"]) == {"lat", "lon", "start_utc", "score", "n_matched", "rms_s", "uncertainty_m"}
    assert 0 < len(res["heat"]) <= 20000 and set(res["heat"][0]) == {"lat", "lon", "score", "cell_m"}
    cells = sorted({h["cell_m"] for h in res["heat"]})
    assert len(cells) >= 2 and cells[0] <= 250.0 + 1e-6          # a coarse grid plus finer cells near the peaks
    assert res["candidates"][0]["score"] == pytest.approx(res["best"]["score"], abs=1e-3)
    for m in res["matches"]:
        assert set(m) == {"onset_id", "strike_id", "residual_s", "distance_m"} and m["onset_id"] != "lo_rej"
    assert all(isinstance(n, str) and n for n in res["notes"])


def test_known_start():
    storm, onsets, region = scenario()
    res, err, dT = run(storm, onsets, region, start_utc=storm.start_utc + 2.0, start_tol_s=5.0)
    check_doc(res)
    assert res["mode"] == "known_start"
    assert err < 1000 and abs(dT) < 1.5
    assert res["best"]["n_matched"] >= 0.8 * len(storm.onset_times())
    assert res["best"]["uncertainty_m"] < 1500


def test_flash_sync_with_missing_and_extra_flashes():
    storm, onsets, region = scenario(seed=4)
    inside = [s for s in storm.strikes if storm.start_utc + 2 < s.t_utc < storm.start_utc + storm.duration_s - 2]
    flashes = [{"id": f"lf_{i}", "t": s.t_utc - storm.start_utc + 0.03, "status": "accepted"}
               for i, s in enumerate(inside[1:8:2])]
    flashes.append({"id": "lf_false", "t": 123.4, "status": "accepted"})          # a car's headlights
    flashes.append({"id": "lf_cand", "t": 222.2, "status": "candidate"})         # not accepted: ignored
    res, err, dT = run(storm, onsets, region, flashes=flashes)
    check_doc(res)
    assert res["mode"] == "flash_sync"
    assert err < 1000 and abs(dT + 0.03) < 0.05          # the flashes' common +30 ms lag goes into the clock
    assert {m["flash_id"] for m in res["flash_matches"]} == {f["id"] for f in flashes[:4]}
    assert all(abs(m["residual_s"]) < 0.02 for m in res["flash_matches"])
    for r in res["ranges"]:                        # flash-to-bang ranges are the strike distances
        s = next(x for x in storm.strikes if abs(x.lat_cat - r["lat"]) < 1e-5 and abs(x.lon_cat - r["lon"]) < 1e-5)
        assert abs(r["distance_m"] - s.distance_m) < 600


def test_free_clock():
    storm, onsets, region = scenario(seed=5)
    res, err, dT = run(storm, onsets, region, date="2025-07-19")
    check_doc(res)
    assert res["mode"] == "free"
    assert err < 1000 and abs(dT) < 1.5


def test_glm_like_catalogue_gives_a_few_km():
    storm, onsets, region = scenario(seed=6, sigma_m=4000.0, kind="flash")
    res, err, dT = run(storm, onsets, region, start_utc=storm.start_utc, start_tol_s=5.0)
    assert err < 5000 and abs(dT) <= 5.0 + 1e-6
    assert res["best"]["uncertainty_m"] > 500
    assert any("4 km" in n for n in res["notes"])
    res, err, dT = run(storm, onsets, region)
    assert res["mode"] == "free" and err < 6000 and abs(dT) < 20


def test_unaccepted_candidates_are_used_with_a_note():
    storm, onsets, region = scenario(seed=3)
    for o in onsets:
        if o["status"] == "accepted":
            o["status"] = "candidate"
    res, err, _ = run(storm, onsets, region, start_utc=storm.start_utc)
    assert err < 1000 and any("No onsets were accepted" in n for n in res["notes"])


def test_bad_input():
    storm, onsets, region = scenario()
    with pytest.raises(ValueError, match="no onsets"):
        solver.solve([], [], storm.catalogue(), c=C, duration_s=900, region=region)
    with pytest.raises(ValueError, match="no events"):
        solver.solve(onsets, [], [], c=C, duration_s=900, region=region)
    # no strike anywhere near the region: an empty result, not an error
    far = square_bbox(storm.lat + 3.0, storm.lon, 20000.0)
    res = solver.solve(onsets, [], storm.catalogue(), c=C, duration_s=900, region=far, start_utc=storm.start_utc)
    assert res["best"] is None and res["notes"]


def test_mode_choice():
    assert solver.choose_mode(123.0, 3, True) == "known_start"
    assert solver.choose_mode(None, 3, True) == "flash_sync"
    assert solver.choose_mode(None, 3, False) == "free"
    assert solver.choose_mode(None, 0, True) == "free"


def test_flash_sync_votes():
    rng = np.random.default_rng(0)
    t = np.sort(rng.uniform(0, 3600, 300)) + 1.75e9
    strikes = catalogue.assign_ids([catalogue.make_strike(x, 28.0, -82.0, 4000, None, "flash", "glm-G16") for x in t])
    sa = catalogue.to_arrays(strikes)
    T = 1.75e9 + 1000.0
    f = np.array([sa.t[100] - T, sa.t[120] - T, sa.t[150] - T, 77.7])      # three real flashes, one false
    region = square_bbox(28.0, -82.0, 20000)
    # with ~one strike per 12 s and a 0.25 s tolerance, chance offsets can match as many flashes
    # as the true one (the thunder decides in the solve); the true offset must be a candidate
    cands, info = flash_sync.sync_candidates(f, sa, 0.25, region, 25000)
    true = [c for c in cands if abs(c["T"] - T) < 1e-3]
    assert true and true[0]["count"] == 3 and true[0]["sigma"] == flash_sync.MIN_SIGMA_S
    # a tight tolerance removes the chance coincidences
    cands, info = flash_sync.sync_candidates(f, sa, 0.02, region, 25000)
    assert abs(cands[0]["T"] - T) < 1e-3 and cands[0]["count"] == 3 and info["best_count"] == 3


def test_dense_storm_with_coarse_catalogue_is_reported_as_ambiguous():
    """~30 strikes a minute with 4 km errors: every onset fits several strikes anywhere nearby."""
    storm = synth.make_storm(seed=8, sigma_m=4000.0, n_strikes=600, c=C, kind="flash", spread_km=12.0)
    rng = np.random.default_rng(8)
    heard = rng.choice(storm.onset_times(), size=30, replace=False)        # a user's 30 clearest onsets
    onsets = [{"id": f"lo_{i}", "t": t + rng.normal(0, 0.1), "sigma_s": 0.2, "status": "accepted",
               "origin": "manual"} for i, t in enumerate(sorted(heard))]
    region = square_bbox(storm.lat, storm.lon, 25000.0)
    res = solver.solve(onsets, [], storm.catalogue(), c=C, duration_s=storm.duration_s, region=region,
                       start_utc=storm.start_utc)
    assert any("not distinctive" in n for n in res["notes"])
    assert res["best"]["uncertainty_m"] > 10000


def test_exact_start_and_no_region():
    storm, onsets, _ = scenario(seed=3)
    res, err, dT = run(storm, onsets, None, start_utc=storm.start_utc, start_tol_s=0.0)
    assert res["mode"] == "known_start" and abs(dT) < 0.02 and err < 1000
    assert any("no search region" in n for n in res["notes"])
