"""A ready-to-use demo case for the ENF source.

The recording is synthetic: room noise, low rumble, a few soft transients and a faint mains hum
(mostly at 100 and 150 Hz) whose frequency follows real Great Britain grid data. Six hours of
NESO's 1 s data (2026-09-14, 09:00-15:00 UTC; NESO Open Data Licence) are bundled in
``tracksound/data/enf_demo_gb_2026-09-14.npz`` and registered as an imported reference, so the
demo matches offline; with network access the built-in "Great Britain (NESO, 1 s)" reference
finds the same time. If the bundled slice is missing, a synthetic random-walk ENF is used and
registered instead.

The true start time is written in the case notes; ``start_utc`` and ``date`` are left unknown.
"""
from __future__ import annotations

import datetime as dt
import json
import time
from pathlib import Path

import numpy as np
import soundfile as sf

from ... import audio_io, spectrogram
from ...store import Store
from . import synth
from .references import imported

DATA = Path(__file__).resolve().parents[2] / "data" / "enf_demo_gb_2026-09-14.npz"
DEMO_SR = 8000
SYNTH_T0 = dt.datetime(2026, 1, 15, 8, 0, tzinfo=dt.timezone.utc).timestamp()


def demo_enf() -> tuple[np.ndarray, float, dict]:
    """(1 s frequency series, UTC of its first sample, metadata): the bundled real slice if present."""
    if DATA.exists():
        with np.load(DATA) as z:
            dev = z["dev_mhz"].astype(float)
            meta = json.loads(str(z["meta"]))
        return 50.0 + dev / 1000.0, float(meta["t0_utc"]), meta
    f = synth.random_walk_enf(6 * 3600, 50.0, seed=2026, std_hz=0.06, tau_s=650)
    return f, SYNTH_T0, {"grid": "Synthetic demo grid", "nominal_hz": 50, "t0_utc": SYNTH_T0,
                         "source": "synthetic random walk (TrackSound demo)", "licence": None, "url": None}


def _ensure_reference(store: Store, f: np.ndarray, t0: float, meta: dict) -> dict:
    """The demo reference as an imported reference, created once."""
    day = dt.datetime.fromtimestamp(t0, dt.timezone.utc)
    end = dt.datetime.fromtimestamp(t0 + len(f), dt.timezone.utc)
    name = f"Demo: {meta['grid']} {day:%Y-%m-%d %H:%M}-{end:%H:%M} UTC"
    for m in imported.list_imported(store.settings.data_dir):
        if m.get("name") == name:
            return m
    lines = ["time,frequency"] + [f"{int(t0) + i},{v:.3f}" for i, v in enumerate(f) if np.isfinite(v)]
    meta_doc = imported.import_csv(store.settings.data_dir, "\n".join(lines).encode(), name, meta["grid"], 50)
    # keep the provenance of the bundled data with the reference
    p = store.settings.data_dir / "enf" / "references" / f"{meta_doc['id']}.json"
    meta_doc.update(licence=meta.get("licence"), url=meta.get("url"), demo=True)
    p.write_text(json.dumps(meta_doc, indent=1))
    return meta_doc


def demo_audio(f: np.ndarray, offset_s: float, duration_s: float, sr: int = DEMO_SR, seed: int = 7,
               hum: float = 0.06, clock_ppm: float = 12.0) -> np.ndarray:
    """Room tone with a faint hum following ``f`` from ``offset_s`` on (float32, peak ~0.5)."""
    rng = np.random.default_rng(seed)
    n = int(round(duration_s * sr))
    y = hum * synth.hum_signal(f, sr, duration_s, offset_s=offset_s, seed=seed, clock_ppm=clock_ppm,
                               amplitudes={1: 0.25, 2: 1.0, 3: 0.7, 4: 0.3, 5: 0.15})
    y += synth.room_noise(n, sr, seed=seed, white=0.8, rumble=1.0)
    # a few soft transients (doors, footsteps): decaying noise bursts
    for _ in range(int(duration_s / 90)):
        a = int(rng.uniform(0, n - sr))
        m = int(sr * rng.uniform(0.2, 1.5))
        y[a:a + m] += rng.standard_normal(m) * rng.uniform(1.0, 3.0) * np.exp(-np.arange(m) / (0.25 * sr))
    return (0.5 * y / np.max(np.abs(y))).astype(np.float32)


def make_demo_case(store: Store, duration_s: float = 1200.0, seed: int = 7) -> dict:
    """Create the ENF demo case (recording processed and ready) and return its Case document."""
    f, t0, meta = demo_enf()
    ref = _ensure_reference(store, f, t0, meta)
    rng = np.random.default_rng(seed)
    offset = float(int(rng.uniform(1800, len(f) - duration_s - 1800)))
    true_start = t0 + offset
    when = dt.datetime.fromtimestamp(true_start, dt.timezone.utc)
    y = demo_audio(f, offset, duration_s, seed=seed)

    case = store.create_case("Demo: mains hum (ENF)")
    cid = case["id"]
    src = store.case_dir(cid) / "original.wav"
    sf.write(str(src), y, DEMO_SR, subtype="PCM_16")
    notes = (f"Synthetic demo recording: room noise with a faint mains hum (mostly 100 and 150 Hz) whose frequency "
             f"follows {meta['grid']} grid data ({meta['source']}). The recorder clock runs 12 ppm fast.\n"
             f"True start: {when:%Y-%m-%d %H:%M:%S} UTC (unix {true_start:.0f}).\n"
             f"Try: ENF > Extract, then Match against '{ref['name']}' (works offline)"
             + (", or against 'Great Britain (NESO, 1 s)' for that day (downloads)." if DATA.exists() else "."))
    store.update_case(cid, notes=notes, recording={
        "filename": "enf-demo.wav", "size_bytes": src.stat().st_size, "uploaded_at": time.time(),
        "status": "processing", "error": None, "duration_s": None, "has_video": False, "video_fps": None,
        "spectrogram": None})
    try:
        x, sr = audio_io.decode_to_standard(src, store.audio_path(cid))
        store.update_recording(cid, duration_s=len(x) / sr, has_video=False, video_fps=None,
                               source_sample_rate=DEMO_SR, source_channels=1)
        info = spectrogram.build(x, sr, store.spectrogram_path(cid), store.case_dir(cid) / "tiles")
        store.update_recording(cid, status="ready", error=None, spectrogram=info)
    except Exception as e:
        store.update_recording(cid, status="error", error=f"{type(e).__name__}: {e}")
        raise
    out = store.get_case(cid)
    out["_demo"] = {"true_start_utc": true_start, "reference_id": ref["id"],
                    "reference_t0_utc": t0, "reference_t1_utc": t0 + len(f)}
    return out
