"""Lightning flashes in video: sudden, short brightenings of the frame.

Light arrives effectively instantly, so a flash at recording time ``f`` from a catalogued
strike at UTC ``t`` gives the recording clock directly: ``start_utc = t - f``.

Method: ffmpeg decodes the video at 64 px width and ``signalstats`` reports each frame's mean
luma (YAVG) and 90th-percentile luma (YHIGH, which catches a flash lighting only the sky).
A flash is a jump above the trailing median of the previous ~1.5 s that exceeds both an
absolute floor and ``k`` times the frame-to-frame noise (both shrink as ``sensitivity``
rises), and that fades again within ~2 s (a light switched on or a scene cut stays bright).
Consecutive bright frames (gaps under 0.3 s: multi-stroke flashes flicker) are one flash,
timed at its first bright frame.
"""
from __future__ import annotations

import json
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
from scipy import ndimage

ProgressFn = Callable[[float, str | None], None]

BASELINE_S = 1.5
MERGE_GAP_S = 0.3
FADE_S = 2.0


@dataclass
class LumaTrace:
    t: np.ndarray          # recording time of each frame (s)
    yavg: np.ndarray
    yhigh: np.ndarray


def stream_offset(path: str | Path) -> float:
    """Seconds to subtract from a video frame's pts_time to get recording time.

    ffmpeg shifts all streams so the earliest starts at 0; the decoded audio (recording
    time 0) begins at the audio stream's own start, which can be a little later.
    """
    cmd = ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        return 0.0
    info = json.loads(r.stdout or "{}")
    starts = {}
    for s in info.get("streams", []):
        try:
            starts.setdefault(s.get("codec_type"), float(s.get("start_time")))
        except (TypeError, ValueError):
            continue
    if "audio" not in starts or "video" not in starts:
        return 0.0
    first = min(v for v in starts.values())
    return float(starts["audio"] - first)


def luma_trace(path: str | Path, duration_s: float | None = None, progress: ProgressFn | None = None,
               cancelled: Callable[[], bool] | None = None) -> LumaTrace:
    """Per-frame luma statistics of the first video stream (streamed from ffmpeg)."""
    vf = "scale=64:-2,signalstats,metadata=mode=print:file=-"
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-map", "0:v:0", "-vf", vf, "-an", "-f", "null", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1 << 16)
    err_chunks: list[str] = []
    t_err = threading.Thread(target=lambda: err_chunks.append(proc.stderr.read()), daemon=True)
    t_err.start()
    ts: list[float] = []
    ya: list[float] = []
    yh: list[float] = []
    cur_t = None
    cur_a = cur_h = np.nan
    n = 0
    try:
        for line in proc.stdout:
            if line.startswith("frame:"):
                if cur_t is not None:
                    ts.append(cur_t), ya.append(cur_a), yh.append(cur_h)
                k = line.find("pts_time:")
                try:
                    cur_t = float(line[k + 9:].split()[0]) if k >= 0 else None
                except (ValueError, IndexError):
                    cur_t = None
                cur_a = cur_h = np.nan
                n += 1
                if n % 500 == 0:
                    if cancelled and cancelled():
                        proc.kill()
                        break
                    if progress and duration_s and cur_t is not None:
                        progress(min(0.99, cur_t / duration_s), f"Reading video frames ({cur_t:.0f} s)")
            elif line.startswith("lavfi.signalstats.YAVG="):
                cur_a = float(line.split("=", 1)[1])
            elif line.startswith("lavfi.signalstats.YHIGH="):
                cur_h = float(line.split("=", 1)[1])
        if cur_t is not None:
            ts.append(cur_t), ya.append(cur_a), yh.append(cur_h)
    finally:
        rc = proc.wait()
        t_err.join(timeout=5)
    if rc != 0 and not (cancelled and cancelled()):
        msg = "".join(err_chunks).strip()[:300]
        raise ValueError(f"ffmpeg could not read the video frames: {msg or 'exit code ' + str(rc)}")
    t = np.asarray(ts, dtype=np.float64)
    a = np.asarray(ya, dtype=np.float64)
    h = np.asarray(yh, dtype=np.float64)
    ok = np.isfinite(t) & np.isfinite(a)
    t, a, h = t[ok], a[ok], h[ok]
    h = np.where(np.isfinite(h), h, a)
    order = np.argsort(t, kind="stable")
    return LumaTrace(t=t[order] - stream_offset(path), yavg=a[order], yhigh=h[order])


