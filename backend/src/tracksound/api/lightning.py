"""Lightning source API (docs/API.md): onsets of thunder or other bangs, and video flashes, matched to
catalogues of known-time events (GLM lightning, USGS blasts/booms/earthquakes, CSV, manual events)."""
from __future__ import annotations

import time
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, Field

from ..deps import get_jobs, get_store
from ..jobs import JobContext, JobManager
from ..physics import case_sound_speed, pad_bbox
from ..sources.lightning import catalogue, flashes as flash_mod, glm, solve as solver, state, thunder, usgs
from ..sources.lightning.events import canonical_kind
from ..store import Store, new_id
from .cases import BBox

router = APIRouter(prefix="/api", tags=["lightning"])

MAX_FETCH_S = 6 * 3600.0                 # GLM: 20 s files, ~60 MB per hour
MAX_FETCH_USGS_S = 31 * 86400.0          # USGS events are sparse
MANUAL_SIGMA_M = 250.0
FETCH_PAD_M = 50_000.0
MAX_CSV_BYTES = 200 * 1024 * 1024
Status = Literal["candidate", "accepted", "rejected"]


class DetectThunderBody(BaseModel):
    sensitivity: float = Field(0.5, ge=0.0, le=1.0)
    min_gap_s: float = Field(4.0, ge=0.5, le=600.0)


class DetectFlashesBody(BaseModel):
    sensitivity: float = Field(0.5, ge=0.0, le=1.0)


class OnsetCreate(BaseModel):
    t: float = Field(ge=0.0)
    sigma_s: float | None = Field(None, gt=0.0, le=60.0)


class OnsetPatch(BaseModel):
    t: float | None = Field(None, ge=0.0)
    sigma_s: float | None = Field(None, gt=0.0, le=60.0)
    status: Status | None = None


class FlashCreate(BaseModel):
    t: float = Field(ge=0.0)


class FlashPatch(BaseModel):
    t: float | None = Field(None, ge=0.0)
    status: Status | None = None


class StrikeFetch(BaseModel):
    source: Literal["glm", "usgs"] = "glm"
    satellite: str = "auto"                       # glm only
    event_types: list[str] | None = None          # usgs only; default blasts, explosions, sonic booms
    t0_utc: float
    t1_utc: float
    bbox: BBox | None = None


class StrikeCreate(BaseModel):
    t_utc: float
    lat: float = Field(ge=-90.0, le=90.0)
    lon: float = Field(ge=-180.0, le=180.0)
    sigma_m: float | None = Field(None, gt=0.0, le=200_000.0)
    kind: str | None = Field(None, max_length=32)


class SolveBody(BaseModel):
    start_utc: float | None = None
    start_tol_s: float = Field(5.0, ge=0.0, le=6 * 3600.0)
    use_flashes: bool = True
    spacing_m: float = Field(250.0, ge=10.0, le=20_000.0)
    max_range_km: float = Field(25.0, ge=1.0, le=100.0)


# ---- helpers --------------------------------------------------------------------------------

def _recording(store: Store, cid: str) -> tuple[dict, dict]:
    case = store.get_case(cid)
    rec = case.get("recording")
    if not rec:
        raise HTTPException(400, "the case has no recording yet")
    if rec.get("status") == "processing":
        raise HTTPException(409, "the recording is still being processed")
    if rec.get("status") != "ready" or not store.audio_path(cid).exists():
        raise HTTPException(400, "the recording could not be processed")
    return case, rec


def _no_running(jobs: JobManager, cid: str, kind: str) -> None:
    if jobs.active(cid, kind):
        raise HTTPException(409, f"a {kind} job is already running for this case")


def _duration(store: Store, cid: str, rec: dict) -> float:
    if rec.get("duration_s"):
        return float(rec["duration_s"])
    import soundfile as sf
    info = sf.info(str(store.audio_path(cid)))
    return info.frames / info.samplerate


# ---- overview and envelope ------------------------------------------------------------------

@router.get("/cases/{case_id}/lightning")
def lightning_state(case_id: str, store: Store = Depends(get_store)):
    store.get_case(case_id)
    return state.overview(store, case_id)


@router.get("/cases/{case_id}/lightning/envelope")
def envelope(case_id: str, t0: float | None = Query(None, ge=0.0), t1: float | None = Query(None),
             max_points: int = Query(2000, ge=10, le=20000), store: Store = Depends(get_store)):
    case = store.get_case(case_id)
    if (case.get("recording") or {}).get("status") != "ready" or not store.audio_path(case_id).exists():
        raise HTTPException(404, "the recording has not been processed yet")
    if t0 is not None and t1 is not None and t1 <= t0:
        raise HTTPException(400, "t1 must be greater than t0")
    env = thunder.load_or_build_envelope(store.audio_path(case_id), store.path(case_id, state.ENVELOPE))
    return thunder.pooled_envelope(env, t0, t1, max_points)


