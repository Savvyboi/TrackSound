"""Reference data plumbing: parsers of each archive's format, the 7z reader, CSV import, caching."""
from __future__ import annotations

import datetime as dt
import lzma
import struct
import zipfile

import numpy as np
import pytest

from tracksound.sources.enf.references import aemo, common, fingrid, imported, neso, pgf
from tracksound.sources.enf.references.sevenzip import SevenZip


def utc(s: str) -> float:
    return dt.datetime.fromisoformat(s).replace(tzinfo=dt.timezone.utc).timestamp()


# ---- 7z ------------------------------------------------------------------------------------------

def _num(v: int) -> bytes:
    """7z UINT64 (only the forms needed here)."""
    if v < 0x80:
        return bytes([v])
    if v < 0x4000:
        return bytes([0x80 | (v >> 8), v & 0xFF])
    return bytes([0xFF]) + struct.pack("<Q", v)


def make_7z(files: dict[str, bytes]) -> bytes:
    """A minimal solid 7z archive (one LZMA2 folder, plain header), like 7-Zip writes."""
    names = list(files)
    data = b"".join(files[n] for n in names)
    packed = lzma.compress(data, format=lzma.FORMAT_RAW, filters=[{"id": lzma.FILTER_LZMA2, "dict_size": 1 << 20}])
    h = bytearray([0x01, 0x04])                                   # Header, MainStreamsInfo
    h += bytes([0x06]) + _num(0) + _num(1) + bytes([0x09]) + _num(len(packed)) + bytes([0x00])
    h += bytes([0x07, 0x0B]) + _num(1) + bytes([0x00])           # one folder, not external
    h += _num(1) + bytes([0x21]) + bytes([0x21, 0x01, 16])       # one coder: LZMA2, 1 byte of props
    h += bytes([0x0C]) + _num(len(data)) + bytes([0x00])
    h += bytes([0x08, 0x0D]) + _num(len(names))
    h += bytes([0x09]) + b"".join(_num(len(files[n])) for n in names[:-1]) + bytes([0x00])
    h += bytes([0x00])                                            # end of MainStreamsInfo
    nm = b"\x00" + b"".join(n.encode("utf-16-le") + b"\x00\x00" for n in names)
    h += bytes([0x05]) + _num(len(names)) + bytes([0x11]) + _num(len(nm)) + nm + bytes([0x00])
    h += bytes([0x00])
    start = struct.pack("<QQI", len(packed), len(h), 0)
    return b"7z\xbc\xaf\x27\x1c\x00\x04" + struct.pack("<I", 0) + start + packed + bytes(h)


def test_sevenzip_reads_a_solid_lzma2_archive(tmp_path):
    files = {"2026-03/a.csv": b"Time,Value\n" + b"x" * 5000, "2026-03/b.csv": b"hello", "c.txt": bytes(range(256)) * 50}
    p = tmp_path / "t.7z"
    p.write_bytes(make_7z(files))
    arc = SevenZip(p)
    assert [e.name for e in arc.entries] == list(files)
    assert {e.name: data for e, data in arc.files()} == files


def test_sevenzip_rejects_other_files(tmp_path):
    p = tmp_path / "x.7z"
    p.write_bytes(b"PK\x03\x04 not a 7z")
    with pytest.raises(ValueError):
        SevenZip(p)


# ---- day arrays and cleaning ------------------------------------------------------------------------

def test_samples_to_day_resolutions():
    d0 = int(utc("2026-01-01T00:00:00"))
    t = d0 + np.arange(0, 100, 0.1)                      # 10 Hz
    a = common.samples_to_day(t, 50 + 0.001 * np.round(t - d0), d0, resolution_s=0.1)
    assert a[10] == pytest.approx(50.010, abs=1e-6) and np.isnan(a[200])
    t4 = d0 + np.arange(0, 400, 4.0)                     # 4 s, with a 40 s hole
    f4 = 50 + 0.0001 * (t4 - d0)
    keep = (t4 - d0 < 100) | (t4 - d0 > 140)
    b = common.samples_to_day(t4[keep], f4[keep], d0, resolution_s=4.0, max_gap_s=10.0)
    assert b[50] == pytest.approx(50.005, abs=1e-6) and np.isnan(b[120]) and np.isfinite(b[150])


