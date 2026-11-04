"""ENF endpoints (docs/API.md, "ENF source") through the HTTP API."""
from __future__ import annotations

import io

import numpy as np
import pytest

from tests.conftest import wait_job
from tracksound.sources.enf import make_demo_case


@pytest.fixture
def demo(client):
    return make_demo_case(client.app.state.store, duration_s=900.0)


def test_demo_case_extract_and_match(client, demo):
    cid = demo["id"]
    truth = demo["_demo"]
    assert demo["recording"]["status"] == "ready" and demo["start_utc"] is None
    assert f"{truth['true_start_utc']:.0f}" in demo["notes"]
    assert client.get(f"/api/cases/{cid}/enf").json() == {"analysis": None, "matches": []}

    job = client.post(f"/api/cases/{cid}/enf/extract", json={"nominal": "auto"}).json()
    assert job["kind"] == "enf.extract"
    assert wait_job(client, job["id"])["result"] == {"nominal_hz": 50}
    a = client.get(f"/api/cases/{cid}/enf").json()["analysis"]
    assert a["nominal_hz"] == 50 and 0 < a["confidence"] <= 1 and a["quality"] > 0.9
    assert [h["n"] for h in a["harmonics"]] == [1, 2, 3, 4, 5, 6] and any(h["used"] for h in a["harmonics"])
    tr = a["trace"]
    assert tr["dt"] == 1.0 and len(tr["f"]) > 800
    assert all(v is None or 49.5 < v < 50.5 for v in tr["f"])

    body = {"reference_id": truth["reference_id"], "t0_utc": truth["reference_t0_utc"],
            "t1_utc": truth["reference_t1_utc"]}
    job = client.post(f"/api/cases/{cid}/enf/match", json=body).json()
    mid = wait_job(client, job["id"])["result"]["match_id"]
    assert mid.startswith("em_")
    m = client.get(f"/api/cases/{cid}/enf/matches/{mid}").json()
    assert abs(m["best"]["start_utc"] - truth["true_start_utc"]) <= 1.0
    assert m["best"]["z"] > 8 and m["best"]["corr"] > 0.5
    assert m["top"][0]["corr"] == m["best"]["corr"]
    ov = m["overlay"]
    assert {"t", "rec", "ref"} <= set(ov) and len(ov["t"]) == len(ov["rec"]) == len(ov["ref"])
    assert all(v is None or 49.5 < v < 50.5 for v in ov["rec"] + ov["ref"])          # Hz
    listed = client.get(f"/api/cases/{cid}/enf").json()["matches"]
    assert [x["id"] for x in listed] == [mid] and "overlay" not in listed[0]

    assert client.delete(f"/api/cases/{cid}/enf/matches/{mid}").status_code == 204
    assert client.get(f"/api/cases/{cid}/enf/matches/{mid}").status_code == 404
    assert client.delete(f"/api/cases/{cid}/enf/matches/{mid}").status_code == 404


def test_match_errors(client, demo):
    cid = demo["id"]
    ref = demo["_demo"]["reference_id"]
    body = {"reference_id": ref, "t0_utc": demo["_demo"]["reference_t0_utc"], "t1_utc": demo["_demo"]["reference_t1_utc"]}
    assert client.post(f"/api/cases/{cid}/enf/match", json=body).status_code == 400          # not extracted yet
    wait_job(client, client.post(f"/api/cases/{cid}/enf/extract", json={"nominal": 50}).json()["id"])
    r = client.post(f"/api/cases/{cid}/enf/match", json={**body, "reference_id": "no-such-ref"})
    assert r.status_code == 404
    r = client.post(f"/api/cases/{cid}/enf/match", json={**body, "reference_id": "pgf-us-west-2019-05"})
    assert r.status_code == 400 and "60 Hz" in r.json()["detail"]
    r = client.post(f"/api/cases/{cid}/enf/match", json={**body, "t1_utc": body["t0_utc"] + 40 * 86400})
    assert r.status_code == 400
    assert client.post(f"/api/cases/{cid}/enf/extract", json={"nominal": 55}).status_code == 422
    assert client.get(f"/api/cases/{cid}/enf/matches/em_nothere").status_code == 404
    assert client.get("/api/cases/c_missing/enf").status_code == 404


def test_extract_needs_a_recording(client):
    c = client.post("/api/cases", json={"name": "empty"}).json()
    assert client.post(f"/api/cases/{c['id']}/enf/extract", json={}).status_code == 400


def test_references_and_import(client):
    refs = client.get("/api/enf/references").json()
    ids = {r["id"] for r in refs}
    assert {"gb-neso", "gb-elexon", "nordic-fingrid", "au-nem-aemo"} <= ids
    for r in refs:
        assert {"id", "name", "grid", "nominal_hz", "source", "resolution_s", "coverage", "licence", "url"} <= set(r)
        assert r["source"] == "builtin" and r["nominal_hz"] in (50, 60)
    assert next(r for r in refs if r["id"] == "gb-neso")["coverage"] is None

    t0 = 1_790_000_000
    f = 50 + 0.05 * np.sin(np.arange(1200) / 100)
    csv = "time,frequency\n" + "".join(f"{t0 + i},{v:.4f}\n" for i, v in enumerate(f))
    r = client.post("/api/enf/references/import", files={"file": ("ref.csv", io.BytesIO(csv.encode()), "text/csv")},
                    data={"name": "My meter", "grid": "Continental Europe", "nominal_hz": "50"})
    assert r.status_code == 200, r.text
    ref = r.json()
    assert ref["source"] == "import" and ref["grid"] == "Continental Europe" and ref["resolution_s"] == 1.0
    assert ref["coverage"] == [{"t0_utc": float(t0), "t1_utc": float(t0 + 1200)}]
    assert ref["id"] in {x["id"] for x in client.get("/api/enf/references").json()}
    bad = client.post("/api/enf/references/import", files={"file": ("x.csv", io.BytesIO(b"a,b\n1,2\n"), "text/csv")},
                      data={"name": "x", "grid": "y", "nominal_hz": "50"})
    assert bad.status_code == 400
    assert client.delete(f"/api/enf/references/{ref['id']}").status_code == 204
    assert client.delete("/api/enf/references/gb-neso").status_code == 400


def test_grid_map(client):
    g = client.get("/api/enf/grid-map").json()
    assert g["type"] == "FeatureCollection" and len(g["features"]) > 150
    by = {f["properties"]["iso_a3"]: f["properties"] for f in g["features"]}
    assert by["GBR"]["mains_hz"] == 50 and by["USA"]["mains_hz"] == 60 and by["JPN"]["mains_hz"] == "50/60"
    assert by["FRA"]["mains_hz"] == 50 and by["NOR"]["mains_hz"] == 50 and by["BRA"]["mains_hz"] == 60
    assert all(set(f["properties"]) == {"name", "iso_a3", "mains_hz"} for f in g["features"])
