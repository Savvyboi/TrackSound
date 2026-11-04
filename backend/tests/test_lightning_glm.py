"""GOES GLM: file names, S3 listings, satellite choice, NetCDF decoding and the fetch pipeline.

The default tests are offline (synthetic HDF5 files that copy the real files' encodings and a
fake S3); ``test_real_glm_download`` (slow) fetches real files from NOAA's AWS bucket.
"""
from __future__ import annotations

import calendar
import datetime as dt
from pathlib import Path

import h5py
import numpy as np
import pytest

from tracksound.sources.lightning import glm


def utc(*a) -> float:
    return float(calendar.timegm(a + (0,) * (6 - len(a))))


def glm_name(sat: str, t0: float, t1: float) -> str:
    def stamp(t):
        d = dt.datetime.fromtimestamp(t, dt.timezone.utc)
        return f"{d.year}{d.timetuple().tm_yday:03d}{d.hour:02d}{d.minute:02d}{d.second:02d}0"
    return f"OR_GLM-L2-LCFA_{sat}_s{stamp(t0)}_e{stamp(t1)}_c{stamp(t1 + 20)}.nc"


def write_glm(path: Path, t_start: float, flashes, style: str = "new") -> Path:
    """A minimal GLM L2 file with the real files' variable encodings.

    flashes: [(seconds after t_start, lat, lon, energy_J, quality_flag)]
    style "new": int16 _Unsigned, scale 0.00038148 s, offset -5 s, 'seconds since'.
    style "old" (2018): int16 signed, scale 2, 'milliseconds since'.
    """
    d = dt.datetime.fromtimestamp(t_start, dt.timezone.utc)
    epoch = d.strftime("%Y-%m-%d %H:%M:%S.000")
    off = np.array([f[0] for f in flashes], dtype=np.float64)
    with h5py.File(path, "w") as f:
        if style == "new":
            raw = np.round((off + 5.0) / 0.00038148).astype(np.int64).astype(np.uint16).view(np.int16)
            ds = f.create_dataset("flash_time_offset_of_first_event", data=raw)
            ds.attrs["_Unsigned"] = np.bytes_(b"true")
            ds.attrs["scale_factor"] = np.array([0.00038148], np.float32)
            ds.attrs["add_offset"] = np.array([-5.0], np.float32)
            ds.attrs["units"] = np.bytes_(f"seconds since {epoch}".encode())
        else:
            ds = f.create_dataset("flash_time_offset_of_first_event", data=np.round(off * 1000 / 2.0).astype(np.int16))
            ds.attrs["scale_factor"] = np.array([2.0], np.float32)
            ds.attrs["add_offset"] = np.array([0.0], np.float32)
            ds.attrs["units"] = np.bytes_(f"milliseconds since {epoch}".encode())
        f.create_dataset("flash_lat", data=np.array([x[1] for x in flashes], np.float32))
        f.create_dataset("flash_lon", data=np.array([x[2] for x in flashes], np.float32))
        e = np.round(np.array([x[3] for x in flashes]) / 1.52597e-15).astype(np.int64).astype(np.uint16).view(np.int16)
        ds = f.create_dataset("flash_energy", data=e)
        ds.attrs["_Unsigned"] = np.bytes_(b"true")
        ds.attrs["_FillValue"] = np.array([-1], np.int16)
        ds.attrs["scale_factor"] = np.array([1.52597e-15], np.float32)
        ds.attrs["add_offset"] = np.array([0.0], np.float32)
        ds = f.create_dataset("flash_quality_flag", data=np.array([x[4] for x in flashes], np.int16))
        ds.attrs["_Unsigned"] = np.bytes_(b"true")
        ds.attrs["_FillValue"] = np.array([-1], np.int16)
        ds = f.create_dataset("product_time", data=np.float64(t_start - glm.J2000_UNIX))
        ds.attrs["units"] = np.bytes_(b"seconds since 2000-01-01 12:00:00")
    return path


def test_key_times_and_prefix():
    k = "GLM-L2-LCFA/2025/200/18/OR_GLM-L2-LCFA_G19_s20252001800000_e20252001800200_c20252001800221.nc"
    assert glm.parse_key_times(k) == (utc(2025, 7, 19, 18, 0, 0), utc(2025, 7, 19, 18, 0, 20))
    assert glm.hour_prefix(utc(2025, 7, 19, 18, 59, 59)) == "GLM-L2-LCFA/2025/200/18/"
    assert glm.parse_key_times("nonsense.nc") is None


