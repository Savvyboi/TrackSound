"""Strike catalogue CSV import."""
from __future__ import annotations

import numpy as np
import pytest

from tracksound.sources.lightning import catalogue


def test_basic_csv_with_iso_and_unix_times():
    csv = ("time,lat,lon,sigma_m,energy,kind\n"
           "2025-07-19T18:20:05.250Z,28.01,-81.95,300,-12.5,CG\n"
           "1752949210.5,28.02,-81.96,,,ic\n"
           "2025-07-19 14:20:20-04:00,28.03,-81.97,250,,cloud-to-ground\n")
    s = catalogue.parse_csv(csv)
    assert [x["id"] for x in s] == ["st_00000", "st_00001", "st_00002"]
    assert [x["t_utc"] for x in s] == [1752949205.25, 1752949210.5, 1752949220.0]
    assert [x["kind"] for x in s] == ["cg", "ic", "cg"]
    assert [x["sigma_m"] for x in s] == [300.0, catalogue.DEFAULT_CSV_SIGMA_M, 250.0]
    assert s[0]["energy"] == -12.5 and s[1]["energy"] is None
    assert all(x["source"] == "csv" for x in s)


def test_header_aliases_semicolons_and_ms():
    csv = ("Timestamp;Latitude (deg);Longitude (deg);Peak current [kA];Error_km\n"
           "1752949205250;28.5;-81.0;23;0.4\n"
           "1752949200000;28.6;279.0;-8;0.2\n")      # 279 E == -81 W
    s = catalogue.parse_csv(csv.encode())
    assert [x["t_utc"] for x in s] == [1752949200.0, 1752949205.25]      # sorted by time
    assert s[0]["lon"] == -81.0 and s[0]["sigma_m"] == 200.0 and s[0]["energy"] == -8.0


def test_separate_date_and_time_columns_and_bad_rows():
    csv = ("date,time,lat,lon,cloud\n"
           "2025-07-19,18:20:00,28.0,-82.0,0\n"
           "2025-07-19,18:20:01.5,28.0,-82.0,1\n"
           "2025-07-19,not a time,28.0,-82.0,1\n"
           "2025-07-19,18:20:03,999,-82.0,1\n")
    s = catalogue.parse_csv(csv)
    assert len(s) == 2
    assert s[0]["t_utc"] == 1752949200.0 and s[1]["t_utc"] == 1752949201.5
    assert [x["kind"] for x in s] == ["cg", "ic"]


def test_errors():
    with pytest.raises(ValueError, match="missing"):
        catalogue.parse_csv("when,where\n1,2\n")
    with pytest.raises(ValueError, match="empty"):
        catalogue.parse_csv(b"")
    with pytest.raises(ValueError, match="no usable rows"):
        catalogue.parse_csv("time,lat,lon\nx,y,z\n")


def test_arrays_and_strike_set():
    s = catalogue.parse_csv("time,lat,lon,kind\n10,1,2,cg\n5,1.5,2.5,\n")
    a = catalogue.to_arrays(s)
    assert list(a.t) == [5.0, 10.0] and a.ids == ["st_00000", "st_00001"]
    assert a.early_m[1] == catalogue.EARLY_EXTRA_M["cg"] and a.early_m[0] == catalogue.EARLY_EXTRA_DEFAULT_M
    ss = catalogue.strike_set("csv", s)
    assert ss["count"] == 2 and ss["t0_utc"] == 5.0 and ss["t1_utc"] == 10.0
    assert ss["bbox"] == {"lat_min": 1.0, "lat_max": 1.5, "lon_min": 2.0, "lon_max": 2.5}
    assert catalogue.describe_sigma(np.array([4000.0])) == "about 4 km"
    assert catalogue.describe_sigma(np.array([300.0])) == "about 300 m"