# ---- thunder onsets -------------------------------------------------------------------------

@router.post("/cases/{case_id}/lightning/detect-thunder")
def detect_thunder(case_id: str, body: DetectThunderBody | None = None, store: Store = Depends(get_store),
                   jobs: JobManager = Depends(get_jobs)):
    body = body or DetectThunderBody()
    _recording(store, case_id)
    _no_running(jobs, case_id, "lightning.detect_thunder")
    audio = store.audio_path(case_id)

    def work(ctx: JobContext):
        ctx.progress(0.01, "Computing thunder band envelopes")
        env = thunder.load_or_build_envelope(audio, store.path(case_id, state.ENVELOPE), progress=ctx.sub(0.01, 0.5))
        ctx.progress(0.5, "Finding thunder onsets")
        found = thunder.detect_onsets(env, audio, body.sensitivity, body.min_gap_s, progress=ctx.sub(0.5, 0.97))
        new = [state.new_onset(o["t"], o["sigma_s"], o["strength"], o["duration_s"]) for o in found]
        with store.lock(case_id):
            merged, added = state.replace_detected(state.read_onsets(store, case_id), new, "detected",
                                                   tol_s=min(1.0, body.min_gap_s / 2))
            state.write_onsets(store, case_id, merged)
        store.touch(case_id)
        ctx.progress(1.0, f"Found {added} thunder onset{'s' if added != 1 else ''}")
        return {"count": added}

    return jobs.submit("lightning.detect_thunder", case_id, work)


@router.post("/cases/{case_id}/lightning/onsets")
def add_onset(case_id: str, body: OnsetCreate, store: Store = Depends(get_store)):
    store.get_case(case_id)
    o = state.new_onset(body.t, body.sigma_s if body.sigma_s is not None else 0.3, 0.0, None, "manual", "accepted")
    with store.lock(case_id):
        state.write_onsets(store, case_id, state.read_onsets(store, case_id) + [o])
    store.touch(case_id)
    return o


@router.patch("/cases/{case_id}/lightning/onsets/{oid}")
def patch_onset(case_id: str, oid: str, body: OnsetPatch, store: Store = Depends(get_store)):
    store.get_case(case_id)
    with store.lock(case_id):
        onsets = state.read_onsets(store, case_id)
        o = state.find(onsets, oid)
        for k, v in body.model_dump(exclude_unset=True).items():
            if v is not None:
                o[k] = round(float(v), 3) if k in ("t", "sigma_s") else v
        state.write_onsets(store, case_id, onsets)
    store.touch(case_id)
    return o


@router.delete("/cases/{case_id}/lightning/onsets/{oid}", status_code=204)
def delete_onset(case_id: str, oid: str, store: Store = Depends(get_store)):
    store.get_case(case_id)
    with store.lock(case_id):
        onsets = state.read_onsets(store, case_id)
        state.find(onsets, oid)
        state.write_onsets(store, case_id, [o for o in onsets if o["id"] != oid])
    store.touch(case_id)
    return Response(status_code=204)


# ---- flashes ----------------------------------------------------------------------------------

@router.post("/cases/{case_id}/lightning/detect-flashes")
def detect_flashes(case_id: str, body: DetectFlashesBody | None = None, store: Store = Depends(get_store),
                   jobs: JobManager = Depends(get_jobs)):
    body = body or DetectFlashesBody()
    _, rec = _recording(store, case_id)
    video = store.original_path(case_id)
    if not rec.get("has_video") or video is None:
        raise HTTPException(400, "the recording has no video")
    _no_running(jobs, case_id, "lightning.detect_flashes")
    duration = _duration(store, case_id, rec)

    def work(ctx: JobContext):
        ctx.progress(0.01, "Reading video frames")
        trace = flash_mod.load_or_build_trace(video, store.path(case_id, state.LUMA), duration_s=duration,
                                              progress=ctx.sub(0.01, 0.9), cancelled=lambda: ctx.cancelled)
        ctx.check_cancelled()
        found = flash_mod.detect_flashes(trace, body.sensitivity)
        new = [state.new_flash(f["t"], f["strength"]) for f in found]
        with store.lock(case_id):
            merged, added = state.replace_detected(state.read_flashes(store, case_id), new, "video", tol_s=0.2)
            state.write_flashes(store, case_id, merged)
        store.touch(case_id)
        ctx.progress(1.0, f"Found {added} flash{'es' if added != 1 else ''}")
        return {"count": added}

    return jobs.submit("lightning.detect_flashes", case_id, work)


