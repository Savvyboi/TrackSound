"""Lightning API through the HTTP client: onsets, flashes, strikes, solves, the demo case."""
from __future__ import annotations

import numpy as np
import pytest
import soundfile as sf

from tests.conftest import wait_job
from tests.test_lightning_flashes import make_video
from tracksound.physics import haversine_m
from tracksound.sources.lightning import demo, glm, synth
from tracksound.sources.lightning.geo import square_bbox


@pytest.fixture
def storm_case(client, make_case, tmp_path):
    storm = synth.make_storm(seed=21, duration_s=300.0, n_strikes=24, lead_s=90.0, tail_s=20.0, c=340.3,
                             track_km=(-6, -4, 5, 5), spread_km=3.0)
    x = synth.render_audio(storm, sr=8000, seed=22, n_false_thunder=0, n_trucks=0, n_slams=1)
    wav = tmp_path / "storm.wav"
    sf.write(str(wav), x, 8000, subtype="PCM_16")
    case = make_case(name="storm", wav=wav)
    region = square_bbox(storm.lat + 0.02, storm.lon + 0.03, 20000.0)
    client.patch(f"/api/cases/{case['id']}", json={"region": region, "date": "2025-07-19"})
    return storm, case


def test_onsets_detection_and_editing(client, storm_case):
    storm, case = storm_case
    cid = case["id"]
    base = f"/api/cases/{cid}/lightning"
    empty = client.get(base).json()
    assert empty == {"onsets": [], "flashes": [], "strikes": None, "solves": []}

    j = client.post(f"{base}/detect-thunder", json={"sensitivity": 0.5, "min_gap_s": 4}).json()
    assert j["kind"] == "lightning.detect_thunder"
    j = wait_job(client, j["id"])
    onsets = client.get(base).json()["onsets"]
    assert j["result"]["count"] == len(onsets) >= 0.6 * len(storm.onset_times())
    o = onsets[0]
    assert set(o) == {"id", "t", "sigma_s", "strength", "duration_s", "origin", "status"}
    assert o["id"].startswith("lo_") and o["origin"] == "detected" and o["status"] == "candidate"
    det = np.array([x["t"] for x in onsets])
    assert sum(np.min(np.abs(det - t)) < 0.3 for t in storm.onset_times()) >= 0.6 * len(storm.onset_times())

    env = client.get(f"{base}/envelope", params={"t0": 10, "t1": 250, "max_points": 300}).json()
    assert 0 < len(env["t"]) <= 300 and len(env["onset_strength"]) == len(env["t"])
    assert [b["name"] for b in env["bands"]] == ["low", "mid"] and len(env["bands"][0]["db"]) == len(env["t"])
    assert client.get(f"{base}/envelope", params={"t0": 50, "t1": 40}).status_code == 400

    # manual onset, accept one candidate, reject another, delete one
    m = client.post(f"{base}/onsets", json={"t": 42.5}).json()
    assert m["origin"] == "manual" and m["status"] == "accepted" and m["sigma_s"] == 0.3
    acc = client.patch(f"{base}/onsets/{onsets[0]['id']}", json={"status": "accepted", "t": onsets[0]["t"] + 0.1}).json()
    assert acc["status"] == "accepted" and abs(acc["t"] - onsets[0]["t"] - 0.1) < 1e-6
    client.patch(f"{base}/onsets/{onsets[1]['id']}", json={"status": "rejected"})
    assert client.patch(f"{base}/onsets/{onsets[2]['id']}", json={"status": "maybe"}).status_code == 422
    assert client.delete(f"{base}/onsets/{onsets[2]['id']}").status_code == 204
    assert client.delete(f"{base}/onsets/lo_nothere").status_code == 404
    assert client.patch(f"{base}/onsets/lo_nothere", json={"t": 1}).status_code == 404

    # re-detection replaces candidates and rejections, keeps manual and accepted onsets
    wait_job(client, client.post(f"{base}/detect-thunder").json()["id"])
    after = client.get(base).json()["onsets"]
    ids = {x["id"] for x in after}
    assert m["id"] in ids and acc["id"] in ids and onsets[1]["id"] not in ids
    assert len([x for x in after if abs(x["t"] - acc["t"]) < 1.0]) == 1        # no duplicate next to the kept one
    assert [x["t"] for x in after] == sorted(x["t"] for x in after)