def test_parse_listing_with_continuation():
    xml = ('<?xml version="1.0" encoding="UTF-8"?><ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
           '<Name>noaa-goes19</Name><NextContinuationToken>abc+/=</NextContinuationToken><IsTruncated>true</IsTruncated>'
           '<Contents><Key>a.nc</Key><Size>10</Size></Contents><Contents><Key>b.nc</Key><Size>20</Size></Contents>'
           '</ListBucketResult>')
    entries, token = glm.parse_listing(xml)
    assert entries == [("a.nc", 10), ("b.nc", 20)] and token == "abc+/="
    entries, token = glm.parse_listing(xml.replace("<IsTruncated>true", "<IsTruncated>false"))
    assert token is None


def test_satellite_choice():
    florida = {"lat_min": 27.5, "lat_max": 28.5, "lon_min": -82.5, "lon_max": -81.5}
    calif = {"lat_min": 36.0, "lat_max": 37.0, "lon_min": -121.0, "lon_max": -120.0}
    t24, t26, t22 = utc(2024, 7, 15, 20), utc(2026, 7, 15, 20), utc(2022, 6, 1, 20)
    assert glm.satellites_for(florida, t24, t24 + 600)[0] == "G16"
    assert glm.satellites_for(florida, t26, t26 + 600)[0] == "G19"
    assert glm.satellites_for(calif, t22, t22 + 600)[:2] == ["G17", "G16"]
    assert glm.satellites_for(calif, t26, t26 + 600)[0] == "G18"
    # the day of the GOES-East handover: G19 from 15 UTC, G16 still listed as a fallback
    h = utc(2025, 4, 7, 18)
    assert glm.satellites_for(florida, h, h + 600)[:2] == ["G19", "G16"]
    assert glm.satellites_for(florida, t24, t24 + 60, "west") == ["G18"]
    assert glm.satellites_for(florida, t24, t24 + 60, "goes-16") == ["G16"]
    with pytest.raises(ValueError, match="outside its view"):
        glm.satellites_for({"lat_min": 50, "lat_max": 51, "lon_min": 7, "lon_max": 8}, t24, t24 + 60)
    with pytest.raises(ValueError, match="no GLM archive"):
        glm.satellites_for(florida, utc(2010, 7, 1), utc(2010, 7, 1, 1))
    with pytest.raises(ValueError, match="unknown satellite"):
        glm.satellites_for(florida, t24, t24 + 60, "meteosat")


@pytest.mark.parametrize("style", ["new", "old"])
def test_read_flashes_both_encodings(tmp_path, style):
    t0 = utc(2025, 7, 19, 18, 0, 0)
    flashes = [(1.25, 28.1, -81.9, 3e-14, 0), (19.5, 28.2, -82.0, 1e-13, 3)]
    if style == "old":
        flashes.append((-25.1, 2.0, -94.0, 1e-14, 0))    # 2018 files hold junk timed outside the file
    p = write_glm(tmp_path / f"{style}.nc", t0, flashes, style)
    fl = glm.read_flashes(p, (t0, t0 + 20))
    assert len(fl["t_utc"]) == 2
    np.testing.assert_allclose(fl["t_utc"], [t0 + 1.25, t0 + 19.5], atol=2e-3)
    np.testing.assert_allclose(fl["lat"], [28.1, 28.2], atol=1e-5)
    np.testing.assert_allclose(fl["energy_j"], [3e-14, 1e-13], rtol=0.05)
    assert list(fl["quality"]) == [0.0, 3.0]
    assert glm.parse_time_units("milliseconds since 2018-05-30 20:20:00.000") == (1e-3, utc(2018, 5, 30, 20, 20))


