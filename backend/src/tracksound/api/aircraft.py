"""Aircraft source API: prop Doppler events, ADS-B flights, location searches (docs/API.md)."""
from __future__ import annotations

import math
import threading
import time
from typing import Literal

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field, model_validator

from .. import spectrogram
from ..deps import get_jobs, get_store
from ..jobs import JobContext, JobManager
from ..physics import case_sound_speed, pad_bbox
from ..sources.aircraft import flights as fl
from ..sources.aircraft import search as srch
from ..store import Store, new_id
from .. import countries as C
from .cases import BBox
from .countries import require_area

router = APIRouter(prefix="/api/cases/{case_id}/aircraft", tags=["aircraft"])

EVENTS = "aircraft/events.json"
FLIGHTS = "aircraft/flights.json"


# ---- request bodies ------------------------------------------------------------------------

class DetectBody(BaseModel):
    band: tuple[float, float] = (60.0, 300.0)
    harmonics: int = Field(1, ge=1, le=3)
    top: int = Field(12, ge=1, le=60)
    window_s: float = Field(240.0, ge=60, le=900)

    @model_validator(mode="after")
    def _band(self):
        if not (10 <= self.band[0] < self.band[1] <= 450):
            raise ValueError("band must lie within 10-450 Hz with low < high")
        return self


class FitBody(BaseModel):
    t_start: float
    t_end: float
    f_lo: float
    f_hi: float
    harmonics: int = Field(1, ge=1, le=3)

    @model_validator(mode="after")
    def _ranges(self):
        if self.t_end - self.t_start < 20:
            raise ValueError("the fit window must be at least 20 s long")
        if not (10 <= self.f_lo < self.f_hi <= 450):
            raise ValueError("the frequency band must lie within 10-450 Hz with low < high")
        return self


class EventPatch(BaseModel):
    status: Literal["candidate", "accepted", "rejected"] | None = None
    harmonics: int | None = Field(None, ge=1, le=3)


class FetchBody(BaseModel):
    date: str | None = None
    bbox: BBox | None = None
    keep_jets: bool = False