def test_strikes_and_solve(client, storm_case):
    storm, case = storm_case
    cid = case["id"]
    base = f"/api/cases/{cid}/lightning"
    wait_job(client, client.post(f"{base}/detect-thunder").json()["id"])
    assert client.post(f"{base}/solve", json={}).status_code == 400             # no strikes yet

    csv = storm.catalogue_csv().encode()
    r = client.post(f"{base}/strikes/import", files={"file": ("strikes.csv", csv, "text/csv")})
    assert r.status_code == 200 and r.json() == {"count": len(storm.strikes)}
    strikes = client.get(f"{base}/strikes").json()["strikes"]
    assert len(strikes) == len(storm.strikes) and set(strikes[0]) == {"id", "t_utc", "lat", "lon", "sigma_m", "energy",
                                                                      "kind", "source"}
    sset = client.get(base).json()["strikes"]
    assert sset["source"] == "csv" and sset["count"] == len(strikes) and set(sset) == {
        "source", "t0_utc", "t1_utc", "bbox", "count", "fetched_at"}
    bad = client.post(f"{base}/strikes/import", files={"file": ("x.csv", b"a,b\n1,2\n", "text/csv")})
    assert bad.status_code == 400 and "missing" in bad.json()["detail"]

    j = wait_job(client, client.post(f"{base}/solve", json={"spacing_m": 250}).json()["id"])
    sid = j["result"]["solve_id"]
    assert sid.startswith("ls_")
    sol = client.get(f"{base}/solves/{sid}").json()
    assert set(sol) == {"id", "created_at", "request", "mode", "best", "candidates", "heat", "matches",
                        "flash_matches", "ranges", "notes"}
    assert sol["mode"] == "free" and sol["request"]["spacing_m"] == 250
    b = sol["best"]
    assert haversine_m(b["lat"], b["lon"], storm.lat, storm.lon) < 1000 and abs(b["start_utc"] - storm.start_utc) < 2
    assert b["n_matched"] >= 5 and sol["matches"] and sol["notes"]
    summ = client.get(base).json()["solves"]
    assert summ == [{"id": sid, "created_at": sol["created_at"], "mode": "free", "best": b}]

    # known start from the case: mode known_start
    client.patch(f"/api/cases/{cid}", json={"start_utc": storm.start_utc + 1.0})
    j = wait_job(client, client.post(f"{base}/solve", json={"start_tol_s": 3}).json()["id"])
    sol2 = client.get(f"{base}/solves/{j['result']['solve_id']}").json()
    assert sol2["mode"] == "known_start" and abs(sol2["best"]["start_utc"] - storm.start_utc - 1.0) <= 3.0 + 1e-6
    assert [s["id"] for s in client.get(base).json()["solves"]] == [sol2["id"], sid]
    # an explicit null ignores the case's start time (the UI's "use start time" switch off)
    j = wait_job(client, client.post(f"{base}/solve", json={"start_utc": None}).json()["id"])
    assert client.get(f"{base}/solves/{j['result']['solve_id']}").json()["mode"] == "free"

    assert client.delete(f"{base}/solves/{sid}").status_code == 204
    assert client.get(f"{base}/solves/{sid}").status_code == 404
    assert client.delete(f"{base}/solves/{sid}").status_code == 404
    assert client.delete(f"{base}/strikes").status_code == 204
    assert client.get(f"{base}/strikes").json() == {"strikes": []} and client.get(base).json()["strikes"] is None


def test_validation(client, make_case, tmp_path):
    assert client.get("/api/cases/c_missing/lightning").status_code == 404
    bare = client.post("/api/cases", json={"name": "bare"}).json()
    base = f"/api/cases/{bare['id']}/lightning"
    assert client.post(f"{base}/detect-thunder").status_code == 400                # no recording
    assert client.get(f"{base}/envelope").status_code == 404
    t0 = 1752948000.0
    fetch = {"source": "glm", "t0_utc": t0, "t1_utc": t0 + 600}
    assert client.post(f"{base}/strikes/fetch", json=fetch).status_code == 400       # no region, no bbox
    assert client.post(f"{base}/strikes/fetch", json={**fetch, "t1_utc": t0 + 7 * 3600,
                                                       "bbox": square_bbox(28, -82, 1e4)}).status_code == 400
    assert client.post(f"{base}/strikes/fetch", json={**fetch, "t1_utc": t0 - 1,
                                                       "bbox": square_bbox(28, -82, 1e4)}).status_code == 400
    europe = client.post(f"{base}/strikes/fetch", json={**fetch, "bbox": square_bbox(48, 11, 1e4)})
    assert europe.status_code == 400 and "outside" in europe.json()["detail"]
    assert client.post(f"{base}/strikes/fetch", json={**fetch, "source": "blitzortung"}).status_code == 422
    assert client.post(f"{base}/onsets", json={"t": -1}).status_code == 422
    audio = make_case(name="audio-only")
    r = client.post(f"/api/cases/{audio['id']}/lightning/detect-flashes")
    assert r.status_code == 400 and "no video" in r.json()["detail"]
    assert client.post(f"/api/cases/{audio['id']}/lightning/solve").status_code == 400   # no onsets