class FakeS3:
    """Serves ListObjectsV2 pages (two keys per page) and file bodies from a dict."""

    def __init__(self, files: dict[str, bytes], bucket: str):
        self.files, self.bucket, self.calls = files, bucket, []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        base = f"https://{self.bucket}.s3.amazonaws.com/"

        class R:
            status_code = 200
            text = ""
            content = b""
        r = R()
        if url == base:
            keys = sorted(k for k in self.files if k.startswith(params["prefix"]))
            start = int(params.get("continuation-token", 0))
            page = keys[start:start + 2]
            more = start + 2 < len(keys)
            body = "".join(f"<Contents><Key>{k}</Key><Size>{len(self.files[k])}</Size></Contents>" for k in page)
            tok = f"<NextContinuationToken>{start + 2}</NextContinuationToken>" if more else ""
            r.text = (f'<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
                      f"<IsTruncated>{'true' if more else 'false'}</IsTruncated>{tok}{body}</ListBucketResult>")
        elif url.startswith(base) and url[len(base):] in self.files:
            r.content = self.files[url[len(base):]]
        elif url.startswith("https://"):
            r.text = '<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/"></ListBucketResult>'
            if not url.endswith("/"):
                r.status_code = 404
        return r


def test_fetch_pipeline_offline(tmp_path):
    t0 = utc(2025, 7, 19, 18, 0, 0)
    files = {}
    src = tmp_path / "src"
    src.mkdir()
    for i in range(6):                     # 18:00:00 .. 18:02:00, one flash inside the box per file
        ts = t0 + 20 * i
        name = glm_name("G19", ts, ts + 20)
        p = write_glm(src / name, ts, [(5.0, 28.0 + 0.01 * i, -81.9, 2e-14, 0), (6.0, 35.0, -100.0, 2e-14, 0)])
        files[glm.hour_prefix(ts) + name] = p.read_bytes()
    fake = FakeS3(files, "noaa-goes19")
    bbox = {"lat_min": 27.5, "lat_max": 28.5, "lon_min": -82.5, "lon_max": -81.5}
    seen = []
    strikes, info = glm.fetch_flashes(tmp_path / "cache", bbox, t0 + 30, t0 + 90, "auto",
                                      progress=lambda f, m=None: seen.append(f), workers=3, http=fake)
    # files starting 18:00:20 .. 18:01:20 overlap the window; flashes at +5 s of each, inside [t0+30, t0+90]
    assert [s["t_utc"] for s in strikes] == pytest.approx([t0 + 45, t0 + 65, t0 + 85], abs=0.01)
    assert {s["source"] for s in strikes} == {"glm-G19"} and {s["kind"] for s in strikes} == {"flash"}
    assert all(s["sigma_m"] == glm.SIGMA_GOOD_M for s in strikes)
    assert info["satellites"] == ["G19"] and info["n_files"] == 4 and seen and max(seen) <= 1.0
    assert any("continuation-token" in p for _, p in fake.calls)          # paged listing was followed
    n_calls = len(fake.calls)
    strikes2, _ = glm.fetch_flashes(tmp_path / "cache", bbox, t0 + 30, t0 + 90, "auto", http=fake)
    assert strikes2 == strikes
    assert not any(not u.endswith("/") for u, _ in fake.calls[n_calls:])   # files came from the cache


def test_fetch_reports_missing_data(tmp_path):
    fake = FakeS3({}, "noaa-goes19")
    bbox = {"lat_min": 27.5, "lat_max": 28.5, "lon_min": -82.5, "lon_max": -81.5}
    t = utc(2026, 1, 5, 3)
    with pytest.raises(ValueError, match="no GLM files found"):
        glm.fetch_flashes(tmp_path, bbox, t, t + 600, http=fake)


@pytest.mark.slow
def test_real_glm_download(tmp_path):
    """Real GOES-16 files: an active afternoon over Florida (2024-07-15 20:00-20:03 UTC)."""
    bbox = {"lat_min": 25.0, "lat_max": 31.0, "lon_min": -87.6, "lon_max": -80.0}
    t0 = utc(2024, 7, 15, 20, 0, 0)
    strikes, info = glm.fetch_flashes(tmp_path, bbox, t0, t0 + 180)
    assert info["satellites"] == ["G16"] and 9 <= info["n_files"] <= 11
    assert len(strikes) > 100
    t = np.array([s["t_utc"] for s in strikes])
    assert t.min() >= t0 and t.max() <= t0 + 180 and np.all(np.diff(t) >= 0)
    lat = np.array([s["lat"] for s in strikes])
    lon = np.array([s["lon"] for s in strikes])
    assert lat.min() >= 25 and lat.max() <= 31 and lon.min() >= -87.6 and lon.max() <= -80
    assert {s["source"] for s in strikes} == {"glm-G16"}
    e = np.array([s["energy"] for s in strikes if s["energy"] is not None])
    assert len(e) and 1e-16 < np.median(e) < 1e-11            # femtojoules to picojoules
