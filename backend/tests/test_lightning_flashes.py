"""Lightning flash detection in synthetic videos made with ffmpeg."""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import pytest

from tracksound.sources.lightning import flashes


def make_video(path: Path, flashes_at=((3.0, 0.1), (7.5, 0.05), (12.2, 0.3)), lamp_on: float | None = 16.0,
               seconds: float = 20.0, fps: int = 25) -> Path:
    """Dark grey frames with bright flashes (start, duration), optionally a lamp switched on for good."""
    enable = "+".join(f"between(t,{a},{a + d})" for a, d in flashes_at)
    vf = f"drawbox=x=0:y=0:w=iw:h=ih/2:color=white@0.8:t=fill:enable='{enable}'"
    if lamp_on is not None:
        vf += f",drawbox=x=0:y=0:w=iw:h=ih:color=0x909090:t=fill:enable='gte(t,{lamp_on})'"
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=0x282828:s=160x90:r={fps}:d={seconds}",
           "-f", "lavfi", "-i", f"anoisesrc=d={seconds}:c=pink:r=8000:a=0.05",
           "-vf", vf, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", "-shortest", str(path)]
    subprocess.run(cmd, check=True, capture_output=True)
    return path


@pytest.fixture(scope="module")
def video(tmp_path_factory):
    return make_video(tmp_path_factory.mktemp("video") / "storm.mov")


def test_detects_flashes_and_ignores_a_lamp(video):
    tr = flashes.luma_trace(video)
    assert len(tr.t) == 500 and abs(np.median(np.diff(tr.t)) - 0.04) < 1e-6
    found = flashes.detect_flashes(tr, sensitivity=0.5)
    assert len(found) == 3
    assert np.allclose([f["t"] for f in found], [3.0, 7.5, 12.2], atol=0.045)   # within one frame
    assert all(f["strength"] > 50 for f in found)


def test_sensitivity_floor(video):
    tr = flashes.luma_trace(video)
    # a faint flicker of a few luma units is ignored at low sensitivity
    tr.yavg = tr.yavg.copy()
    tr.yhigh = tr.yhigh.copy()
    i = np.searchsorted(tr.t, 9.0)
    tr.yavg[i] += 3.0
    tr.yhigh[i] += 3.0
    low = flashes.detect_flashes(tr, sensitivity=0.0)
    high = flashes.detect_flashes(tr, sensitivity=1.0)
    assert not any(abs(f["t"] - 9.0) < 0.05 for f in low)
    assert any(abs(f["t"] - 9.0) < 0.05 for f in high)


def test_audio_offset_moves_flash_times(video, tmp_path):
    # delay the audio by 0.5 s: recording time 0 is the first audio sample, so flashes come 0.5 s "earlier"
    off = tmp_path / "offset.mov"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-itsoffset", "0.5", "-i", str(video), "-i", str(video),
                    "-map", "1:v", "-map", "0:a", "-c", "copy", str(off)], check=True, capture_output=True)
    assert abs(flashes.stream_offset(off) - 0.5) < 1e-3
    found = flashes.detect_flashes(flashes.luma_trace(off))
    assert abs(found[0]["t"] - 2.5) < 0.045


def test_trace_cache(video, tmp_path):
    cache = tmp_path / "luma.npz"
    a = flashes.load_or_build_trace(video, cache)
    assert cache.exists()
    b = flashes.load_or_build_trace(video, cache)
    np.testing.assert_array_equal(a.yavg, b.yavg)


def test_unreadable_video_raises(tmp_path):
    p = tmp_path / "junk.mp4"
    p.write_bytes(b"nope" * 100)
    with pytest.raises(ValueError):
        flashes.luma_trace(p)