def test_clean_blanks_frozen_meter():
    x = 50 + 0.001 * np.arange(200.0)
    x[50:120] = 50.123                                   # 70 s stuck
    x[150:160] = 50.2                                    # 10 s: plausible plateau, kept
    c = common.clean(x, 50.0)
    assert np.isnan(c[50:120]).all() and np.isfinite(c[49]) and np.isfinite(c[150:160]).all() and np.isfinite(c[121])
    assert np.isnan(common.clean(np.array([50.0, 3.0, 50.0]), 50.0)[1])


def test_day_cache_merge(tmp_path):
    cache = common.DayCache(tmp_path)
    d0 = int(utc("2026-02-01T00:00:00"))
    a = np.full(86400, np.nan)
    a[:100] = 50.0
    b = np.full(86400, np.nan)
    b[50:200] = 49.9
    cache.merge(d0, a)
    m = cache.merge(d0, b)
    assert m[0] == 50.0 and m[60] == 50.0 and m[150] == pytest.approx(49.9) and np.isnan(m[300])


# ---- NESO ---------------------------------------------------------------------------------------

def test_neso_time_formats():
    assert neso.parse_time("2019-08-09 16:52:38 +0100") == utc("2019-08-09T15:52:38")
    assert neso.parse_time("2025-07-01T00:00:00") == utc("2025-07-01T00:00:00")
    assert neso.parse_time("2021-11-01 00:00:01") == utc("2021-11-01T00:00:01")
    assert neso.parse_time("nonsense") is None


def test_neso_month_file_with_offsets(tmp_path):
    p = tmp_path / "f-2019-8.zip"
    rows = "dtm,f\r\n" + "".join(f"2019-08-09 16:52:{s:02d} +0100,{50 - s / 1000:.3f}\r\n" for s in range(30))
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("f 2019 8.csv", rows)
    df = neso.read_month_file(p)
    assert df.attrs["explicit"] and len(df) == 30
    assert df["t_label"].iloc[0] == utc("2019-08-09T15:52:00")


def test_neso_month_file_naive(tmp_path):
    p = tmp_path / "fnew-2025-7.csv"
    p.write_text("dtm,f\n2025-07-01 00:00:00,50.01\n2025-07-01 00:00:01,50.02\n")
    df = neso.read_month_file(p)
    assert not df.attrs["explicit"] and df["t_label"].tolist() == [utc("2025-07-01T00:00:00"), utc("2025-07-01T00:00:01")]


# ---- Fingrid, AEMO, PGF ------------------------------------------------------------------------------

def test_fingrid_local_time_and_dst():
    # 2026-10-25: clocks go back at 04:00 EEST -> 03:00 EET, so 03:00-04:00 local happens twice
    lines = ["Time,Value"]
    for h, m in [(2, 59), (3, 0), (3, 30), (3, 59), (3, 0), (3, 30), (4, 0)]:
        for tenth in range(10):
            lines.append(f"2026-10-25 {h:02d}:{m:02d}:00.{tenth}00,50.0{h}")
    t, f = fingrid.parse_day_csv("\n".join(lines).encode())
    assert len(t) == 70 and np.all(np.diff(t) >= 0)
    assert t[0] == pytest.approx(utc("2026-10-24T23:59:00"))       # 02:59 EEST = UTC+3
    assert t[-1] == pytest.approx(utc("2026-10-25T02:00:00.900"))   # 04:00 EET = UTC+2


def test_fingrid_months_for_utc_day():
    assert fingrid.local_months_for_day(int(utc("2026-09-30T00:00:00"))) == [(2026, 9), (2026, 10)]
    assert fingrid.local_months_for_day(int(utc("2026-09-14T00:00:00"))) == [(2026, 9)]


