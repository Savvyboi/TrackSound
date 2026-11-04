"""A ready-to-use lightning demo case (no downloads needed).

A thunderstorm drifts past a listener in central Florida. The case gets a synthetic
15-minute recording (rain, wind, thunder from the audible strikes, one thunder burst with no
catalogued strike, a passing truck, two door slams), processed through the normal recording
pipeline, and an imported strike catalogue (40 strikes over 20 minutes, ~300 m accuracy,
like a ground network). The recording's start time is left unknown, so a solve runs in
``free`` mode and recovers both the place and the clock. The true answer is in the notes.
"""
from __future__ import annotations

import datetime as dt
import math
import time

import soundfile as sf

from ... import audio_io, spectrogram
from ...physics import speed_of_sound
from ...store import Store
from . import catalogue, state, synth
from .geo import EARTH_R, square_bbox

TRUE_LAT = 28.0395
TRUE_LON = -81.9498
TRUE_START_UTC = 1752949200.0          # 2025-07-19 18:20:00 UTC
DURATION_S = 900.0
TEMPERATURE_C = 28.0
SOURCE_SR = 8000
FILENAME = "demo_thunderstorm.flac"


def make_demo_case(store: Store, name: str = "Demo: thunderstorm (lightning)") -> dict:
    """Create the demo case and return its Case document."""
    case = store.create_case(name)
    cid = case["id"]
    c = speed_of_sound(TEMPERATURE_C)
    storm = synth.make_storm(seed=11, lat=TRUE_LAT, lon=TRUE_LON, start_utc=TRUE_START_UTC, duration_s=DURATION_S,
                             n_strikes=40, lead_s=240.0, tail_s=60.0, sigma_m=300.0, c=c, kind="cg")
    x = synth.render_audio(storm, sr=SOURCE_SR, seed=12, n_false_thunder=1, n_trucks=1, n_slams=2)

    # the "upload", then the normal recording pipeline (as api/cases.py does it)
    orig = store.case_dir(cid) / ("original." + FILENAME.rsplit(".", 1)[1])
    sf.write(str(orig), x, SOURCE_SR, subtype="PCM_16")
    store.update_recording(cid, filename=FILENAME, size_bytes=orig.stat().st_size, uploaded_at=time.time(),
                           status="processing", error=None, duration_s=None, has_video=False, video_fps=None,
                           spectrogram=None)
    info = audio_io.probe(orig)
    x4, sr4 = audio_io.decode_to_standard(orig, store.audio_path(cid))
    spec = spectrogram.build(x4, sr4, store.spectrogram_path(cid), store.case_dir(cid) / "tiles")
    store.update_recording(cid, status="ready", error=None, duration_s=len(x4) / sr4, has_video=info["has_video"],
                           video_fps=info["video_fps"], source_sample_rate=info["audio_sample_rate"],
                           source_channels=info["audio_channels"], spectrogram=spec)

    strikes = storm.catalogue("csv")
    with store.lock(cid):
        state.write_strikes(store, cid, catalogue.strike_set("csv", strikes), strikes)

    # search region: 50 km square whose centre is ~11 km from the true position
    clat = TRUE_LAT + math.degrees(6000.0 / EARTH_R)
    clon = TRUE_LON - math.degrees(9000.0 / (EARTH_R * math.cos(math.radians(TRUE_LAT))))
    region = {k: round(v, 5) for k, v in square_bbox(clat, clon, 25000.0).items()}
    start_iso = dt.datetime.fromtimestamp(TRUE_START_UTC, dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    n_heard = len(storm.onset_times())
    notes = (
        "Synthetic demo for the lightning source (no downloads needed).\n"
        "A thunderstorm drifts past the listener. The 15-minute recording has rain, wind and the thunder of "
        f"{n_heard} strikes, plus one thunder burst with no catalogued strike, a passing truck and two door slams. "
        "Its start time is unknown. A strike catalogue like a ground network's (40 strikes over 20 minutes, "
        "about 300 m accuracy) is already imported.\n"
        "Try: Lightning -> Detect thunder, review the onsets, then Solve (it runs in free mode and finds the clock too).\n"
        f"True answer: {TRUE_LAT:.4f} N, {abs(TRUE_LON):.4f} W; recording start {start_iso} "
        f"(unix {TRUE_START_UTC:.0f}); air temperature {TEMPERATURE_C:g} C."
    )
    return store.update_case(cid, notes=notes, date="2025-07-19", timezone="America/New_York", region=region,
                             temperature_c=TEMPERATURE_C, start_utc=None, start_utc_source=None)