def flash_thresholds(sensitivity: float) -> tuple[float, float]:
    """(k for k * noise, absolute luma floor) for a sensitivity in [0, 1]."""
    s = float(np.clip(sensitivity, 0.0, 1.0))
    return 4.0 + 8.0 * (1.0 - s), 2.0 + 6.0 * (1.0 - s)


def _trailing_median(x: np.ndarray, n: int) -> np.ndarray:
    """Median over the n frames before each frame (frame itself excluded)."""
    n = max(1, n)
    m = ndimage.median_filter(x, size=n, mode="nearest")
    shift = n // 2 + 1
    out = np.empty_like(x)
    if shift >= len(x):
        out[:] = x[0]
        return out
    out[shift:] = m[:len(x) - shift]
    out[:shift] = x[0]
    return out


def _jump(y: np.ndarray, n_base: int) -> tuple[np.ndarray, float]:
    base = _trailing_median(y, n_base)
    dy = np.diff(y)
    noise = 1.4826 * float(np.median(np.abs(dy - np.median(dy)))) / np.sqrt(2.0) if len(dy) else 0.0
    return y - base, noise


def detect_flashes(trace: LumaTrace, sensitivity: float = 0.5) -> list[dict]:
    """Flashes ``[{t, strength}]`` (strength = brightness jump in luma units, 0..255)."""
    t, ya, yh = trace.t, trace.yavg, trace.yhigh
    if len(t) < 5:
        return []
    fps = 1.0 / float(np.median(np.diff(t))) if len(t) > 1 and np.median(np.diff(t)) > 0 else 30.0
    n_base = max(5, int(round(BASELINE_S * fps)))
    k, floor = flash_thresholds(sensitivity)
    ja, na = _jump(ya, n_base)
    jh, nh = _jump(yh, n_base)
    bright = (ja >= max(floor, k * na)) | (jh >= max(floor * 1.5, k * nh))
    idx = np.flatnonzero(bright)
    if len(idx) == 0:
        return []
    groups: list[list[int]] = [[int(idx[0])]]
    for i in idx[1:]:
        if t[i] - t[groups[-1][-1]] <= MERGE_GAP_S:
            groups[-1].append(int(i))
        else:
            groups.append([int(i)])
    out = []
    for g in groups:
        i0, i1 = g[0], g[-1]
        peak_a, peak_h = float(np.max(ja[g])), float(np.max(jh[g]))
        base_a = ya[i0] - ja[i0]
        base_h = yh[i0] - jh[i0]
        after = (t > t[i1]) & (t <= t[i1] + FADE_S)
        if after.any():
            # a flash fades: some frame soon after must be back near the old baseline
            back_a = float(np.min(ya[after] - base_a))
            back_h = float(np.min(yh[after] - base_h))
            if back_a > 0.5 * max(peak_a, 1e-9) and back_h > 0.5 * max(peak_h, 1e-9):
                continue
        if t[i1] - t[i0] > 3.0:          # too long for lightning
            continue
        out.append({"t": round(float(t[i0]), 3), "strength": round(max(peak_a, peak_h), 2)})
    return out


# ---- cache ----------------------------------------------------------------------------------

def load_or_build_trace(video_path: Path, cache_path: Path, duration_s: float | None = None,
                        progress: ProgressFn | None = None, cancelled: Callable[[], bool] | None = None) -> LumaTrace:
    """The luma trace, cached next to the case state (rebuilt when the video changes)."""
    st = video_path.stat()
    stamp = np.array([st.st_size, st.st_mtime_ns], dtype=np.int64)
    if cache_path.exists():
        try:
            with np.load(cache_path, allow_pickle=False) as z:
                if np.array_equal(z["stamp"], stamp):
                    return LumaTrace(t=z["t"], yavg=z["yavg"], yhigh=z["yhigh"])
        except (OSError, KeyError, ValueError):
            pass
    tr = luma_trace(video_path, duration_s=duration_s, progress=progress, cancelled=cancelled)
    if cancelled and cancelled():
        return tr
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache_path.with_name(cache_path.stem + ".tmp.npz")
    np.savez(tmp, stamp=stamp, t=tr.t, yavg=tr.yavg, yhigh=tr.yhigh)
    tmp.replace(cache_path)
    return tr