def test_fetch_job_wiring(client, monkeypatch):
    """The fetch endpoint's job, with the network part replaced (the real download is a slow test)."""
    case = client.post("/api/cases", json={"name": "glm"}).json()
    region = square_bbox(28.0, -82.0, 10000.0)
    client.patch(f"/api/cases/{case['id']}", json={"region": region})
    seen = {}

    def fake_fetch(cache_dir, bbox, t0, t1, satellite="auto", progress=None, **kw):
        seen.update(bbox=bbox, t0=t0, t1=t1, satellite=satellite, cache=cache_dir)
        progress(0.5, "halfway")
        s = [{"id": "st_00000", "t_utc": t0 + 5, "lat": 28.0, "lon": -82.0, "sigma_m": 4000.0, "energy": 1e-14,
              "kind": "flash", "source": "glm-G19"}]
        return s, {"satellites": ["G19"], "n_files": 3, "missing_hours": [], "slot": "GOES-East"}

    monkeypatch.setattr(glm, "fetch_flashes", fake_fetch)
    t0 = 1752948000.0
    j = client.post(f"/api/cases/{case['id']}/lightning/strikes/fetch", json={"source": "glm", "t0_utc": t0,
                                                                            "t1_utc": t0 + 1200}).json()
    j = wait_job(client, j["id"])
    assert j["result"] == {"count": 1} and j["kind"] == "lightning.fetch_strikes"
    assert seen["bbox"]["lat_min"] < region["lat_min"] - 0.4                      # padded by 50 km
    assert seen["cache"].name == "glm" and seen["satellite"] == "auto"
    sset = client.get(f"/api/cases/{case['id']}/lightning").json()["strikes"]
    assert sset["source"] == "glm-G19" and sset["t0_utc"] == t0 and sset["t1_utc"] == t0 + 1200 and sset["count"] == 1


def test_video_flashes(client, make_case, tmp_path):
    video = make_video(tmp_path / "night.mov", flashes_at=((4.0, 0.1), (9.0, 0.2)), lamp_on=None, seconds=14.0)
    case = make_case(name="video", wav=video)
    assert case["recording"]["has_video"]
    base = f"/api/cases/{case['id']}/lightning"
    j = wait_job(client, client.post(f"{base}/detect-flashes", json={"sensitivity": 0.5}).json()["id"])
    fl = client.get(base).json()["flashes"]
    assert j["result"]["count"] == 2 and np.allclose([f["t"] for f in fl], [4.0, 9.0], atol=0.05)
    assert set(fl[0]) == {"id", "t", "strength", "origin", "status"} and fl[0]["origin"] == "video"
    assert fl[0]["id"].startswith("lf_") and fl[0]["status"] == "candidate" and fl[0]["strength"] > 20
    acc = client.patch(f"{base}/flashes/{fl[0]['id']}", json={"status": "accepted"}).json()
    assert acc["status"] == "accepted"
    man = client.post(f"{base}/flashes", json={"t": 11.5}).json()
    assert man["origin"] == "manual" and man["status"] == "accepted"
    assert client.patch(f"{base}/flashes/{man['id']}", json={"t": 11.6}).json()["t"] == 11.6
    wait_job(client, client.post(f"{base}/detect-flashes").json()["id"])        # keeps accepted + manual
    ids = {f["id"] for f in client.get(base).json()["flashes"]}
    assert acc["id"] in ids and man["id"] in ids and fl[1]["id"] not in ids and len(ids) == 3
    assert client.delete(f"{base}/flashes/{man['id']}").status_code == 204
    assert client.delete(f"{base}/flashes/{man['id']}").status_code == 404


def test_demo_case_end_to_end(client):
    store = client.app.state.store
    case = demo.make_demo_case(store)
    assert case["recording"]["status"] == "ready" and case["start_utc"] is None and case["region"]
    assert "True answer" in case["notes"]
    base = f"/api/cases/{case['id']}/lightning"
    assert client.get(base).json()["strikes"]["count"] == 40
    wait_job(client, client.post(f"{base}/detect-thunder").json()["id"])
    j = wait_job(client, client.post(f"{base}/solve", json={}).json()["id"])
    sol = client.get(f"{base}/solves/{j['result']['solve_id']}").json()
    b = sol["best"]
    assert sol["mode"] == "free"
    assert haversine_m(b["lat"], b["lon"], demo.TRUE_LAT, demo.TRUE_LON) < 1000
    assert abs(b["start_utc"] - demo.TRUE_START_UTC) < 2.0