def test_aemo_report_parsing():
    text = "\n".join([
        "C,SETP.WORLD,FPP_RUN,AEMO,PUBLIC,2026/11/04,14:04:10,1,FPP_RUN,1",
        "I,FPP,REGION_FREQ_MEASURE,1,INTERVAL_DATETIME,MEASUREMENT_DATETIME,REGIONID,VERSIONNO,FREQ_DEVIATION_HZ,"
        "HZ_QUALITY_FLAG,FREQ_MEASURE_HZ,FM_ALIGNMENT_FLAG,LASTCHANGED",
        'D,FPP,REGION_FREQ_MEASURE,1,"2026/11/04 14:00:00","2026/11/04 13:55:04",NSW1,1,-0.01600000,1,0.009,1,"x"',
        'D,FPP,REGION_FREQ_MEASURE,1,"2026/11/04 14:00:00","2026/11/04 13:55:04",TAS1,1,0.02100000,1,0.007,1,"x"',
        'D,FPP,REGION_FREQ_MEASURE,1,"2026/11/04 14:00:00","2026/11/04 13:55:08",NSW1,1,-0.01400000,0,0.010,1,"x"',
    ])
    out = aemo.parse_report(text)
    assert out["NSW1"][0] == [utc("2026-11-04T03:55:04")]          # AEST = UTC+10; bad-quality row dropped
    assert out["NSW1"][1] == [pytest.approx(49.984)]
    assert out["TAS1"][1] == [pytest.approx(50.021)]


def test_pgf_campaign_file(tmp_path):
    p = tmp_path / "tx.csv"
    p.write_text("Time;f60_US_TX01;QI_US_TX01\n2019-05-15 04:35:36;3.83139;0\n2019-05-15 04:35:37;2.5;1\n"
                 "2019-05-15 04:35:38;-12.0;0\n")
    t, f, nominal = pgf.parse_campaign_csv(p)
    assert nominal == 60 and list(t) == [utc("2019-05-15T04:35:36"), utc("2019-05-15T04:35:38")]
    assert f[0] == pytest.approx(60.00383139) and f[1] == pytest.approx(59.988)


def test_pgf_campaigns_have_coverage():
    for c in pgf.CAMPAIGNS:
        info = pgf.info_for(c)
        cov = info.coverage[0]
        assert cov["t1_utc"] > cov["t0_utc"] and info.nominal_hz in (50, 60) and info.licence


# ---- CSV import ----------------------------------------------------------------------------------------

def test_import_csv_formats(tmp_path):
    t0 = int(utc("2026-03-01T12:00:00"))
    iso = "time,frequency\n" + "".join(f"{dt.datetime.fromtimestamp(t0 + i, dt.timezone.utc).isoformat()},"
                                       f"{50 + 0.001 * (i % 7):.3f}\n" for i in range(300))
    meta = imported.import_csv(tmp_path, iso.encode(), "test", "Somewhere", 50)
    assert meta["id"].startswith("er_") and meta["resolution_s"] == 1.0
    assert meta["coverage"] == [{"t0_utc": float(t0), "t1_utc": float(t0 + 300)}]
    s = imported.load_imported(tmp_path, meta["id"], t0 - 10, t0 + 20)
    assert np.isnan(s.values[:10]).all() and s.values[10] == pytest.approx(50.0) and s.values[11] == pytest.approx(50.001)
    # unix seconds, semicolons, no header, 10 Hz, with a long gap -> two coverage intervals
    rows = [f"{t0 + k / 10:.1f};{50.02:.3f}" for k in range(3000)] + [f"{t0 + 3600 + k / 10:.1f};50.03" for k in range(3000)]
    meta2 = imported.import_csv(tmp_path, "\n".join(rows).encode(), "fast", "X", 50)
    assert meta2["resolution_s"] == pytest.approx(0.1) and len(meta2["coverage"]) == 2
    assert [m["id"] for m in imported.list_imported(tmp_path)] == [meta["id"], meta2["id"]]
    imported.delete_imported(tmp_path, meta2["id"])
    assert len(imported.list_imported(tmp_path)) == 1


def test_import_csv_rejects_bad_input(tmp_path):
    with pytest.raises(ValueError):
        imported.import_csv(tmp_path, b"time,frequency\n1,2\n", "x", "y", 50)              # too few rows
    rows = "time,frequency\n" + "".join(f"{1_700_000_000 + i},{59.99}\n" for i in range(100))
    with pytest.raises(ValueError):
        imported.import_csv(tmp_path, rows.encode(), "x", "y", 50)                          # 60 Hz data as 50
    with pytest.raises(ValueError):
        imported.import_csv(tmp_path, rows.encode(), "x", "y", 55)
