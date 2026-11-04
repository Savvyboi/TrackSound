"""Scan a whole recording for SAME bursts, block by block, and decode the headers.

The original upload is decoded by ffmpeg to mono float32 at ``same.FS`` (11025 Hz: the 4 kHz
standard copy cannot carry the 2083 Hz mark tone) and read from a pipe in 30 s blocks, so a
multi-hour file needs only a few MB. Each block is extended by 0.5 s before and 6 s after
(longer than the longest burst, 4.1 s) and only owns the candidates that start in its own 30 s,
so every burst is decoded exactly once whole.
"""
from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator

import numpy as np

from . import attention, header, same

STEP_S = 30.0
PRE_S = 0.5
POST_S = 6.0
GROUP_GAP_S = 6.0          # start-to-start spacing of repetitions: <= 4.1 s burst + 1 s pause
EOM_GAP_S = 2.5
MIN_REPORT_P = 0.2         # below this posterior a header is only mentioned in the notes

Progress = Callable[[float, str], None]


@dataclass
class ScanResult:
    headers: list[dict] = field(default_factory=list)      # decoded headers (see _header_record)
    eoms: list[dict] = field(default_factory=list)         # {t, bursts}
    attention: list[dict] = field(default_factory=list)    # {kind, t, duration_s}
    undecoded: list[dict] = field(default_factory=list)    # {t, bursts, best, p}
    duration_s: float = 0.0
    n_bursts: int = 0


