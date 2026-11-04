"""End to end through the HTTP API on a synthetic world: detect flyovers, search, refine."""
from __future__ import annotations

import numpy as np
import pytest
import soundfile as sf

from tests.conftest import wait_job
from tracksound.physics import haversine_m, speed_of_sound
from tracksound.sources.aircraft import flights, synth

LAT0, LON0 = 40.8765, -73.5250
START = 1_788_890_400.0          # 2026-09-08 18:00:00 UTC
DUR = 1500.0
PASSES = [
    synth.PlannedPass(t_cpa=330.0, lateral_m=2600.0, alt_m=900.0, v_mps=62.0, heading_deg=70.0, turn_dps=0.02, f0=118.0),
    synth.PlannedPass(t_cpa=1050.0, lateral_m=-3200.0, alt_m=1200.0, v_mps=85.0, heading_deg=200.0, turn_dps=-0.015, f0=96.0),
]
REGION = {"lat_min": LAT0 - 0.07, "lat_max": LAT0 + 0.1, "lon_min": LON0 - 0.12, "lon_max": LON0 + 0.09}


@pytest.fixture(scope="module")
def world():
    traces, dense = synth.make_world(LAT0, LON0, START, DUR, PASSES, n_background=12, region_half_m=20_000, seed=11)
    x = synth.synth_audio(dense, START, DUR, sr=4000, c=speed_of_sound(15))
    return traces, x


@pytest.fixture
def case(client, tmp_path, world):
    traces, x = world
    wav = tmp_path / "flyovers.wav"
    sf.write(str(wav), x, 4000)
    c = client.post("/api/cases", json={"name": "synthetic"}).json()
    with open(wav, "rb") as fh:
        r = client.post(f"/api/cases/{c['id']}/recording", files={"file": ("flyovers.wav", fh, "audio/wav")})
    wait_job(client, r.json()["job"]["id"])
    client.patch(f"/api/cases/{c['id']}", json={"date": "2026-09-08", "region": REGION})
    flights.attach_traces(client.app.state.store, c["id"], "2026-09-08", REGION, traces)
    return c


def _heard(p: synth.PlannedPass) -> float:
    return p.t_cpa + float(np.hypot(p.lateral_m, p.alt_m)) / speed_of_sound(15)


def test_flights_endpoints(client, case):
    fs = client.get(f"/api/cases/{case['id']}/aircraft/flights").json()
    assert fs["status"] == "ready" and fs["n_flights"] == 14
    assert {f["type"] for f in fs["flights"]} <= {t for t, _ in synth.PROP_TYPES}
    gj = client.get(f"/api/cases/{case['id']}/aircraft/flights/geojson").json()
    assert gj["features"] and gj["features"][0]["geometry"]["type"] == "LineString"
    assert len(gj["features"][0]["properties"]["times"]) == len(gj["features"][0]["geometry"]["coordinates"])
    tr = client.get(f"/api/cases/{case['id']}/aircraft/flights/0").json()
    assert tr["icao"] == "a00000" and len(tr["t"]) == len(tr["lat"])
    assert client.get(f"/api/cases/{case['id']}/aircraft/flights/99").status_code == 404


