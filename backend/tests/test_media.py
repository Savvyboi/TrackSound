"""ffmpeg discovery and the ffprobe-free fallback (used when only the bundled ffmpeg exists)."""
from __future__ import annotations

import shutil
import subprocess

import pytest

from tracksound import audio_io, media


def _make(tmp_path, name, args):
    p = tmp_path / name
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args, str(p)], check=True)
    return p


@pytest.fixture
def files(tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("needs a system ffmpeg to make test files")
    return {
        "wav": _make(tmp_path, "a.wav", ["-f", "lavfi", "-i", "sine=f=440:r=8000:d=3", "-ac", "1"]),
        "mp3": _make(tmp_path, "b.mp3", ["-f", "lavfi", "-i", "sine=f=300:r=44100:d=4", "-ac", "2"]),
        "mp4": _make(tmp_path, "c.mp4", ["-f", "lavfi", "-i", "testsrc=size=64x48:rate=30:d=2", "-f", "lavfi",
                                         "-i", "sine=f=200:r=48000:d=2", "-ac", "2", "-shortest",
                                         "-metadata", "creation_time=2025-07-19T18:20:00Z", "-c:v", "mpeg4", "-c:a", "aac"]),
    }


@pytest.fixture
def no_ffprobe(monkeypatch):
    """Behave like a machine with only the bundled ffmpeg."""
    import imageio_ffmpeg
    media.ffmpeg_exe.cache_clear(); media.ffprobe_exe.cache_clear()
    monkeypatch.setenv("TRACKSOUND_FFMPEG", imageio_ffmpeg.get_ffmpeg_exe())
    monkeypatch.setenv("TRACKSOUND_FFPROBE", "")
    yield
    media.ffmpeg_exe.cache_clear(); media.ffprobe_exe.cache_clear()


def test_probe_without_ffprobe_matches_ffprobe(files, no_ffprobe):
    assert media.ffprobe_exe() is None and "imageio_ffmpeg" in media.ffmpeg_exe()
    got = {k: audio_io.probe(p) for k, p in files.items()}
    media.ffmpeg_exe.cache_clear(); media.ffprobe_exe.cache_clear()
    import os
    os.environ.pop("TRACKSOUND_FFMPEG"); os.environ.pop("TRACKSOUND_FFPROBE")
    if not shutil.which("ffprobe"):
        pytest.skip("no system ffprobe to compare with")
    want = {k: audio_io.probe(p) for k, p in files.items()}
    for k in files:
        g, w = got[k], want[k]
        assert g["has_audio"] == w["has_audio"] and g["has_video"] == w["has_video"], k
        assert g["audio_sample_rate"] == w["audio_sample_rate"] and g["audio_channels"] == w["audio_channels"], k
        assert abs(g["duration_s"] - w["duration_s"]) < 0.1, k
        assert (g["video_fps"] is None) == (w["video_fps"] is None), k
        if w["video_fps"]:
            assert abs(g["video_fps"] - w["video_fps"]) < 0.05


def test_bundled_ffmpeg_decodes_and_reads_tags(files, no_ffprobe, tmp_path):
    x, sr = audio_io.decode_to_standard(files["mp4"], tmp_path / "out.wav")
    assert sr == 4000 and abs(len(x) / sr - 2.0) < 0.1
    info = media.probe(files["mp4"])
    assert info["format"]["tags"].get("creation_time", "").startswith("2025-07-19")
    from tracksound.sources.alerts.issue_time import creation_time
    assert creation_time(files["mp4"]) == "2025-07-19T18:20:00Z"


def test_unreadable_file(tmp_path, no_ffprobe):
    p = tmp_path / "junk.mp3"
    p.write_bytes(b"not audio" * 50)
    with pytest.raises(ValueError, match="could not read"):
        media.probe(p)


def test_parse_ffmpeg_info_sample():
    text = """Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'clip.mov':
  Metadata:
    major_brand     : qt
    creation_time   : 2024-07-15T20:01:02.000000Z
    com.apple.quicktime.location.ISO6709: +38.7223-009.1393+010.000/
  Duration: 00:15:00.03, start: 0.000000, bitrate: 1234 kb/s
  Stream #0:0[0x1](und): Video: h264 (High) (avc1 / 0x31637661), yuv420p(tv, bt709, progressive), 1920x1080, 1131 kb/s, 29.97 fps, 29.97 tbr, 600 tbn (default)
    Metadata:
      creation_time   : 2024-07-15T20:01:02.000000Z
  Stream #0:1[0x2](und): Audio: aac (LC) (mp4a / 0x6134706D), 48000 Hz, stereo, fltp, 128 kb/s (default)
  Stream #0:2: Video: mjpeg (Baseline), yuvj420p, 300x300, 90k tbr, 90k tbn (attached pic)
At least one output file must be specified"""
    info = media.parse_ffmpeg_info(text)
    assert info["format"]["format_name"].startswith("mov") and abs(float(info["format"]["duration"]) - 900.03) < 1e-6
    assert info["format"]["tags"]["creation_time"].startswith("2024-07-15")
    v, a, pic = info["streams"]
    assert v["avg_frame_rate"] == "29.97/1" and v["tags"]["creation_time"].startswith("2024")
    assert a["sample_rate"] == "48000" and a["channels"] == 2
    assert pic["disposition"]["attached_pic"] == 1
