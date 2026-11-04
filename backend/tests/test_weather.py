"""Weather lookup with the Open-Meteo response mocked (no network)."""
import requests

from tracksound.api import weather


class FakeResp:
    def __init__(self, body, ok=True):
        self._b, self.ok, self.status_code = body, ok, 200 if ok else 429
        self.headers = {"content-type": "application/json"}

    def json(self):
        return self._b


def test_weather_suggests_temperature(client, monkeypatch):
    calls = []

    def fake_get(url, params, timeout):
        calls.append(url)
        if url == weather.ARCHIVE:          # archive has no data yet for recent dates
            return FakeResp({"hourly": {"time": ["2026-09-08T00:00"], "temperature_2m": [None]}})
        times = [f"2026-09-08T{h:02d}:00" for h in range(24)]
        return FakeResp({"hourly": {"time": times, "temperature_2m": [10.0 + h for h in range(24)]}})

    monkeypatch.setattr(requests, "get", fake_get)
    c = client.post("/api/cases", json={"name": "w"}).json()
    assert client.get(f"/api/cases/{c['id']}/weather").status_code == 400
    client.patch(f"/api/cases/{c['id']}", json={"date": "2026-09-08",
                                                "region": {"lat_min": 40, "lat_max": 41, "lon_min": -74, "lon_max": -73}})
    w = client.get(f"/api/cases/{c['id']}/weather").json()
    assert w["source"] == "open-meteo-forecast" and len(w["hourly"]) == 24 and w["suggested_c"] == 21.5
    client.patch(f"/api/cases/{c['id']}", json={"start_utc": 1788897600.0})       # 2026-09-08 20:00 UTC
    w = client.get(f"/api/cases/{c['id']}/weather").json()
    assert w["suggested_c"] == 30.0 and calls[0] == weather.ARCHIVE