def ffmpeg_reader(path: str | Path, fs: int = same.FS):
    """Start ffmpeg decoding ``path`` to mono float32 at ``fs``.

    Returns (process, read(n) -> up to n samples, stderr file)."""
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-i", str(path), "-vn", "-ac", "1", "-ar", str(fs),
           "-f", "f32le", "-acodec", "pcm_f32le", "-"]
    # stderr goes to a file: a full stderr pipe would stall ffmpeg while we wait on stdout
    errfile = tempfile.TemporaryFile()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=errfile)

    def read(n: int) -> np.ndarray:
        want, chunks = n * 4, []
        while want > 0:
            b = proc.stdout.read(want)
            if not b:
                break
            chunks.append(b)
            want -= len(b)
        data = b"".join(chunks)
        return np.frombuffer(data[: len(data) // 4 * 4], dtype=np.float32)
    return proc, read, errfile


def blocks(read: Callable[[int], np.ndarray], fs: int) -> Iterator[tuple[int, np.ndarray, int, int]]:
    """Yield (block start sample, block, own_lo, own_hi): the block owns [own_lo, own_hi)."""
    step, pre, post = int(STEP_S * fs), int(PRE_S * fs), int(POST_S * fs)
    buf = read(step + post)
    start = 0                                          # recording sample of buf[0]
    own = 0                                            # recording sample where this block's ownership starts
    while len(buf) > own - start:
        lo = own - start
        hi = min(lo + step, len(buf))
        yield start, buf, lo, hi
        if len(buf) - lo <= step:                      # nothing beyond this block
            return
        more = read(step)
        keep_from = max(0, hi - pre)
        buf = np.concatenate((buf[keep_from:], more))
        start += keep_from
        own += step


def group_headers(bursts: list[same.Burst]) -> list[list[same.Burst]]:
    """Up to three header bursts per group, each starting within GROUP_GAP_S of the previous."""
    groups: list[list[same.Burst]] = []
    for b in sorted((b for b in bursts if b.kind == "header"), key=lambda b: b.t):
        if groups and len(groups[-1]) < 3 and b.t - groups[-1][-1].t <= GROUP_GAP_S:
            groups[-1].append(b)
        else:
            groups.append([b])
    return groups


def group_eoms(bursts: list[same.Burst]) -> list[dict]:
    out: list[dict] = []
    for b in sorted((b for b in bursts if b.kind == "eom"), key=lambda b: b.t):
        if b.marker_score < 0.45:                      # an EOM has no grammar to check: be stricter
            continue
        if out and out[-1]["bursts"] < 3 and b.t - out[-1]["_last"] <= EOM_GAP_S:
            out[-1]["bursts"] += 1
            out[-1]["_last"] = b.t
        else:
            out.append({"t": round(b.t, 3), "bursts": 1, "_last": b.t})
    return [{"t": e["t"], "bursts": e["bursts"]} for e in out]


def _attention_after(group: list[same.Burst], segs: list[dict]) -> dict | None:
    """The attention signal that starts within 6 s after the last burst of a header group."""
    last = group[-1]
    end = last.t_payload + max(last.n_bytes, 40) * 8 * last.period
    for s in segs:
        if end - 1.0 <= s["t"] <= end + 6.0:
            return s
    return None


def _header_record(group: list[same.Burst], comb: header.Combined, att: dict | None) -> dict:
    return {
        "t": round(group[0].t, 3), "t_bursts": [round(b.t, 3) for b in group], "repeats": len(group),
        "raw": comb.text, "fields": comb.fields, "p_correct": round(comb.p_correct, 4), "n_agree": comb.n_agree,
        "method": comb.method, "snapped": comb.snapped, "quality": round(comb.quality, 3),
        "burst_texts": comb.burst_texts, "attention": att,
        "period_ms": round(float(np.mean([b.period for b in group])) * 1e3, 5),
    }


def decode_bursts(bursts: list[same.Burst], segs: list[dict]) -> tuple[list[dict], list[dict]]:
    """(decoded header records, undecoded groups) from all bursts of a recording."""
    headers, undecoded = [], []
    for g in group_headers(bursts):
        comb = header.combine([b.soft for b in g], [b.weight for b in g])
        if comb is not None and comb.p_correct >= MIN_REPORT_P:
            headers.append(_header_record(g, comb, _attention_after(g, segs)))
        else:
            undecoded.append({"t": round(g[0].t, 3), "bursts": len(g), "best": comb.text if comb else None,
                              "p": round(comb.p_correct, 4) if comb else 0.0})
    return headers, undecoded


def scan_stream(read: Callable[[int], np.ndarray], fs: int = same.FS, duration_s: float | None = None,
                progress: Progress | None = None) -> ScanResult:
    """Scan audio delivered by ``read(n)`` (mono float32 at ``fs``)."""
    bursts: list[same.Burst] = []
    prom: list[np.ndarray] = []
    total = 0
    for start, buf, lo, hi in blocks(read, fs):
        # owning 0.3 s before the block too means a burst on a boundary cannot fall between blocks
        found = same.find_bursts(buf, fs, t_offset=start / fs, own=(max(0, lo / fs - 0.3), hi / fs))
        bursts.extend(b for b in found if not any(abs(b.t_payload - o.t_payload) < 0.05 for o in bursts))
        prom.append(attention.block_prominence(buf, fs, lo, hi))
        total = start + hi
        if progress:
            frac = min(0.99, total / fs / duration_s) if duration_s else 0.5
            progress(frac, f"Scanned {total / fs / 60:.1f} min, {len(bursts)} burst{'s' if len(bursts) != 1 else ''}")
    segs = attention.segments(np.concatenate(prom) if prom else np.zeros((0, 3)), fs)
    headers, undecoded = decode_bursts(bursts, segs)
    return ScanResult(headers=headers, eoms=group_eoms(bursts), attention=segs, undecoded=undecoded,
                      duration_s=total / fs, n_bursts=len(bursts))


def scan_array(x: np.ndarray, fs: int = same.FS) -> ScanResult:
    """Scan samples already in memory (mono, at ``fs``)."""
    x = np.asarray(x, dtype=np.float32)
    pos = 0

    def read(n: int) -> np.ndarray:
        nonlocal pos
        out = x[pos:pos + n]
        pos += len(out)
        return out
    return scan_stream(read, fs)


def scan_file(path: str | Path, duration_s: float | None = None, progress: Progress | None = None) -> ScanResult:
    """Scan any file ffmpeg can read, streaming it at ``same.FS``."""
    proc, read, errfile = ffmpeg_reader(path)
    with errfile:
        try:
            result = scan_stream(read, same.FS, duration_s, progress)
        finally:
            proc.stdout.close()
            code = proc.wait()
        if code != 0 and result.duration_s == 0:
            errfile.seek(0)
            err = errfile.read().decode(errors="replace").strip()
            raise ValueError(f"ffmpeg could not decode the audio: {err[:300]}")
    return result
