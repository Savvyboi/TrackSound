"""Flight archive parsing and caching (no network)."""
from __future__ import annotations

import io
import tarfile
from pathlib import Path

from dopplertrack import fetch

from tracksound.sources.aircraft import flights

FIX = Path(__file__).parent / "data" / "trace_full_a2f38b.json"   # one real adsb.lol trace (from dopplertrack's tests)


def _tar(n: int) -> bytes:
    buf = io.BytesIO()
    raw = FIX.read_bytes()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for k in range(n):
            ti = tarfile.TarInfo(f"traces/{k % 256:02x}/trace_full_{k:06x}.json")
            ti.size = len(raw)
            tf.addfile(ti, io.BytesIO(raw))
        ti = tarfile.TarInfo("README"); ti.size = 3; tf.addfile(ti, io.BytesIO(b"hi\n"))
    return buf.getvalue()


def test_parallel_parser_matches_sequential():
    data = _tar(950)                                   # several batches of 400
    box = (40.0, 41.5, -75.0, -72.0)
    seq = list(fetch.iter_tar_traces(io.BytesIO(data), box))
    par = list(flights.iter_tar_traces_parallel(io.BytesIO(data), box, workers=2, batch_size=400))
    assert len(seq) == len(par) == 950
    assert all(a.icao == b.icao and len(a.t) == len(b.t) for a, b in zip(seq, par))
    assert list(flights.iter_tar_traces_parallel(io.BytesIO(data), (0.0, 1.0, 0.0, 1.0), workers=2)) == []


def test_cache_reuse_by_containing_box(tmp_path):
    tr = list(fetch.iter_tar_traces(io.BytesIO(_tar(3)), (40.0, 41.5, -75.0, -72.0)))
    big = {"lat_min": 40.0, "lat_max": 41.5, "lon_min": -75.0, "lon_max": -72.0}
    meta = flights.save_cached(tmp_path, "2026-09-08", big, False, tr, {"pod": "test"})
    small = {"lat_min": 40.5, "lat_max": 41.0, "lon_min": -74.0, "lon_max": -73.0}
    assert flights.find_cached(tmp_path, "2026-09-08", small, False)["file"] == meta["file"]
    assert flights.find_cached(tmp_path, "2026-09-08", {**small, "lat_max": 42.0}, False) is None
    assert flights.find_cached(tmp_path, "2026-09-09", small, False) is None
    assert flights.find_cached(tmp_path, "2026-09-08", small, True) is None
    loaded = flights.load(flights.cache_file(tmp_path, meta))
    assert len(loaded) == 3 and loaded[0].icao == "a2f38b"
    gj = flights.geojson(loaded, small)
    assert gj["features"] and all(len(f["geometry"]["coordinates"]) >= 2 for f in gj["features"])
