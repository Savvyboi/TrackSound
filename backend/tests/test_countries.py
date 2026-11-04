"""The "recorded in" country filter: outlines, search areas, and how each source uses them."""
from __future__ import annotations

import numpy as np
import pytest

from tests.conftest import wait_job
from tracksound import countries as C
from tracksound.sources.aircraft import flights, search, synth


def test_portugal_outline_and_margin():
    pt = C.Area(None, [C.get("PT")])
    inside = {"Lisbon": (38.7223, -9.1393), "Porto": (41.1496, -8.6109), "Faro": (37.0194, -7.9304),
              "Funchal (Madeira)": (32.65, -16.91), "Ponta Delgada (Azores)": (37.74, -25.67)}
    outside = {"Madrid": (40.4168, -3.7038), "Vigo": (42.24, -8.72), "5 km offshore": (38.66, -9.40)}
    for name, (la, lo) in inside.items():
        assert pt.contains([la], [lo])[0], name
    for name, (la, lo) in outside.items():
        assert not pt.contains([la], [lo])[0], name
    assert 85_000 < pt.area_km2() < 110_000
    lat, lon = pt.grid(2000)
    assert pt.contains(lat, lon).all() and 20_000 < len(lat) < 28_000
    assert 39 < pt.center()[0] < 40.5 and -8.5 < pt.center()[1] < -7.5


def test_region_inside_country_and_large_countries():
    lisbon = {"lat_min": 38.5, "lat_max": 39.0, "lon_min": -9.6, "lon_max": -8.8}
    a = C.Area(lisbon, [C.get("PT")])
    lat, lon = a.grid(500)
    assert len(lat) and (lat >= 38.5).all() and (lon <= -8.8).all()
    assert a.area_km2() < C.Area(lisbon, []).area_km2()       # the estuary and ocean are not searched
    us = C.Area(None, [C.get("US")])
    assert us.too_large() and not C.Area(lisbon, [C.get("US")]).too_large()
    with pytest.raises(ValueError, match="too large"):
        C.Area({"lat_min": 30, "lat_max": 45, "lon_min": -120, "lon_max": -80}, [C.get("US")]).grid(100)


def test_power_grids():
    assert C.grids_for(["PT"]) == ["Continental Europe"]
    assert C.grids_for(["EE"], "2024-06-01")[0].startswith("Baltic")
    assert C.grids_for(["EE"], "2026-01-01") == ["Continental Europe"]
    assert C.grid_matches("Continental Europe", C.grids_for(["PT", "ES"]))
    assert not C.grid_matches("Great Britain", C.grids_for(["PT"]))
    assert C.validate(["pt", "PT", "es"]) == ["PT", "ES"]
    with pytest.raises(ValueError):
        C.validate(["XX"])


def test_countries_api(client):
    lst = client.get("/api/countries").json()
    assert len(lst) > 240
    pt = next(c for c in lst if c["code"] == "PT")
    assert pt["mains_hz"] == 50 and pt["grids"] == ["Continental Europe"] and pt["n_parts"] > 3
    f = client.get("/api/countries/pt").json()
    assert f["type"] == "Feature" and f["geometry"]["type"] == "MultiPolygon" and f["properties"]["code"] == "PT"
    assert client.get("/api/countries/XX").status_code == 404

    c = client.post("/api/cases", json={"name": "pt"}).json()
    assert c["countries"] == [] and client.get(f"/api/cases/{c['id']}/area").json()["description"] == "not set"
    assert client.patch(f"/api/cases/{c['id']}", json={"countries": ["XX"]}).status_code == 422
    c = client.patch(f"/api/cases/{c['id']}", json={"countries": ["pt"]}).json()
    assert c["countries"] == ["PT"]
    area = client.get(f"/api/cases/{c['id']}/area").json()
    assert area["description"] == "Portugal" and 85_000 < area["area_km2"] < 110_000 and not area["too_large"]
    assert area["grids"] == ["Continental Europe"] and area["mains_hz"] == [50]

    us = client.post("/api/cases", json={"name": "us"}).json()
    client.patch(f"/api/cases/{us['id']}", json={"countries": ["US"], "date": "2026-09-08"})
    assert client.get(f"/api/cases/{us['id']}/area").json()["too_large"]
    r = client.post(f"/api/cases/{us['id']}/aircraft/flights/fetch", json={})
    assert r.status_code == 400 and "too large" in r.json()["detail"]
    assert client.get(f"/api/cases/{us['id']}/alerts").json()["notes"] == []
    pt_alerts = client.get(f"/api/cases/{c['id']}/alerts").json()["notes"]
    assert pt_alerts and "SAME" in pt_alerts[0]


