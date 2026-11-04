"""Decoding uploads and reading the case's standard audio copy.

Every upload is decoded once with ffmpeg into ``audio.wav``: mono, 4 kHz, float32. That
covers aircraft tones (40-450 Hz), mains-hum harmonics and thunder (up to the 2 kHz Nyquist
limit). Sources read it with ``load_case_audio`` / ``read_segment``; the untouched original is
kept for anything that needs video frames or the full bandwidth.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf

STANDARD_SR = 4000


def probe(path: str | Path) -> dict:
    """ffprobe summary: duration_s, has_video, has_audio, audio sample rate, video fps."""
    cmd = ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise ValueError(f"ffprobe could not read the file: {r.stderr.strip()[:300]}")
    info = json.loads(r.stdout or "{}")
    streams = info.get("streams", [])
    v = [s for s in streams if s.get("codec_type") == "video" and s.get("disposition", {}).get("attached_pic", 0) == 0]
    a = [s for s in streams if s.get("codec_type") == "audio"]
    dur = float(info.get("format", {}).get("duration") or 0.0)
    fps = None
    if v:
        num, _, den = (v[0].get("avg_frame_rate") or "0/1").partition("/")
        try:
            fps = float(num) / float(den or 1) if float(den or 1) else None
        except ValueError:
            fps = None
    return {
        "duration_s": dur,
        "has_video": bool(v),
        "has_audio": bool(a),
        "audio_sample_rate": int(a[0].get("sample_rate", 0)) if a else None,
        "audio_channels": int(a[0].get("channels", 0)) if a else None,
        "video_fps": fps,
        "format": info.get("format", {}).get("format_name"),
    }


def decode_to_standard(src: str | Path, dst: str | Path, sr: int = STANDARD_SR) -> tuple[np.ndarray, int]:
    """Decode any ffmpeg-readable file to mono float32 at ``sr`` and write it to ``dst`` (WAV).
    Returns the samples too, so the caller can build the spectrogram without re-reading."""
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-i", str(src), "-vn", "-ac", "1", "-ar", str(sr),
           "-f", "f32le", "-acodec", "pcm_f32le", "-"]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        raise ValueError(f"ffmpeg could not decode the audio: {r.stderr.decode(errors='replace').strip()[:300]}")
    x = np.frombuffer(r.stdout, dtype=np.float32).copy()
    if len(x) == 0:
        raise ValueError("the file has no audio samples")
    sf.write(str(dst), x, sr, subtype="FLOAT")
    return x, sr


def load_audio_file(path: str | Path) -> tuple[np.ndarray, int]:
    x, sr = sf.read(str(path), dtype="float32", always_2d=False)
    if x.ndim > 1:
        x = x.mean(axis=1)
    return x, int(sr)


def read_segment(path: str | Path, t0: float, t1: float) -> tuple[np.ndarray, int]:
    """Samples between t0 and t1 seconds (clipped to the file)."""
    with sf.SoundFile(str(path)) as f:
        sr = f.samplerate
        a = max(0, int(round(t0 * sr)))
        b = min(f.frames, int(round(t1 * sr)))
        if b <= a:
            return np.zeros(0, np.float32), sr
        f.seek(a)
        x = f.read(b - a, dtype="float32", always_2d=False)
    if x.ndim > 1:
        x = x.mean(axis=1)
    return x, sr


def wav_bytes(x: np.ndarray, sr: int) -> bytes:
    """16-bit PCM WAV for browser playback, peak-normalised."""
    import io
    peak = float(np.max(np.abs(x))) if len(x) else 0.0
    y = (x / peak * 0.9) if peak > 0 else x
    buf = io.BytesIO()
    sf.write(buf, y.astype(np.float32), int(sr), format="WAV", subtype="PCM_16")
    return buf.getvalue()