def test_detect_search_refine(client, case):
    cid = case["id"]
    j = client.post(f"/api/cases/{cid}/aircraft/detect", json={"band": [80, 150], "top": 4}).json()
    wait_job(client, j["id"], timeout=300)
    evs = client.get(f"/api/cases/{cid}/aircraft/events").json()
    assert len(evs) >= 2
    picked = []
    for p in PASSES:                                  # the detection nearest each planned pass
        e = min(evs, key=lambda e: abs(e["t_cpa"] - _heard(p)))
        assert abs(e["t_cpa"] - _heard(p)) < 20, (e["t_cpa"], _heard(p))
        picked.append(e)
    for e in picked:
        assert client.patch(f"/api/cases/{cid}/aircraft/events/{e['id']}", json={"status": "accepted"}).json()["status"] == "accepted"
    ids = [e["id"] for e in picked]

    est = client.post(f"/api/cases/{cid}/aircraft/search/estimate", json={"mode": "blind", "events": ids, "spacing_m": 250}).json()
    assert est["n_points"] > 1000 and est["n_segments"] >= 2

    j = client.post(f"/api/cases/{cid}/aircraft/search",
                    json={"mode": "blind", "events": ids, "spacing_m": 250, "use_dem": False}).json()
    res = wait_job(client, j["id"], timeout=300)["result"]
    s = client.get(f"/api/cases/{cid}/aircraft/searches/{res['search_id']}").json()
    best = s["best"]
    assert best is not None and s["status"] == "done"
    err = haversine_m(best["lat"], best["lon"], LAT0, LON0)
    assert err < 700, f"{err:.0f} m from truth"
    assert [f["icao"] for f in best["flights"]] == ["a00000", "a00001"]
    assert abs(best["start_utc"] - START) < 15
    assert s["heat"] and s["stats"]["n_candidates"] > 0

    lst = client.get(f"/api/cases/{cid}/aircraft/searches").json()
    assert lst[0]["id"] == s["id"] and "heat" not in lst[0]

    cur = client.get(f"/api/cases/{cid}/aircraft/searches/{s['id']}/candidates/1/curves").json()
    assert len(cur["events"]) == 2
    for ce, p in zip(cur["events"], PASSES):
        t, f = np.array(ce["t"]), np.array(ce["f"])
        k = np.argmin(np.abs(t - _heard(p)))
        assert abs(f[k] - p.f0) < 4.0, (f[k], p.f0)   # at the heard CPA the tone is near f0

    j = client.post(f"/api/cases/{cid}/aircraft/searches/{s['id']}/refine",
                    json={"rank": 1, "spacing_m": 100, "half_m": 500, "use_dem": False}).json()
    rid = wait_job(client, j["id"], timeout=300)["result"]["search_id"]
    r = client.get(f"/api/cases/{cid}/aircraft/searches/{rid}").json()
    assert r["mode"] == "refine" and r["parent_id"] == s["id"]
    assert haversine_m(r["best"]["lat"], r["best"]["lon"], LAT0, LON0) < 500
    assert client.get(f"/api/cases/{cid}/aircraft/searches/{rid}/candidates/1/curves").status_code == 200

    # one known point: the true pair must rank first there
    j = client.post(f"/api/cases/{cid}/aircraft/search",
                    json={"mode": "point", "events": ids, "point": {"lat": LAT0, "lon": LON0}, "use_dem": False}).json()
    pid = wait_job(client, j["id"], timeout=120)["result"]["search_id"]
    pt = client.get(f"/api/cases/{cid}/aircraft/searches/{pid}").json()
    assert [f["icao"] for f in pt["best"]["flights"]] == ["a00000", "a00001"]

    # one event with a known clock
    j = client.post(f"/api/cases/{cid}/aircraft/search",
                    json={"mode": "single", "events": [ids[1]], "event_utc": START + _heard(PASSES[1]),
                          "spacing_m": 250, "use_dem": False}).json()
    sid1 = wait_job(client, j["id"], timeout=300)["result"]["search_id"]
    one = client.get(f"/api/cases/{cid}/aircraft/searches/{sid1}").json()
    assert one["best"]["flights"][0]["icao"] == "a00001"

    assert client.delete(f"/api/cases/{cid}/aircraft/searches/{sid1}").status_code == 204
    assert client.get(f"/api/cases/{cid}/aircraft/searches/{sid1}").status_code == 404


def test_validation(client, case):
    cid = case["id"]
    assert client.post(f"/api/cases/{cid}/aircraft/search", json={"mode": "blind", "events": ["x"]}).status_code == 422
    assert client.post(f"/api/cases/{cid}/aircraft/search", json={"mode": "blind", "events": ["x", "y"]}).status_code == 400
    assert client.post(f"/api/cases/{cid}/aircraft/fit", json={"t_start": 10, "t_end": 15, "f_lo": 80, "f_hi": 200}).status_code == 422
    nf = client.post("/api/cases", json={"name": "empty"}).json()
    assert client.post(f"/api/cases/{nf['id']}/aircraft/flights/fetch", json={}).status_code == 400
    assert client.post(f"/api/cases/{nf['id']}/aircraft/detect", json={}).status_code == 409


def test_manual_fit(client, case):
    cid = case["id"]
    p = PASSES[0]
    h = _heard(p)
    j = client.post(f"/api/cases/{cid}/aircraft/fit",
                    json={"t_start": h - 90, "t_end": h + 90, "f_lo": 85, "f_hi": 160}).json()
    eid = wait_job(client, j["id"], timeout=120)["result"]["event_id"]
    ev = next(e for e in client.get(f"/api/cases/{cid}/aircraft/events").json() if e["id"] == eid)
    assert ev["origin"] == "manual" and ev["status"] == "accepted"
    assert abs(ev["t_cpa"] - h) < 10 and abs(ev["f0"] - p.f0) < 5
