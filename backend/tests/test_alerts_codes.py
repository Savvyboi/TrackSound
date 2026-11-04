"""SAME code tables, header parsing, soft decoding details, issue-year inference, IPAWS matching."""
from __future__ import annotations

import subprocess
from datetime import datetime, timezone

import numpy as np
import pytest

from tracksound.sources.alerts import codes, header, ipaws, issue_time, same

# NWS Instruction 10-1712 A.3.1 and A.3.4
TOR = "ZCZC-WXR-TOR-039173-039051-139069+0030-1591829-KCLE/NWS-"
RWT = "ZCZC-WXR-RWT-020103-020209-020091-020121-029047-029165-029095-029037+0030-3031700-KEAX/NWS-"


def utc(*a) -> float:
    return datetime(*a, tzinfo=timezone.utc).timestamp()


def soft_of(text: str) -> np.ndarray:
    return (2.0 * same.bits_of(text.encode()) - 1.0).astype(np.float32)


def test_event_and_originator_tables():
    assert len(codes.EVENTS) == 57 + 5          # 47 CFR 11.31(e) plus the NWS-only codes
    assert codes.event_name("TOR") == "Tornado Warning" and codes.event_name("MEP") == "Missing and Endangered Persons"
    assert codes.event_name("XYZ").startswith("Unknown")
    assert codes.originator_name("WXR") == "National Weather Service"


def test_locations():
    jackson = codes.describe_location("029095")
    assert (jackson["state"], jackson["county"], jackson["part"]) == ("MO", "Jackson County", "all")
    assert jackson["lat"] == pytest.approx(39.006, abs=0.01) and jackson["lon"] == pytest.approx(-94.343, abs=0.01)
    henry = codes.describe_location("139069")
    assert (henry["county"], henry["part"], henry["fips"]) == ("Henry County", "northwest", "39069")
    state = codes.describe_location("029000")
    assert state["county"] == "Entire state" and 36 < state["lat"] < 41
    assert codes.describe_location("000000")["county"] == "All of the United States"
    assert codes.describe_location("009001")["county"] == "Fairfield County"      # legacy CT county, still used by NWS
    marine = codes.describe_location("073530")
    assert marine["county"] == "Chesapeake Bay north of Pooles Island MD (ANZ530)" and marine["lat"] is not None
    assert codes.describe_location("999000")["lat"] is None
    assert codes.describe_location("048999")["county"] is None


def test_parse_directive_examples():
    f = header.parse(TOR)
    assert f["originator"] == "WXR" and f["event"] == "TOR" and f["purge_minutes"] == 30
    assert f["locations"] == ["039173", "039051", "139069"] and (f["jjj"], f["hh"], f["mm"]) == (159, 18, 29)
    assert f["station"] == "KCLE/NWS"
    assert len(header.parse(RWT)["locations"]) == 8
    for bad in (TOR.replace("WXR", "XXX"), TOR.replace("1591829", "3671829"), TOR.replace("1591829", "1592429"), TOR[:-1]):
        assert header.parse(bad) is None


def test_soft_decoding_snaps_weak_errors_only():
    soft = soft_of(TOR)
    assert header.decode_header(soft) == (TOR, [])
    i = TOR.index("039051") + 5                     # bit 0 of the final '1': 051 -> 050, not an Ohio county
    weak = soft.copy()
    weak[i * 8] = -0.1                               # one weakly wrong bit
    text, snapped = header.decode_header(weak)
    assert text == TOR and snapped == ["location 2"]
    strong = soft.copy()
    strong[i * 8] = -1.0                             # a confidently different code is kept as decoded
    assert header.decode_header(strong)[0] == TOR.replace("039051", "039050")


def test_combine_votes_and_scores():
    good = soft_of(TOR)
    bad = good.copy()
    bad[200:260] *= -1                               # a burst with a burst of errors
    c = header.combine([good, bad, good], [5.0, 5.0, 5.0])
    assert c.text == TOR and c.n_agree == 2 and c.p_correct > 0.99
    assert header.majority(["abc", "abd", "xbc"]) == "abc"


def test_issue_year_inference():
    # case date: the closest year, across the new year in both directions
    assert issue_time.resolve(128, 19, 17, "2026-05-08", None, 0)[0] == utc(2026, 5, 8, 19, 17)
    assert issue_time.resolve(365, 23, 50, "2027-01-01", None, 0) == (utc(2026, 12, 31, 23, 50), "case date")
    assert issue_time.resolve(1, 0, 10, "2026-12-31", None, 0)[0] == utc(2027, 1, 1, 0, 10)
    assert issue_time.resolve(366, 12, 0, "2028-12-30", None, 0)[0] == utc(2028, 12, 31, 12, 0)   # leap year
    # file creation time, then the most recent occurrence before the scan
    assert issue_time.resolve(150, 6, 0, None, "2024-05-29T07:00:00Z", 0) == (utc(2024, 5, 29, 6, 0), "file creation time")
    assert issue_time.resolve(360, 12, 0, None, None, utc(2027, 1, 5)) == (utc(2026, 12, 26, 12, 0), "scan time")


def test_metadata_timestamps(tmp_path):
    p = issue_time.parse_timestamp
    assert p("2024-06-08T18:21:33.000000Z") == datetime(2024, 6, 8, 18, 21, 33, tzinfo=timezone.utc)
    assert p("2024-06-08T14:21:33-0400") == datetime(2024, 6, 8, 18, 21, 33, tzinfo=timezone.utc)
    assert p("1970-01-01T00:00:00Z") is None and p("garbage") is None
    f = tmp_path / "phone.m4a"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono", "-t", "0.5", "-c:a", "aac",
                    "-metadata", "creation_time=2025-03-04T05:06:07Z", str(f)], check=True)
    assert issue_time.creation_time(f) == "2025-03-04T05:06:07Z"


def test_ipaws_matching_offline():
    alert = {"identifier": "x", "sender": "w-nws.webmaster@noaa.gov", "sent": "2026-11-02T12:27:00.000Z",
             "status": "Actual", "msgType": "Alert",
             "info": [{"eventCode": [{"name": "SAME", "value": "FFW"}],
                       "area": [{"geocode": [{"name": "SAME", "value": "012009"}]}]}]}
    m = ipaws.match([alert], "FFW", ["012009", "012061"], utc(2026, 11, 2, 12, 27))
    assert len(m) == 1 and m[0]["common_locations"] == 1 and m[0]["dt_s"] == 0
    assert ipaws.match([alert], "TOR", ["012009"], utc(2026, 11, 2, 12, 27)) == []
    assert ipaws.match([alert], "FFW", ["029095"], utc(2026, 11, 2, 12, 27)) == []


@pytest.mark.slow
def test_ipaws_archive_live():
    """A Flash Flood Warning for Brevard County, FL that NWS Melbourne sent through IPAWS."""
    r = ipaws.crosscheck("FFW", ["012009"], utc(2026, 11, 2, 12, 27))
    assert r["status"] == "match", r
    assert r["matches"][0]["sender"] == "w-nws.webmaster@noaa.gov"