@router.post("/cases/{case_id}/lightning/flashes")
def add_flash(case_id: str, body: FlashCreate, store: Store = Depends(get_store)):
    store.get_case(case_id)
    f = state.new_flash(body.t, 0.0, "manual", "accepted")
    with store.lock(case_id):
        state.write_flashes(store, case_id, state.read_flashes(store, case_id) + [f])
    store.touch(case_id)
    return f


@router.patch("/cases/{case_id}/lightning/flashes/{fid}")
def patch_flash(case_id: str, fid: str, body: FlashPatch, store: Store = Depends(get_store)):
    store.get_case(case_id)
    with store.lock(case_id):
        fl = state.read_flashes(store, case_id)
        f = state.find(fl, fid)
        for k, v in body.model_dump(exclude_unset=True).items():
            if v is not None:
                f[k] = round(float(v), 3) if k == "t" else v
        state.write_flashes(store, case_id, fl)
    store.touch(case_id)
    return f


@router.delete("/cases/{case_id}/lightning/flashes/{fid}", status_code=204)
def delete_flash(case_id: str, fid: str, store: Store = Depends(get_store)):
    store.get_case(case_id)
    with store.lock(case_id):
        fl = state.read_flashes(store, case_id)
        state.find(fl, fid)
        state.write_flashes(store, case_id, [f for f in fl if f["id"] != fid])
    store.touch(case_id)
    return Response(status_code=204)


# ---- strikes ------------------------------------------------------------------------------------

@router.post("/cases/{case_id}/lightning/strikes/fetch")
def fetch_strikes(case_id: str, body: StrikeFetch, store: Store = Depends(get_store),
                  jobs: JobManager = Depends(get_jobs)):
    case = store.get_case(case_id)
    if body.t1_utc <= body.t0_utc:
        raise HTTPException(400, "t1_utc must be after t0_utc")
    if body.source == "glm" and body.t1_utc - body.t0_utc > MAX_FETCH_S:
        raise HTTPException(400, "at most 6 hours of GLM data per fetch")
    if body.source == "usgs" and body.t1_utc - body.t0_utc > MAX_FETCH_USGS_S:
        raise HTTPException(400, "at most 31 days of USGS events per fetch")
    if body.bbox is not None:
        bbox = body.bbox.model_dump()
    elif case.get("region"):
        bbox = pad_bbox(case["region"], FETCH_PAD_M)
    else:
        raise HTTPException(400, "set the case region or pass a bbox")
    if body.source == "glm":
        sats = glm.satellites_for(bbox, body.t0_utc, body.t1_utc, body.satellite)   # ValueError -> 400
    else:
        usgs.expand_types(body.event_types)                                        # ValueError -> 400
    _no_running(jobs, case_id, "lightning.fetch_strikes")
    cache = store.settings.cache_dir / "glm"

    def work(ctx: JobContext):
        if body.source == "glm":
            ctx.progress(0.01, f"Listing GLM files (GOES-{sats[0][1:]})")
            strikes, info = glm.fetch_flashes(cache, bbox, body.t0_utc, body.t1_utc, body.satellite,
                                              progress=ctx.progress)
            source = "glm-" + "+".join(info["satellites"])
            msg = f"{len(strikes)} GLM flashes from {info['n_files']} files"
            if info["missing_hours"]:
                msg += f" ({len(info['missing_hours'])} hour(s) had no data)"
        else:
            ctx.progress(0.01, "Querying USGS ComCat")
            strikes, info = usgs.fetch_events(bbox, body.t0_utc, body.t1_utc, body.event_types, progress=ctx.progress)
            source = usgs.SOURCE
            msg = (f"{len(strikes)} USGS events" + (": " + ", ".join(f"{n} {k}" for k, n in info["kinds"].items())
                                                   if strikes else " of these types here (blast coverage is mostly US)"))
        with store.lock(case_id):
            state.replace_catalogue(store, case_id, source, strikes, body.t0_utc, body.t1_utc, bbox)
        store.touch(case_id)
        ctx.progress(1.0, msg)
        return {"count": len(strikes)}

    return jobs.submit("lightning.fetch_strikes", case_id, work)


