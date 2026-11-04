import numpy as np

from tests.conftest import wait_job


def test_case_crud(client):
    assert client.get("/api/health").json()["ok"]
    c = client.post("/api/cases", json={"name": "Backyard"}).json()
    assert c["name"] == "Backyard" and c["recording"] is None
    assert [x["id"] for x in client.get("/api/cases").json()] == [c["id"]]

    region = {"lat_min": 40.4, "lat_max": 41.3, "lon_min": -74.4, "lon_max": -72.6}
    c2 = client.patch(f"/api/cases/{c['id']}", json={"date": "2026-09-08", "region": region, "timezone": "America/New_York",
                                                     "start_utc": 1788898650.0}).json()
    assert c2["region"] == region and c2["date"] == "2026-09-08" and c2["start_utc_source"] == "user"
    assert client.patch(f"/api/cases/{c['id']}", json={"date": "08/09/2026"}).status_code == 422
    assert client.patch(f"/api/cases/{c['id']}", json={"region": {**region, "lat_min": 42}}).status_code == 422
    assert client.patch(f"/api/cases/{c['id']}", json={"timezone": "Mars/Olympus"}).status_code == 422
    c3 = client.patch(f"/api/cases/{c['id']}", json={"start_utc": None}).json()
    assert c3["start_utc"] is None and c3["start_utc_source"] is None

    assert client.delete(f"/api/cases/{c['id']}").status_code == 204
    assert client.get(f"/api/cases/{c['id']}").status_code == 404
    store = client.app.state.store
    import pytest
    from tracksound.store import NotFound
    for bad in ("../etc", "..", ".hidden", "a/b", ""):
        with pytest.raises(NotFound):
            store.case_dir(bad)
    with pytest.raises(ValueError):
        store.path(c["id"], "../../outside.json")
    assert client.get("/api/cases/c_doesnotexist").status_code == 404
    assert client.get("/api/no-such-endpoint").status_code == 404


def test_upload_builds_spectrogram_and_tiles(make_case, client):
    case = make_case(seconds=300)
    rec = case["recording"]
    assert rec["status"] == "ready" and abs(rec["duration_s"] - 300) < 0.5
    sp = rec["spectrogram"]
    assert sp["n_bins"] > 800 and sp["levels"] >= 1 and abs(sp["dt"] - 0.25) < 1e-9
    r = client.get(f"/api/cases/{case['id']}/spectrogram/tile", params={"level": 0, "index": 0})
    assert r.status_code == 200
    rows, cols = int(r.headers["X-Rows"]), int(r.headers["X-Cols"])
    assert rows == sp["n_bins"] and cols == min(sp["n_frames"], sp["tile_frames"])
    tile = np.frombuffer(r.content, np.uint8).reshape(rows, cols)
    # the 120 Hz tone is the brightest row
    f = sp["f0"] + np.arange(rows) * sp["df"]
    assert abs(f[np.argmax(tile.mean(axis=1))] - 117) < 6
    assert client.get(f"/api/cases/{case['id']}/spectrogram/tile", params={"level": 0, "index": 99}).status_code == 404

    w = client.get(f"/api/cases/{case['id']}/audio", params={"t0": 10, "t1": 20, "speed": 4})
    assert w.status_code == 200 and w.content[:4] == b"RIFF"
    assert client.get(f"/api/cases/{case['id']}/audio", params={"t0": 0, "t1": 1000}).status_code == 400


def test_upload_rejects_garbage(client, tmp_path):
    c = client.post("/api/cases", json={"name": "bad"}).json()
    p = tmp_path / "junk.mp3"
    p.write_bytes(b"not audio at all" * 100)
    with open(p, "rb") as fh:
        r = client.post(f"/api/cases/{c['id']}/recording", files={"file": ("junk.mp3", fh, "audio/mpeg")})
    jid = r.json()["job"]["id"]
    import time
    for _ in range(100):
        j = client.get(f"/api/jobs/{jid}").json()
        if j["status"] in ("done", "error"):
            break
        time.sleep(0.1)
    assert j["status"] == "error"
    assert client.get(f"/api/cases/{c['id']}").json()["recording"]["status"] == "error"


def test_jobs_cancel(client):
    import threading
    app = client.app
    gate = threading.Event()

    def slow(ctx):
        for i in range(200):
            ctx.progress(i / 200, "working")
            gate.wait(0.02)
        return {"ok": True}

    j = app.state.jobs.submit("test.slow", None, slow)
    client.post(f"/api/jobs/{j['id']}/cancel")
    import time
    for _ in range(200):
        s = client.get(f"/api/jobs/{j['id']}").json()["status"]
        if s in ("cancelled", "done"):
            break
        time.sleep(0.05)
    assert s == "cancelled"


def test_report(client):
    c = client.post("/api/cases", json={"name": "report"}).json()
    r = client.get(f"/api/cases/{c['id']}/report")
    assert r.status_code == 200
    rep = r.json()
    assert rep["case"]["id"] == c["id"] and isinstance(rep["estimates"], list)
    assert rep["aircraft"]["events"] == [] and rep["aircraft"]["flights"]["status"] == "none"
    assert client.get("/api/cases/c_nope/report").status_code == 404


def test_reupload_clears_recording_results(make_case, client, tmp_path):
    from tests.conftest import write_tone_wav
    case = make_case(seconds=60)
    cid, store = case["id"], client.app.state.store
    for rel in ("aircraft/events.json", "aircraft/flights.json", "lightning/onsets.json", "lightning/strikes.json",
                "enf/analysis.json", "alerts/scan.json"):
        store.write_json(cid, rel, [])
    store.write_json(cid, "aircraft/searches/as_x.json", {})
    client.patch(f"/api/cases/{cid}", json={"start_utc": 1.7e9, "start_utc_source": "aircraft"})
    wav = write_tone_wav(tmp_path / "second.wav", seconds=60, seed=3)
    with open(wav, "rb") as fh:
        r = client.post(f"/api/cases/{cid}/recording", files={"file": ("second.wav", fh, "audio/wav")})
    wait_job(client, r.json()["job"]["id"])
    d = store.case_dir(cid)
    for gone in ("aircraft/events.json", "aircraft/searches", "lightning/onsets.json", "enf/analysis.json", "alerts/scan.json"):
        assert not (d / gone).exists(), gone
    for kept in ("aircraft/flights.json", "lightning/strikes.json"):
        assert (d / kept).exists(), kept
    c = client.get(f"/api/cases/{cid}").json()
    assert c["start_utc"] is None and c["recording"]["filename"] == "second.wav" and c["recording"]["status"] == "ready"
