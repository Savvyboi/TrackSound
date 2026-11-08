"""The case spectrogram (10-450 Hz): built once per upload, cached in memory, served to the browser as tiles.

Scoring always uses the float32 whitened spectrogram (``spectrogram.npz``, dopplertrack's
format). The browser gets a uint8 display pyramid: level 0 has one column per STFT frame,
level z max-pools 2**z frames per column so faint lines survive zooming out.
"""
from __future__ import annotations

import threading
from collections import OrderedDict
from pathlib import Path

import numpy as np
import scipy.signal as ss
from dopplertrack.audio import Spectrogram

# display quantisation: byte 0 -> DB_MIN, 255 -> DB_MAX (whitened dB above the per-bin median)
DB_MIN = -10.0
DB_MAX = 30.0
TILE_FRAMES = 1024

# 10 Hz floor: helicopter main rotors have blade-pass tones at about 10-40 Hz (props are 60-400 Hz)
SPEC_PARAMS = dict(nperseg=8192, hop_s=0.25, fmin=10.0, fmax=450.0, whiten_window_s=600.0)


def compute_spectrogram(x: np.ndarray, sr: int, nperseg: int = 8192, hop_s: float = 0.25, fmin: float = 40.0,
                        fmax: float = 450.0, whiten_window_s: float | None = 600.0,
                        chunk_frames: int = 2048) -> Spectrogram:
    """dopplertrack.audio.compute_spectrogram with the same output, in far less memory.

    The library keeps several full-size copies at once (a float64 one among them), so a 6 h
    recording peaked near 2.7 GB for a 0.3 GB result. Here the dB spectrogram is written into
    one float32 array and whitened in place, a frequency row at a time, with the same maths
    (per-block medians, blended linearly between block centres, subtracted in float64). Frames are
    computed in chunks of 2048 (each frame is independent): a chunk's complex STFT is ~130 MB."""
    hop = max(1, int(round(hop_s * sr)))
    K = (len(x) - nperseg) // hop + 1
    f = np.fft.rfftfreq(nperseg, 1 / sr)
    band = (f >= fmin) & (f <= fmax)
    f = f[band]
    L = np.empty((len(f), K), np.float32)
    for k0 in range(0, K, chunk_frames):
        k1 = min(k0 + chunk_frames, K)
        seg = x[k0 * hop:(k1 - 1) * hop + nperseg]
        _, _, Z = ss.stft(seg, sr, nperseg=nperseg, noverlap=nperseg - hop, window="hann", boundary=None, padded=False)
        L[:, k0:k1] = (10 * np.log10(np.abs(Z[band]) ** 2 + 1e-20)).astype(np.float32)
        del Z
    t = (nperseg / 2 + np.arange(K) * hop) / sr
    w = None if whiten_window_s is None else int(whiten_window_s / (hop / sr))
    if w is None or w >= K:
        L -= np.median(L, axis=1, keepdims=True)
    else:
        nb = int(np.ceil(K / w))
        bounds = [(b * w, min((b + 1) * w, K)) for b in range(nb)]
        centers = [(a + e) / 2 for a, e in bounds]
        cm = np.array([np.median(L[:, a:e], axis=1) for a, e in bounds])     # (nb, F)
        idx = np.arange(K)
        for k in range(L.shape[0]):
            L[k] = (L[k] - np.interp(idx, centers, cm[:, k])).astype(np.float32)
    return Spectrogram(t, f, L)