class Point(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class SearchBody(BaseModel):
    mode: Literal["blind", "point", "single"]
    events: list[str]
    spacing_m: float = Field(200.0, ge=25, le=5000)
    tol_s: float | None = Field(None, gt=0, le=600)
    point: Point | None = None
    event_utc: float | None = None
    use_dem: bool = True
    silence: bool = True

    @model_validator(mode="after")
    def _shape(self):
        need = 1 if self.mode == "single" else 2
        if len(self.events) != need or len(set(self.events)) != need:
            raise ValueError(f"mode {self.mode!r} needs exactly {need} different event id(s)")
        if self.mode == "point" and self.point is None:
            raise ValueError("mode 'point' needs a point")
        return self


class RefineBody(BaseModel):
    rank: int = Field(1, ge=1)
    spacing_m: float = Field(50.0, ge=10, le=500)
    half_m: float = Field(600.0, ge=50, le=5000)
    extra_events: list[str] = []
    use_dem: bool = True


# ---- helpers -------------------------------------------------------------------------------

def _events(store: Store, case_id: str) -> list[dict]:
    return store.read_json(case_id, EVENTS, [])


def _event_map(store: Store, case_id: str) -> dict[str, dict]:
    return {e["id"]: e for e in _events(store, case_id)}


def _require_spectrogram(store: Store, case_id: str):
    case = store.get_case(case_id)
    if not ((case.get("recording") or {}).get("status") == "ready" and store.spectrogram_path(case_id).exists()):
        raise HTTPException(409, "upload a recording and wait for it to be processed first")
    return case


def quality(score: float) -> str:
    """Heuristic label from the fit score (mean whitened dB along the curve). Fits to pure
    noise score about 1 dB; real flyovers score 3 dB and up (9-16 dB on dopplertrack's
    reference recording)."""
    return "strong" if score >= 2.5 else "possible" if score >= 1.5 else "weak"


def _event_doc(fit, origin: str, status: str, harmonics: int, band) -> dict:
    d = {k: float(getattr(fit, k)) for k in srch.EVENT_FIT_FIELDS}
    return {"id": new_id("ae"), "origin": origin, "status": status, "harmonics": int(harmonics),
            "band": [float(band[0]), float(band[1])], "quality": quality(d["score"]), **d}


def _flight_set(store: Store, case_id: str) -> dict | None:
    fs = store.read_json(case_id, FLIGHTS)
    if fs and fl.cache_file(store.settings.flights_dir, fs).exists():
        return fs
    return None


def _traces(store: Store, case_id: str):
    fs = _flight_set(store, case_id)
    if fs is None:
        raise HTTPException(409, "no flights for this case yet: set the date and region, then fetch flights")
    return fs, fl.load(fl.cache_file(store.settings.flights_dir, fs))


def _elevation(use_dem: bool, notes: list[str]):
    if not use_dem:
        return None
    from dopplertrack.terrain import Terrain
    terrain = Terrain()

    def elev(lat, lon):
        try:
            return terrain.elevation(lat, lon)
        except Exception as e:  # noqa: BLE001 - offline: fall back to sea level
            notes.append(f"Terrain elevation unavailable ({type(e).__name__}); assumed the listener is at sea level.")
            return np.zeros(np.shape(lat))
    return elev


def _search_path(sid: str, internal: bool = False) -> str:
    return f"aircraft/searches/{sid}{'.internal' if internal else ''}.json"


def _load_search(store: Store, case_id: str, sid: str) -> dict:
    s = store.read_json(case_id, _search_path(sid))
    if s is None:
        raise HTTPException(404, "no such search")
    return s


# ---- events --------------------------------------------------------------------------------

@router.get("/events")
def list_events(case_id: str, store: Store = Depends(get_store)):
    store.get_case(case_id)
    evs = _events(store, case_id)
    for e in evs:
        e.setdefault("quality", quality(e["score"]))
    return sorted(evs, key=lambda e: e["t_cpa"])


@router.post("/detect")
def detect(case_id: str, body: DetectBody, store: Store = Depends(get_store), jobs: JobManager = Depends(get_jobs)):
    case = _require_spectrogram(store, case_id)
    if jobs.active(case_id, "aircraft.detect"):
        raise HTTPException(409, "flyover detection is already running for this case")
    c = case_sound_speed(case)

    def work(ctx: JobContext):
        from dopplertrack.tracks import detect_events
        ctx.progress(0.01, "Loading spectrogram")
        sp = spectrogram.load(store.spectrogram_path(case_id))
        n_win = len(np.arange(sp.t[0], max(sp.t[-1] - body.window_s, sp.t[0]) + 1e-9, 120.0))
        real = threading.Event()

        def on_window(k: int, n: int) -> None:
            real.set()
            ctx.progress(0.02 + 0.93 * k / n, f"Scanning window {k} of {n}")

        # the windows run in parallel and report all at once near the end: until then, show an
        # estimate that keeps moving (it slows down and never claims more than 80%)
        def heartbeat() -> None:
            t_start = time.time()
            tau = 40.0 + 6.0 * n_win
            while not real.wait(2.0):
                el = time.time() - t_start
                frac = 0.02 + 0.78 * (1.0 - math.exp(-el / tau))
                try:
                    ctx.progress(frac, f"Scanning {n_win} windows in parallel ({int(el // 60)}:{int(el % 60):02d} so far)")
                except Exception:  # noqa: BLE001 - cancelled or finished: stop quietly
                    return

        hb = threading.Thread(target=heartbeat, daemon=True)
        hb.start()
        try:
            found = detect_events(sp, body.band[0], body.band[1], c=c, harmonics=body.harmonics, top=body.top,
                                  window_s=body.window_s, progress=on_window)
        finally:
            real.set()
            hb.join(timeout=5)
        ctx.progress(0.97, "Saving")
        with store.lock(case_id):
            keep = [e for e in _events(store, case_id) if e["origin"] == "manual" or e["status"] == "accepted"]
            new = [_event_doc(f, "detected", "candidate", body.harmonics, body.band) for f in found
                   if all(abs(f.t_cpa - k["t_cpa"]) >= 60 for k in keep)]
            store.write_json(case_id, EVENTS, keep + new)
        store.touch(case_id)
        return {"count": len(new)}
    return jobs.submit("aircraft.detect", case_id, work)


@router.post("/fit")
def fit(case_id: str, body: FitBody, store: Store = Depends(get_store), jobs: JobManager = Depends(get_jobs)):
    case = _require_spectrogram(store, case_id)
    c = case_sound_speed(case)

    def work(ctx: JobContext):
        from dopplertrack.tracks import fit_event
        ctx.progress(0.05, "Fitting a Doppler curve")
        sp = spectrogram.load(store.spectrogram_path(case_id))
        t0, t1 = max(body.t_start, float(sp.t[0])), min(body.t_end, float(sp.t[-1]))
        if t1 - t0 < 20:
            raise ValueError("the fit window is outside the recording")
        f = fit_event(sp, t0, t1, body.f_lo, body.f_hi, c=c, harmonics=body.harmonics)
        ev = _event_doc(f, "manual", "accepted", body.harmonics, (body.f_lo, body.f_hi))
        with store.lock(case_id):
            store.write_json(case_id, EVENTS, _events(store, case_id) + [ev])
        store.touch(case_id)
        return {"event_id": ev["id"]}
    return jobs.submit("aircraft.fit", case_id, work)


@router.patch("/events/{eid}")
def patch_event(case_id: str, eid: str, body: EventPatch, store: Store = Depends(get_store)):
    store.get_case(case_id)
    with store.lock(case_id):
        evs = _events(store, case_id)
        for e in evs:
            if e["id"] == eid:
                e.update(body.model_dump(exclude_none=True))
                store.write_json(case_id, EVENTS, evs)
                return e
    raise HTTPException(404, "no such event")


@router.delete("/events/{eid}", status_code=204)
def delete_event(case_id: str, eid: str, store: Store = Depends(get_store)):
    store.get_case(case_id)
    with store.lock(case_id):
        evs = _events(store, case_id)
        rest = [e for e in evs if e["id"] != eid]
        if len(rest) == len(evs):
            raise HTTPException(404, "no such event")
        store.write_json(case_id, EVENTS, rest)
    return Response(status_code=204)


# ---- flights -------------------------------------------------------------------------------

@router.get("/flights")
def get_flights(case_id: str, store: Store = Depends(get_store)):
    store.get_case(case_id)
    fs = _flight_set(store, case_id)
    if fs is None:
        return {"status": "none", "date": None, "bbox": None, "fetched_at": None, "n_flights": 0, "flights": []}
    traces = fl.load(fl.cache_file(store.settings.flights_dir, fs))
    return {"status": "ready", "date": fs["date"], "bbox": fs["bbox"], "fetched_at": fs.get("fetched_at"),
            "n_flights": len(traces), "flights": fl.summaries(traces), "attribution": fl.ATTRIBUTION}


@router.post("/flights/fetch")
def fetch_flights(case_id: str, body: FetchBody, store: Store = Depends(get_store), jobs: JobManager = Depends(get_jobs)):
    case = store.get_case(case_id)
    date = body.date or case.get("date")
    if not date:
        raise HTTPException(400, "set the case date first (the UTC day of the recording)")
    region = body.bbox.model_dump() if body.bbox else pad_bbox(require_area(case, "download flights for").bbox, 15_000.0)
    if jobs.active(case_id, "aircraft.fetch"):
        raise HTTPException(409, "flights are already being fetched for this case")
    flights_dir = store.settings.flights_dir

    def work(ctx: JobContext):
        meta = fl.find_cached(flights_dir, date, region, body.keep_jets)
        if meta is None:
            ctx.progress(0.0, f"Finding the adsb.lol archive for {date}")
            traces, extra = fl.fetch_day(date, region, body.keep_jets, progress=ctx.sub(0.0, 0.97))
            ctx.progress(0.98, f"Saving {len(traces)} flights")
            meta = fl.save_cached(flights_dir, date, region, body.keep_jets, traces, extra)
        else:
            ctx.progress(0.9, "Using cached flights for this day")
        store.write_json(case_id, FLIGHTS, {**meta, "linked_at": time.time()})
        store.touch(case_id)
        return {"n_flights": int(meta["n_traces"]), "cached": True}
    return jobs.submit("aircraft.fetch", case_id, work)


@router.get("/flights/geojson")
def flights_geojson(case_id: str, t0_utc: float | None = None, t1_utc: float | None = None,
                    store: Store = Depends(get_store)):
    store.get_case(case_id)
    fs = _flight_set(store, case_id)
    if fs is None:
        return {"type": "FeatureCollection", "features": []}
    traces = fl.load(fl.cache_file(store.settings.flights_dir, fs))
    return fl.geojson(traces, fs.get("bbox"), t0_utc, t1_utc)


@router.get("/flights/{idx}")
def flight_track(case_id: str, idx: int, store: Store = Depends(get_store)):
    store.get_case(case_id)
    _, traces = _traces(store, case_id)
    try:
        return fl.track(traces, idx)
    except IndexError:
        raise HTTPException(404, "no such flight")


# ---- searches ------------------------------------------------------------------------------

def _check_events(store: Store, case_id: str, ids: list[str]) -> dict[str, dict]:
    evs = _event_map(store, case_id)
    missing = [i for i in ids if i not in evs]
    if missing:
        raise HTTPException(400, f"unknown event id(s): {', '.join(missing)}")
    return evs


def _single_time(case: dict, ev: dict, body: SearchBody) -> float:
    if body.event_utc is not None:
        return float(body.event_utc)
    if case.get("start_utc") is None:
        raise HTTPException(400, "single-event search needs the recording start time (set it in Setup) or event_utc")
    return float(case["start_utc"]) + float(ev["t_cpa"])


def _silence_allowed(body: SearchBody, evs: dict[str, dict]) -> tuple[bool, str | None]:
    """The 'silence' filter drops candidates that would have heard another close prop between
    the two events. That is right for a quiet sky, and wrong when the recording itself shows
    other flyovers in between (a busy sky): then the true location always fails it. On the
    eagle-cam day that filter pushed the right answer out of the top 5; without it the search
    came within 51 m."""
    if body.mode == "single" or not body.silence:
        return False, None
    a, b = sorted((evs[body.events[0]]["t_cpa"], evs[body.events[1]]["t_cpa"]))
    between = [e for e in evs.values() if e["id"] not in body.events and e.get("status") != "rejected"
               and e.get("quality", "strong") != "weak" and a + 40 < e["t_cpa"] < b - 40]
    if between:
        return False, (f"{len(between)} other flyover(s) were detected between the two events, so the filter that "
                       "requires no other aircraft in between was switched off. Adjacent events usually give a "
                       "clearer result.")
    return True, None


@router.post("/search/estimate")
def search_estimate(case_id: str, body: SearchBody, store: Store = Depends(get_store)):
    case = store.get_case(case_id)
    evs = _check_events(store, case_id, body.events)
    _, traces = _traces(store, case_id)
    if body.mode == "point":
        return srch.estimate(traces, None, body.spacing_m, "point", point=body.point.model_dump())
    area = require_area(case, "search")
    t = _single_time(case, evs[body.events[0]], body) if body.mode == "single" else None
    return srch.estimate(traces, area, body.spacing_m, body.mode, t)


@router.post("/search")
def run_search(case_id: str, body: SearchBody, store: Store = Depends(get_store), jobs: JobManager = Depends(get_jobs)):
    case = _require_spectrogram(store, case_id)
    evs = _check_events(store, case_id, body.events)
    fs, traces = _traces(store, case_id)
    area = None if body.mode == "point" else require_area(case, "search")
    t_single = _single_time(case, evs[body.events[0]], body) if body.mode == "single" else None
    c = case_sound_speed(case)
    sid = new_id("as")
    request = body.model_dump()

    silence, silence_note = _silence_allowed(body, evs)

    def work(ctx: JobContext):
        notes: list[str] = [silence_note] if silence_note else []
        inp = srch.Inputs(spectrogram.load(store.spectrogram_path(case_id)), traces, c, _elevation(body.use_dem, notes), notes,
                          area=area)
        if area is not None and area.countries:
            notes.append(f"Only positions in {area.describe()} were searched.")
        if body.mode == "single":
            res = srch.single_event_search(inp, evs[body.events[0]], area, t_single, body.spacing_m,
                                           body.tol_s or 30.0, progress=ctx.progress)
        else:
            res = srch.two_event_search(inp, evs[body.events[0]], evs[body.events[1]],
                                        area, body.spacing_m,
                                        body.point.model_dump() if body.point else None, body.tol_s or 6.0,
                                        silence, progress=ctx.progress)
        _save_search(store, case_id, sid, body.mode, None, request, res)
        best = res.candidates[0] if res.candidates else None
        return {"search_id": sid, "n_candidates": len(res.candidates), "best": best and {k: best[k] for k in ("lat", "lon", "score")}}

    def done(job):
        if job["status"] == "error":
            _save_failed(store, case_id, sid, body.mode, None, request, job["error"])
    job = jobs.submit("aircraft.search", case_id, work, on_done=done)
    return job


def _save_search(store: Store, case_id: str, sid: str, mode: str, parent_id: str | None, request: dict, res: srch.Result,
                 parent_margin: float | None = None):
    pub = res.public()
    if mode == "refine":
        # a refine ranks neighbouring fine-grid points, which always score alike, so their gap
        # says nothing; the confidence that matters is the parent's margin over the next flight pair
        pub["margin"] = parent_margin
    doc = {"id": sid, "created_at": time.time(), "mode": mode, "parent_id": parent_id, "request": request,
           "status": "done", "error": None, **pub}
    store.write_json(case_id, _search_path(sid, internal=True),
                     {"lat0": res.lat0, "lon0": res.lon0, "candidates": res.internal})
    store.write_json(case_id, _search_path(sid), doc)
    store.touch(case_id)


def _save_failed(store: Store, case_id: str, sid: str, mode: str, parent_id: str | None, request: dict, error: str | None):
    store.write_json(case_id, _search_path(sid), {
        "id": sid, "created_at": time.time(), "mode": mode, "parent_id": parent_id, "request": request,
        "status": "error", "error": error, "bbox": None,
        "stats": {"n_points": 0, "n_segments": 0, "n_cpa": 0, "n_candidates": 0, "elapsed_s": 0},
        "best": None, "margin": None, "candidates": [], "heat": [], "notes": []})


@router.get("/searches")
def list_searches(case_id: str, store: Store = Depends(get_store)):
    store.get_case(case_id)
    d = store.case_dir(case_id) / "aircraft" / "searches"
    out = []
    if d.exists():
        for p in d.glob("as_*.json"):
            if p.name.endswith(".internal.json"):
                continue
            s = store.read_json(case_id, f"aircraft/searches/{p.name}")
            if s:
                s = {**s, "candidates": s.get("candidates", [])[:5]}
                s.pop("heat", None)
                out.append(s)
    out.sort(key=lambda s: s["created_at"], reverse=True)
    return out


@router.get("/searches/{sid}")
def get_search(case_id: str, sid: str, store: Store = Depends(get_store)):
    store.get_case(case_id)
    return _load_search(store, case_id, sid)


@router.delete("/searches/{sid}", status_code=204)
def delete_search(case_id: str, sid: str, store: Store = Depends(get_store)):
    store.get_case(case_id)
    _load_search(store, case_id, sid)
    store.delete_file(case_id, _search_path(sid))
    store.delete_file(case_id, _search_path(sid, internal=True))
    return Response(status_code=204)


@router.post("/searches/{sid}/refine")
def refine_search(case_id: str, sid: str, body: RefineBody, store: Store = Depends(get_store),
                  jobs: JobManager = Depends(get_jobs)):
    case = _require_spectrogram(store, case_id)
    parent = _load_search(store, case_id, sid)
    if parent.get("status") != "done" or not parent.get("candidates"):
        raise HTTPException(400, "this search has no results to refine")
    internal = store.read_json(case_id, _search_path(sid, internal=True))
    if internal is None:
        raise HTTPException(409, "this search's internal data is missing; run it again")
    evs = _check_events(store, case_id, body.extra_events)
    for f in internal["candidates"][min(body.rank, len(internal["candidates"])) - 1]["flights"]:
        if f["event_id"] not in evs:
            raise HTTPException(409, "an event this search used has been deleted")
    fs, traces = _traces(store, case_id)
    c = case_sound_speed(case)
    new_sid = new_id("as")
    request = {"parent_id": sid, **body.model_dump()}

    def work(ctx: JobContext):
        notes: list[str] = []
        inp = srch.Inputs(spectrogram.load(store.spectrogram_path(case_id)), traces, c, _elevation(body.use_dem, notes), notes,
                          area=C.case_area(case))
        res = srch.refine(inp, parent, internal, body.rank, evs, body.extra_events, body.spacing_m, body.half_m,
                          progress=ctx.progress)
        _save_search(store, case_id, new_sid, "refine", sid, request, res, parent_margin=parent.get("margin"))
        return {"search_id": new_sid}

    def done(job):
        if job["status"] == "error":
            _save_failed(store, case_id, new_sid, "refine", sid, request, job["error"])
    return jobs.submit("aircraft.refine", case_id, work, on_done=done)


@router.get("/searches/{sid}/candidates/{rank}/curves")
def candidate_curves(case_id: str, sid: str, rank: int, store: Store = Depends(get_store)):
    case = _require_spectrogram(store, case_id)
    s = _load_search(store, case_id, sid)
    internal = store.read_json(case_id, _search_path(sid, internal=True))
    if internal is None or not s.get("candidates"):
        raise HTTPException(404, "no candidates")
    _, traces = _traces(store, case_id)
    inp = srch.Inputs(spectrogram.load(store.spectrogram_path(case_id)), traces, case_sound_speed(case))
    return srch.candidate_curves(inp, s, internal, rank, _event_map(store, case_id))
