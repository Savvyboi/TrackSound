"""Cases, recording upload, spectrogram tiles and audio playback."""
from __future__ import annotations

import re
import shutil
import time
from datetime import date as date_cls
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, Field, field_validator, model_validator

from .. import audio_io, spectrogram
from ..deps import get_jobs, get_store
from ..jobs import JobContext, JobManager
from ..store import Store

router = APIRouter(prefix="/api", tags=["cases"])

MAX_PLAYBACK_S = 600.0

# Results that belong to one recording (its timeline): cleared when a new file is uploaded.
# Downloaded catalogues (aircraft/flights.json, lightning/strikes.json) depend only on the
# date and region, so they are kept.
RECORDING_DERIVED = (
    "audio.wav", "spectrogram.npz", "tiles",
    "aircraft/events.json", "aircraft/searches",
    "lightning/onsets.json", "lightning/flashes.json", "lightning/solves", "lightning/envelope.npz", "lightning/luma.npz",
    "enf/analysis.json", "enf/matches",
    "alerts/scan.json",
)


class BBox(BaseModel):
    lat_min: float = Field(ge=-90, le=90)
    lat_max: float = Field(ge=-90, le=90)
    lon_min: float = Field(ge=-540, le=540)
    lon_max: float = Field(ge=-540, le=540)

    @model_validator(mode="after")
    def _ordered(self):
        if not (self.lat_min < self.lat_max and self.lon_min < self.lon_max):
            raise ValueError("region must have lat_min < lat_max and lon_min < lon_max")
        return self


# times outside this range are typos (a half-typed year) rather than real recordings
EARLIEST_UTC = -2208988800.0     # 1900-01-01
LATEST_UTC = 4102444800.0        # 2100-01-01


def _clean_name(v):
    if v is None:
        return v
    v = v.strip()
    if not v:
        raise ValueError("the case name can't be empty")
    return v[:200]


class CaseCreate(BaseModel):
    name: str = "Untitled case"

    @field_validator("name")
    @classmethod
    def _name(cls, v):
        return _clean_name(v)


class CasePatch(BaseModel):
    name: str | None = None
    notes: str | None = None
    date: str | None = None
    timezone: str | None = None
    region: BBox | None = None
    countries: list[str] | None = None
    start_utc: float | None = None
    start_utc_source: Literal["user", "aircraft", "lightning", "enf", "alerts"] | None = None
    temperature_c: float | None = Field(default=None, ge=-60, le=60)

    @field_validator("name")
    @classmethod
    def _name(cls, v):
        return _clean_name(v)

    @field_validator("date")
    @classmethod
    def _date(cls, v):
        if v is None:
            return v
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
            raise ValueError("date must be YYYY-MM-DD")
        try:
            d = date_cls.fromisoformat(v)
        except ValueError:
            raise ValueError(f"{v} is not a real calendar date")
        if not 1900 <= d.year < 2100:
            raise ValueError(f"the year {d.year} is outside 1900-2099")
        return v

    @field_validator("start_utc")
    @classmethod
    def _start(cls, v):
        if v is not None and not (EARLIEST_UTC <= v < LATEST_UTC):
            raise ValueError("the start time must fall between 1900 and 2099")
        return v

    @field_validator("countries")
    @classmethod
    def _countries(cls, v):
        if v is None:
            return v
        from .. import countries as C
        return C.validate(v)

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v):
        if v:
            try:
                ZoneInfo(v)
            except (ZoneInfoNotFoundError, ValueError):
                raise ValueError(f"unknown timezone {v!r}")
        return v


@router.get("/health")
def health(store: Store = Depends(get_store)):
    from .. import __version__
    return {"ok": True, "version": __version__, "data_dir": str(store.settings.data_dir)}


@router.get("/cases")
def list_cases(store: Store = Depends(get_store)):
    return store.list_cases()


@router.post("/cases", status_code=201)
def create_case(body: CaseCreate, store: Store = Depends(get_store)):
    return store.create_case(body.name)


@router.get("/cases/{case_id}")
def get_case(case_id: str, store: Store = Depends(get_store)):
    return store.get_case(case_id)


@router.patch("/cases/{case_id}")
def patch_case(case_id: str, body: CasePatch, store: Store = Depends(get_store)):
    store.get_case(case_id)
    fields = body.model_dump(exclude_unset=True)
    if "region" in fields and fields["region"] is not None:
        fields["region"] = body.region.model_dump()
    if "start_utc" in fields and fields["start_utc"] is not None and "start_utc_source" not in fields:
        fields["start_utc_source"] = "user"
    if fields.get("start_utc", 0) is None:
        fields["start_utc_source"] = None
    return store.update_case(case_id, **fields)


@router.delete("/cases/{case_id}", status_code=204)
def delete_case(case_id: str, store: Store = Depends(get_store), jobs: JobManager = Depends(get_jobs)):
    store.get_case(case_id)                                   # 404 before cancelling anything
    active = [j["id"] for j in jobs.list(case_id) if j["status"] in ("queued", "running")]
    for jid in active:
        jobs.cancel(jid)
    # a cancelled job stops at its next checkpoint; its files stay open until then (and Windows
    # won't delete open files), so wait for it, then retry briefly while handles close
    deadline = time.time() + 60
    while time.time() < deadline and any((jobs.get(j) or {}).get("status") in ("queued", "running") for j in active):
        time.sleep(0.2)
    for attempt in range(20):
        try:
            store.delete_case(case_id)
            return Response(status_code=204)
        except PermissionError:
            time.sleep(0.25)
    raise HTTPException(409, "the case's files are still in use by a running job; try again in a moment")