def test_enf_references_flagged_by_country(client):
    refs = client.get("/api/enf/references", params={"country": "PT"}).json()
    assert refs and all("in_area" in r for r in refs)
    flagged = [r for r in refs if r["in_area"]]
    assert flagged and all("Continental Europe" in r["grid"] for r in flagged)
    assert refs[0]["in_area"]                                   # matching references first
    assert not any(r["in_area"] for r in refs if r["grid"] == "Great Britain")
    assert all("in_area" not in r for r in client.get("/api/enf/references").json())


def test_aircraft_search_area_and_estimate(client, make_case):
    """Searches stay inside the country; a whole-country estimate suggests a coarser grid."""
    lat0, lon0, start = 38.75, -9.15, 1_788_890_400.0
    traces, _ = synth.make_world(lat0, lon0, start, 1200.0, synth.DEMO_PASSES[:2], n_background=6, seed=3)
    case = make_case(seconds=60)
    cid = case["id"]
    client.patch(f"/api/cases/{cid}", json={"countries": ["PT"], "date": "2026-09-08"})
    store = client.app.state.store
    flights.attach_traces(store, cid, "2026-09-08", C.Area(None, [C.get("PT")]).bbox, traces)
    for e in ({"id": "e1", "t_cpa": 400.0}, {"id": "e2", "t_cpa": 900.0}):
        store.write_json(cid, "aircraft/events.json", (store.read_json(cid, "aircraft/events.json", []) +
                         [{**e, "t_cpa_emit": e["t_cpa"] - 8, "f0": 110.0, "v": 70.0, "d": 3000.0, "t_start": e["t_cpa"] - 60,
                           "t_end": e["t_cpa"] + 60, "score": 4.0, "f_pre": 130.0, "f_post": 90.0, "harmonics": 1,
                           "status": "accepted", "origin": "manual", "quality": "strong", "band": [60, 300]}]))
    est = client.post(f"/api/cases/{cid}/aircraft/search/estimate",
                      json={"mode": "blind", "events": ["e1", "e2"], "spacing_m": 200}).json()
    assert 85_000 < est["area_km2"] < 110_000 and est["n_points"] > 2_000_000
    # 200 m over all of Portugal is over the point limit; the suggestion is the finest spacing that
    # fits (with only a handful of synthetic flights that is 250 m; real traffic pushes it up)
    sug = est["suggested_spacing_m"]
    assert est["too_many_points"] and sug and sug > 200 and est["n_points"] * (200 / sug) ** 2 <= search.MAX_SEARCH_POINTS

    # the grid itself: only points in Portugal, Lisbon region narrowed
    inp = search.Inputs(None, traces, 340.0)
    lisbon = C.Area({"lat_min": 38.6, "lat_max": 38.9, "lon_min": -9.5, "lon_max": -9.0}, [C.get("PT")])
    g = search.make_grid(inp, lisbon, 500.0)
    assert len(g.lat) > 100 and lisbon.contains(g.lat, g.lon).all()
    with pytest.raises(ValueError, match="too large|grid points"):
        search.make_grid(inp, C.Area(None, [C.get("PT")]), 50.0)


def test_lightning_solve_stays_in_country(client):
    """A Florida storm, searched as 'inside the United States' with a region: the note says so."""
    from tracksound.sources.lightning.demo import make_demo_case
    case = make_demo_case(client.app.state.store)
    cid = case["id"]
    client.patch(f"/api/cases/{cid}", json={"countries": ["US"]})
    wait_job(client, client.post(f"/api/cases/{cid}/lightning/detect-thunder", json={}).json()["id"], timeout=300)
    sid = wait_job(client, client.post(f"/api/cases/{cid}/lightning/solve", json={}).json()["id"], timeout=600)["result"]["solve_id"]
    sol = client.get(f"/api/cases/{cid}/lightning/solves/{sid}").json()
    assert sol["best"] is not None
    assert C.Area(None, [C.get("US")]).contains([sol["best"]["lat"]], [sol["best"]["lon"]])[0]
    assert any("United States" in n for n in sol["notes"])
