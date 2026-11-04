"""USGS ComCat events (blasts, explosions, sonic booms, earthquakes) as known-time impulsive events."""
from __future__ import annotations

import calendar

import pytest

from tracksound.sources.lightning import catalogue, usgs

# rows copied from real ComCat CSV responses (2024-06 southern California, and sonic booms)
SAMPLE = """time,latitude,longitude,depth,mag,magType,nst,gap,dmin,rms,net,id,updated,place,type,horizontalError,depthError,magError,magNst,status,locationSource,magSource
2024-06-05T21:37:07.740Z,34.9876667,-118.1948333,-0.91,1.19,ml,26,36,0.1121,0.22,ci,ci40615527,2024-06-06T15:31:19.671Z,"8 km SSW of Mojave, CA",quarry blast,0.38,31.61,0.054,10,reviewed,ci,ci
2024-06-06T16:21:13.120Z,34.5761667,-117.2516667,-1.39,1.34,ml,16,65,0.06615,0.17,ci,ci40616063,2024-06-06T17:19:46.905Z,"6 km NE of Victorville, CA",quarry_blast,0.41,31.61,0.223,17,reviewed,ci,ci
2024-06-06T18:23:45.000Z,34.3,-117.3,0,0,mb_lg,,,,0,us,us20004vbk,2026-06-03T15:01:42.217Z,"somewhere, CA",sonicboom,0,0,,,reviewed,spec,spec
2024-06-06T20:05:56.590Z,33.8785,-117.5001667,13.3,0.98,ml,22,54,0.07327,0.23,ci,ci40616207,2024-06-06T22:31:03.721Z,"2 km E of Home Gardens, CA",earthquake,,0.81,0.127,15,reviewed,ci,ci
"""
HEADER = SAMPLE.splitlines()[0] + "\n"
BBOX = {"lat_min": 33.0, "lat_max": 35.5, "lon_min": -118.5, "lon_max": -116.0}


def utc(*a) -> float:
    return float(calendar.timegm(a + (0,) * (6 - len(a))))


def test_type_names_expand_to_comcat_spellings():
    default = usgs.expand_types(None)
    assert {"quarry blast", "quarry_blast", "quarry", "mining explosion", "explosion", "chemical explosion",
            "sonic boom", "sonicboom"} <= set(default) and "earthquake" not in default
    assert usgs.expand_types(["Earthquake"]) == ["earthquake", "eq"]
    assert usgs.expand_types(["quarry_blast", "rock burst"]) == ["quarry blast", "quarry_blast", "quarry",
                                                                  "rock burst", "rock_burst"]
    with pytest.raises(ValueError, match="unknown USGS event type"):
        usgs.expand_types(["fireworks"])


def test_parse_events():
    ev = usgs.parse_events(SAMPLE)
    assert [e["kind"] for e in ev] == ["quarry blast", "quarry blast", "sonic boom", "earthquake"]
    assert [e["sigma_m"] for e in ev] == [380.0, 410.0, usgs.DEFAULT_SIGMA_M, usgs.DEFAULT_SIGMA_M]
    assert ev[0]["t_utc"] == pytest.approx(utc(2024, 6, 5, 21, 37, 7) + 0.74)
    assert ev[0]["ref"] == "ci40615527" and ev[0]["energy"] == 1.19 and ev[0]["source"] == "usgs"
    assert ev[3]["depth_m"] == 13300.0
    a = catalogue.to_arrays(catalogue.assign_ids(ev))
    assert list(a.seismic) == [False, False, False, True]           # only the earthquake travels as a P wave
    assert a.sigma_m[2] >= 3000.0                                      # a boom is no point source
    assert usgs.parse_events(HEADER) == []


class FakeHttp:
    def __init__(self, text: str, status: int = 200):
        self.text, self.status, self.calls = text, status, []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        rows = self.text.splitlines()[1:]
        start = int(params["offset"]) - 1
        page = rows[start:start + int(params["limit"])]

        class R:
            pass
        r = R()
        r.status_code = self.status
        r.text = HEADER + "".join(x + "\n" for x in page) if self.status == 200 else "Bad Request: nope"
        return r


def test_fetch_events_pages_and_filters(monkeypatch):
    monkeypatch.setattr(usgs, "PAGE", 2)
    http = FakeHttp(SAMPLE)
    strikes, info = usgs.fetch_events(BBOX, utc(2024, 6, 5), utc(2024, 6, 6, 19), ["quarry blast", "sonic boom"],
                                      http=http)
    # the earthquake is outside the time window
    assert [s["kind"] for s in strikes] == ["quarry blast", "quarry blast", "sonic boom"]
    assert [s["id"] for s in strikes] == ["st_00000", "st_00001", "st_00002"]
    assert info["kinds"] == {"quarry blast": 2, "sonic boom": 1} and not info["truncated"]
    assert [p["offset"] for _, p in http.calls] == ["1", "3", "5"]
    p = http.calls[0][1]
    assert p["format"] == "csv" and p["orderby"] == "time-asc" and p["starttime"] == "2024-06-05T00:00:00.000"
    assert "quarry_blast" in p["eventtype"].split(",") and p["minlongitude"] == "-118.50000"
    with pytest.raises(ValueError, match="refused"):
        usgs.fetch_events(BBOX, utc(2024, 6, 5), utc(2024, 6, 6), http=FakeHttp(SAMPLE, status=400))


@pytest.mark.slow
def test_real_comcat_quarry_blasts():
    """Live query: quarry blasts in southern California in the first week of June 2024."""
    strikes, info = usgs.fetch_events(BBOX, utc(2024, 6, 1), utc(2024, 6, 8), ["quarry blast"])
    assert len(strikes) >= 3
    assert {s["kind"] for s in strikes} == {"quarry blast"} and {s["source"] for s in strikes} == {"usgs"}
    assert all(100 <= s["sigma_m"] <= 5000 for s in strikes)
    assert all(utc(2024, 6, 1) <= s["t_utc"] <= utc(2024, 6, 8) for s in strikes)
    assert all(s["ref"].startswith("ci") for s in strikes)
