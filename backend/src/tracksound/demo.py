"""Demo cases with synthetic recordings, so the app can be tried without any downloads.

``tracksound demo`` creates them in the data folder; each case's notes say what the true
answer is, so you can check what the tool finds.
"""
from __future__ import annotations

import logging
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import soundfile as sf

from . import audio_io, spectrogram
from .config import Settings
from .physics import pad_bbox, speed_of_sound
from .store import Store

log = logging.getLogger("tracksound.demo")


def process_recording_now(store: Store, case_id: str, wav: Path, filename: str) -> dict:
    """What the upload job does (api/cases.py), synchronously: copy, decode, spectrogram, tiles."""
    dst = store.case_dir(case_id) / f"original{wav.suffix or '.wav'}"
    dst.write_bytes(wav.read_bytes())
    store.update_case(case_id, recording={"filename": filename, "size_bytes": dst.stat().st_size, "uploaded_at": time.time(),
                                          "status": "processing", "error": None, "duration_s": None, "has_video": False,
                                          "video_fps": None, "spectrogram": None})
    info = audio_io.probe(dst)
    x, sr = audio_io.decode_to_standard(dst, store.audio_path(case_id))
    store.update_recording(case_id, duration_s=len(x) / sr, has_video=info["has_video"], video_fps=info["video_fps"],
                           source_sample_rate=info["audio_sample_rate"], source_channels=info["audio_channels"])
    spec = spectrogram.build(x, sr, store.spectrogram_path(case_id), store.case_dir(case_id) / "tiles")
    return store.update_recording(case_id, status="ready", spectrogram=spec)


def make_aircraft_demo(store: Store) -> dict:
    from .sources.aircraft import flights, synth
    lat0, lon0 = 40.8765, -73.5250
    date = "2026-09-08"
    start = datetime(2026, 9, 8, 18, 0, 0, tzinfo=timezone.utc).timestamp()
    duration = 2700.0
    c = speed_of_sound(15.0)
    traces, dense = synth.make_world(lat0, lon0, start, duration, synth.DEMO_PASSES)
    x = synth.synth_audio(dense, start, duration, sr=8000, c=c)
    case = store.create_case("Demo: aircraft flyovers (synthetic)")
    cid = case["id"]
    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "demo-aircraft.wav"
        sf.write(str(wav), x, 8000, subtype="PCM_16")
        process_recording_now(store, cid, wav, "demo-aircraft.wav")
    region = {"lat_min": lat0 - 0.13, "lat_max": lat0 + 0.19, "lon_min": lon0 - 0.24, "lon_max": lon0 + 0.18}
    flights.attach_traces(store, cid, date, pad_bbox(region, 15_000.0), traces, {"synthetic": True})
    notes = ("Synthetic demo. Four prop aircraft fly past a listener; 24 more fly elsewhere in the region. "
             f"True location: {lat0:.5f}, {lon0:.5f}. True start: 2026-09-08 18:00:00 UTC. "
             "Flights are already loaded (no download needed). Try: Aircraft → Detect flyovers, accept two strong events, "
             "then run a blind search.")
    return store.update_case(cid, date=date, region=region, timezone="America/New_York", notes=notes)


def make_alerts_demo(store: Store) -> dict:
    """A radio recording with a NOAA Weather Radio Required Weekly Test for Jackson County,
    Missouri, issued 2026-07-15 17:40 UTC and heard 150 s into the recording."""
    from .sources.alerts import synth as asynth
    sr = 22050
    header = "ZCZC-WXR-RWT-029095+0030-1961740-KEAX/NWS-"
    alert, _ = asynth.alert_waveform(header, sr=sr, attention="nws", attention_s=8.0, seed=4)
    rng = np.random.default_rng(9)
    bed = asynth.music_like(360.0, sr, seed=2) * 0.6 + 0.02 * rng.standard_normal(int(360.0 * sr)).astype(np.float32)
    x = asynth.mix(alert * 0.35, bed, at_s=150.0, sr=sr)
    case = store.create_case("Demo: weather-radio alert (synthetic)")
    cid = case["id"]
    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "demo-weather-radio.wav"
        sf.write(str(wav), x, sr, subtype="PCM_16")
        process_recording_now(store, cid, wav, "demo-weather-radio.wav")
    notes = ("Synthetic demo. Music with a NOAA Weather Radio Required Weekly Test (SAME header "
             f"{header}) 150 s in. Truth: Jackson County, Missouri; issued 2026-07-15 17:40 UTC, so the "
             "recording started at 17:37:30 UTC. Try: Alerts → Scan.")
    return store.update_case(cid, date="2026-07-15", timezone="America/Chicago", notes=notes)


def _try(name: str, fn, store: Store):
    try:
        return fn(store)
    except Exception:  # noqa: BLE001 - one broken demo should not stop the others
        log.exception("could not build the %s demo", name)
        return None


def make_demo_cases(settings: Settings, kind: str = "all") -> list[dict]:
    store = Store(settings)
    out = []
    if kind in ("aircraft", "all"):
        out.append(_try("aircraft", make_aircraft_demo, store))
    if kind in ("lightning", "all"):
        try:
            from .sources.lightning.demo import make_demo_case as lightning_demo
        except ImportError:
            lightning_demo = None
        if lightning_demo:
            out.append(_try("lightning", lightning_demo, store))
    if kind in ("alerts", "all"):
        out.append(_try("alerts", make_alerts_demo, store))
    if kind in ("enf", "all"):
        try:
            from .sources.enf.demo import make_demo_case as enf_demo
        except ImportError:
            enf_demo = None
        if enf_demo:
            out.append(_try("ENF", enf_demo, store))
    return [c for c in out if c]