def build(x: np.ndarray, sr: int, spec_path: Path, tiles_dir: Path, progress=None) -> dict:
    """Spectrogram + display pyramid. Returns the SpectrogramInfo document."""
    if len(x) < SPEC_PARAMS["nperseg"] + sr:
        need = np.ceil((SPEC_PARAMS["nperseg"] + sr) / sr * 10) / 10   # rounded up, so "3.0 s < 3.0 s" can't happen
        raise ValueError(f"the recording is too short: it is {len(x) / sr:.1f} s, and TrackSound needs at least {need:.1f} s")
    if progress:
        progress(0.05, "Computing spectrogram")
    sp = compute_spectrogram(x, sr, **SPEC_PARAMS)
    # Spectrogram.save's format, without its extra full-size astype copy (S is float32 already)
    np.savez_compressed(spec_path, t=sp.t, f=sp.f, S=sp.S)
    if progress:
        progress(0.7, "Building display tiles")
    info = build_pyramid(sp, tiles_dir)
    _CACHE.put(spec_path, sp)
    return info


def quantise(S: np.ndarray) -> np.ndarray:
    out = np.empty(S.shape, np.uint8)
    scale = np.float32(255.0 / (DB_MAX - DB_MIN))
    step = max(1, (1 << 24) // max(1, S.shape[1]))         # ~64 MB of float32 at a time
    for r0 in range(0, S.shape[0], step):
        q = np.subtract(S[r0:r0 + step], DB_MIN, dtype=np.float32)
        q *= scale
        np.nan_to_num(q, copy=False, nan=0.0)
        np.clip(q, 0, 255, out=q)
        out[r0:r0 + step] = q
    return out


def build_pyramid(sp: Spectrogram, tiles_dir: Path) -> dict:
    tiles_dir.mkdir(parents=True, exist_ok=True)
    for old in tiles_dir.glob("level_*.npy"):
        old.unlink()
    level = quantise(sp.S)                                    # (F, T)
    z = 0
    while True:
        np.save(tiles_dir / f"level_{z}.npy", np.ascontiguousarray(level))
        if level.shape[1] <= TILE_FRAMES:
            break
        T = level.shape[1]
        if T % 2:
            level = np.concatenate([level, level[:, -1:]], axis=1)
        level = np.maximum(level[:, 0::2], level[:, 1::2])
        z += 1
    return {
        "n_frames": int(sp.S.shape[1]), "n_bins": int(sp.S.shape[0]),
        "t0": float(sp.t[0]), "dt": float(sp.dt), "f0": float(sp.f[0]), "df": float(sp.df),
        "tile_frames": TILE_FRAMES, "levels": z + 1, "db_min": DB_MIN, "db_max": DB_MAX,
    }


def read_tile(tiles_dir: Path, level: int, index: int) -> tuple[np.ndarray, int]:
    """(tile uint8 (F, cols), first level-0 frame of the tile)."""
    p = tiles_dir / f"level_{level}.npy"
    if not p.exists():
        raise FileNotFoundError(p)
    arr = np.load(p, mmap_mode="r")
    a = index * TILE_FRAMES
    if index < 0 or a >= arr.shape[1]:
        raise IndexError(index)
    tile = np.ascontiguousarray(arr[:, a:a + TILE_FRAMES])
    return tile, a * (2 ** level)


class _SpecCache:
    """Keeps the last couple of loaded spectrograms in memory (they can be hundreds of MB)."""

    def __init__(self, size: int = 2):
        self.size = size
        self._d: OrderedDict[str, tuple[float, Spectrogram]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, path: Path) -> Spectrogram:
        key = str(path)
        mtime = path.stat().st_mtime
        with self._lock:
            hit = self._d.get(key)
            if hit and hit[0] == mtime:
                self._d.move_to_end(key)
                return hit[1]
        sp = Spectrogram.load(path)
        self.put(path, sp)
        return sp

    def put(self, path: Path, sp: Spectrogram) -> None:
        key = str(path)
        with self._lock:
            self._d[key] = (path.stat().st_mtime, sp)
            self._d.move_to_end(key)
            while len(self._d) > self.size:
                self._d.popitem(last=False)


_CACHE = _SpecCache()


def load(spec_path: Path) -> Spectrogram:
    """The case spectrogram (cached)."""
    if not spec_path.exists():
        raise FileNotFoundError("the recording has no spectrogram yet")
    return _CACHE.get(spec_path)
