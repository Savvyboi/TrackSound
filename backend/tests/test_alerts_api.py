"""Alerts API: upload a recording with an alert at a known offset, scan, read the headers."""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest
import soundfile as sf

from tests.conftest import wait_job
from tracksound.sources.alerts import codes, synth

SR = 44100
HDR = "ZCZC-WXR-TOR-029095-029037+0030-1281917-KEAX/NWS-"
AT = 20.0


@pytest.fixture
def alert_case(make_case, tmp_path):
    x, _ = synth.alert_waveform(HDR, sr=SR, attention="eas", seed=2)
    bed = (np.random.default_rng(0).standard_normal(60 * SR) * 0.02).astype(np.float32)
    wav = tmp_path / "radio.wav"
    sf.write(wav, synth.mix(x * 0.2, bed, AT, SR), SR)
    return make_case("alerts", wav=wav)


def test_scan_gives_header_county_and_implied_start(client, alert_case):
    cid = alert_case["id"]
    assert client.get(f"/api/cases/{cid}/alerts").json() == {"headers": [], "eom": [], "scanned_at": None, "notes": []}
    client.patch(f"/api/cases/{cid}", json={"date": "2026-05-08"})
    r = client.post(f"/api/cases/{cid}/alerts/scan", json={"crosscheck": False})
    assert r.status_code == 200 and r.json()["kind"] == "alerts.scan"
    assert wait_job(client, r.json()["id"])["result"] == {"count": 1}

    st = client.get(f"/api/cases/{cid}/alerts").json()
    assert st["scanned_at"] is not None and len(st["eom"]) == 1
    (h,) = st["headers"]
    assert h["id"].startswith("sh_") and h["raw"] == HDR and h["repeats"] == 3 and h["confidence"] > 0.9
    assert (h["originator_name"], h["event_name"], h["station"]) == ("National Weather Service", "Tornado Warning", "KEAX/NWS")
    assert h["t"] == pytest.approx(AT, abs=0.01) and h["purge_minutes"] == 30
    issued = datetime(2026, 5, 8, 19, 17, tzinfo=timezone.utc).timestamp()       # day 128 of 2026, 19:17 UTC
    assert h["issued_utc"] == issued
    assert h["implied_start_utc"] == pytest.approx(issued - h["t"], abs=1e-3)
    jackson = codes.counties()["29095"]
    loc = h["locations"][0]
    assert (loc["code"], loc["state"], loc["county"]) == ("029095", "MO", "Jackson County")
    assert (loc["lat"], loc["lon"]) == (jackson["lat"], jackson["lon"])
    assert any("case date" in n for n in st["notes"]) and any("853 + 960 Hz" in n for n in st["notes"])

    # the year follows the case date without a new scan
    client.patch(f"/api/cases/{cid}", json={"date": "2025-05-08"})
    h2 = client.get(f"/api/cases/{cid}/alerts").json()["headers"][0]
    assert h2["issued_utc"] == datetime(2025, 5, 8, 19, 17, tzinfo=timezone.utc).timestamp()


def test_scan_needs_a_recording(client):
    cid = client.post("/api/cases", json={"name": "empty"}).json()["id"]
    assert client.post(f"/api/cases/{cid}/alerts/scan", json={}).status_code == 400
    assert client.get("/api/cases/c_missing/alerts").status_code == 404
