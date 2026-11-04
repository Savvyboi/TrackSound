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
from dopplertrack.audio import Spectrogram, compute_spectrogram

# display quantisation: byte 0 -> DB_MIN, 255 -> DB_MAX (whitened dB above the per-bin median)
DB_MIN = -10.0
DB_MAX = 30.0
TILE_FRAMES = 1024

# 10 Hz floor: helicopter main rotors have blade-pass tones at about 10-40 Hz (props are 60-400 Hz)
SPEC_PARAMS = dict(nperseg=8192, hop_s=0.25, fmin=10.0, fmax=450.0, whiten_window_s=600.0)


def build(x: np.ndarray, sr: int, spec_path: Path, tiles_dir: Path, progress=None) -> dict:
    """Spectrogram + display pyramid. Returns the SpectrogramInfo document."""
    if len(x) < SPEC_PARAMS["nperseg"] + sr:
        raise ValueError("recording is too short (need at least a few seconds)")
    if progress:
        progress(0.05, "Computing spectrogram")
    sp = compute_spectrogram(x, sr, **SPEC_PARAMS)
    sp.save(spec_path)
    if progress:
        progress(0.7, "Building display tiles")
    info = build_pyramid(sp, tiles_dir)
    _CACHE.put(spec_path, sp)
    return info


def quantise(S: np.ndarray) -> np.ndarray:
    q = (np.asarray(S, np.float32) - DB_MIN) * (255.0 / (DB_MAX - DB_MIN))
    return np.clip(np.nan_to_num(q, nan=0.0), 0, 255).astype(np.uint8)


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