# ---- recording ----------------------------------------------------------------------------

def _process_recording(store: Store, case_id: str, src):
    def work(ctx: JobContext):
        ctx.progress(0.01, "Reading file")
        info = audio_io.probe(src)
        if not info["has_audio"]:
            raise ValueError("the file has no audio stream")
        ctx.progress(0.05, "Decoding audio")
        x, sr = audio_io.decode_to_standard(src, store.audio_path(case_id))
        duration = len(x) / sr
        store.update_recording(case_id, duration_s=duration, has_video=info["has_video"], video_fps=info["video_fps"],
                               source_sample_rate=info["audio_sample_rate"], source_channels=info["audio_channels"])
        spec_info = spectrogram.build(x, sr, store.spectrogram_path(case_id), store.case_dir(case_id) / "tiles",
                                      progress=ctx.sub(0.1, 0.98))
        store.update_recording(case_id, status="ready", error=None, spectrogram=spec_info)
        return {"duration_s": duration, "n_frames": spec_info["n_frames"]}

    def done(job):
        if job["status"] in ("error", "cancelled"):
            store.update_recording(case_id, status="error", error=job["error"] or "Cancelled")
    return work, done


@router.post("/cases/{case_id}/recording")
def upload_recording(case_id: str, file: UploadFile = File(...), store: Store = Depends(get_store),
                     jobs: JobManager = Depends(get_jobs)):
    case = store.get_case(case_id)
    if (case.get("recording") or {}).get("status") == "processing":
        raise HTTPException(409, "a recording is already being processed for this case")
    name = file.filename or "recording"
    ext = re.sub(r"[^a-zA-Z0-9]", "", name.rsplit(".", 1)[-1])[:8] if "." in name else "bin"
    with store.lock(case_id):
        for old in store.case_dir(case_id).glob("original.*"):
            old.unlink()
        for rel in RECORDING_DERIVED:
            store.delete_file(case_id, rel)
        dst = store.case_dir(case_id) / f"original.{ext or 'bin'}"
        with open(dst, "wb") as fh:
            shutil.copyfileobj(file.file, fh, length=4 << 20)
    size = dst.stat().st_size
    if size == 0:
        dst.unlink()
        raise HTTPException(400, "the uploaded file is empty")
    clock = {} if case.get("start_utc_source") in (None, "user") else {"start_utc": None, "start_utc_source": None}
    case = store.update_case(case_id, recording={
        "filename": name, "size_bytes": size, "uploaded_at": time.time(), "status": "processing", "error": None,
        "duration_s": None, "has_video": False, "video_fps": None, "spectrogram": None}, **clock)
    work, done = _process_recording(store, case_id, dst)
    job = jobs.submit("recording.process", case_id, work, on_done=done)
    return {"case": case, "job": job}


@router.get("/cases/{case_id}/spectrogram/tile")
def spectrogram_tile(case_id: str, level: int = Query(0, ge=0, le=30), index: int = Query(0, ge=0),
                     store: Store = Depends(get_store)):
    store.get_case(case_id)
    try:
        tile, frame0 = spectrogram.read_tile(store.case_dir(case_id) / "tiles", level, index)
    except FileNotFoundError:
        raise HTTPException(404, "no spectrogram at this level")
    except IndexError:
        raise HTTPException(404, "tile index out of range")
    headers = {"X-Rows": str(tile.shape[0]), "X-Cols": str(tile.shape[1]), "X-Level": str(level),
               "X-Index": str(index), "X-Frame0": str(frame0), "X-Frames-Per-Col": str(2 ** level),
               "Cache-Control": "private, max-age=3600",
               "Access-Control-Expose-Headers": "X-Rows, X-Cols, X-Level, X-Index, X-Frame0, X-Frames-Per-Col"}
    return Response(tile.tobytes(), media_type="application/octet-stream", headers=headers)


@router.get("/cases/{case_id}/audio")
def audio_snippet(case_id: str, t0: float = Query(..., ge=0), t1: float = Query(...),
                  speed: float = Query(1.0, ge=0.25, le=16.0), store: Store = Depends(get_store)):
    """WAV of [t0, t1] for playback. speed > 1 plays faster and higher, which makes
    low aircraft tones and hum audible (speed=4 moves 100 Hz to 400 Hz)."""
    store.get_case(case_id)
    if t1 <= t0:
        raise HTTPException(400, "t1 must be greater than t0")
    if t1 - t0 > MAX_PLAYBACK_S:
        raise HTTPException(400, f"at most {MAX_PLAYBACK_S:.0f} s per request")
    p = store.audio_path(case_id)
    if not p.exists():
        raise HTTPException(404, "the recording has not been processed yet")
    x, sr = audio_io.read_segment(p, t0, t1)
    return Response(audio_io.wav_bytes(x, int(round(sr * speed))), media_type="audio/wav")