@router.post("/cases/{case_id}/lightning/strikes/import")
def import_strikes(case_id: str, file: UploadFile = File(...), store: Store = Depends(get_store)):
    store.get_case(case_id)
    data = file.file.read(MAX_CSV_BYTES + 1)
    if len(data) > MAX_CSV_BYTES:
        raise HTTPException(400, "the CSV file is too large (200 MB at most)")
    strikes = catalogue.parse_csv(data)                       # ValueError -> 400
    with store.lock(case_id):
        state.replace_catalogue(store, case_id, "csv", strikes)
    store.touch(case_id)
    return {"count": len(strikes)}


@router.post("/cases/{case_id}/lightning/strikes")
def add_strike(case_id: str, body: StrikeCreate, store: Store = Depends(get_store)):
    """A user-supplied known event (a reported explosion, a firework display ...)."""
    store.get_case(case_id)
    s = catalogue.make_strike(body.t_utc, body.lat, body.lon, body.sigma_m or MANUAL_SIGMA_M, None,
                              canonical_kind(body.kind) or "event", state.MANUAL)
    s["id"] = new_id("st")
    with store.lock(case_id):
        state.add_manual_strike(store, case_id, s)
    store.touch(case_id)
    return s


@router.delete("/cases/{case_id}/lightning/strikes/{sid}", status_code=204)
def delete_strike(case_id: str, sid: str, store: Store = Depends(get_store)):
    store.get_case(case_id)
    with store.lock(case_id):
        state.delete_strike(store, case_id, sid)
    store.touch(case_id)
    return Response(status_code=204)


@router.get("/cases/{case_id}/lightning/strikes")
def list_strikes(case_id: str, store: Store = Depends(get_store)):
    store.get_case(case_id)
    _, strikes = state.read_strikes(store, case_id)
    return {"strikes": strikes}


@router.delete("/cases/{case_id}/lightning/strikes", status_code=204)
def delete_strikes(case_id: str, store: Store = Depends(get_store)):
    store.get_case(case_id)
    with store.lock(case_id):
        state.delete_strikes(store, case_id)
    store.touch(case_id)
    return Response(status_code=204)


# ---- solve --------------------------------------------------------------------------------------

@router.post("/cases/{case_id}/lightning/solve")
def solve(case_id: str, body: SolveBody | None = None, store: Store = Depends(get_store),
          jobs: JobManager = Depends(get_jobs)):
    body = body or SolveBody()
    case, rec = _recording(store, case_id)
    used, _ = solver.select_onsets(state.read_onsets(store, case_id))
    if not used:
        raise HTTPException(400, "no onsets (thunder or other bangs) to match: run 'detect thunder' or add "
                                 "onsets by hand")
    if not state.read_strikes(store, case_id)[1]:
        raise HTTPException(400, "no events to match: fetch GLM lightning or USGS events, import a catalogue, "
                                 "or add a known event first")
    _no_running(jobs, case_id, "lightning.solve")
    # start_utc omitted: use the case's start time (if any); given (even null): use exactly that
    start = body.start_utc if "start_utc" in body.model_fields_set else case.get("start_utc")
    duration = _duration(store, case_id, rec)
    request = body.model_dump()

    def work(ctx: JobContext):
        c = store.get_case(case_id)
        onsets = state.read_onsets(store, case_id)
        fl = state.read_flashes(store, case_id)
        _, strikes = state.read_strikes(store, case_id)
        result = solver.solve(onsets, fl, strikes, c=case_sound_speed(c), duration_s=duration, region=c.get("region"),
                              date=c.get("date"), start_utc=start, start_tol_s=body.start_tol_s,
                              use_flashes=body.use_flashes, spacing_m=body.spacing_m, max_range_km=body.max_range_km,
                              progress=ctx.progress)
        doc = {"id": new_id("ls"), "created_at": time.time(), "request": request, **result}
        state.save_solve(store, case_id, doc)
        store.touch(case_id)
        b = doc["best"]
        ctx.progress(1.0, f"Best: {b['n_matched']} onsets matched (score {b['score']:.1f})" if b else "No solution")
        return {"solve_id": doc["id"]}

    return jobs.submit("lightning.solve", case_id, work)


@router.get("/cases/{case_id}/lightning/solves/{sid}")
def get_solve(case_id: str, sid: str, store: Store = Depends(get_store)):
    store.get_case(case_id)
    return state.get_solve(store, case_id, sid)


@router.delete("/cases/{case_id}/lightning/solves/{sid}", status_code=204)
def delete_solve(case_id: str, sid: str, store: Store = Depends(get_store)):
    store.get_case(case_id)
    state.delete_solve(store, case_id, sid)
    store.touch(case_id)
    return Response(status_code=204)

