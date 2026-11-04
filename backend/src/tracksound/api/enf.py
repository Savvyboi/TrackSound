"""ENF API: mains-hum frequency vs grid-frequency records (see docs/API.md, "ENF source")."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel

from .. import countries as C
from ..deps import get_jobs, get_store
from ..jobs import JobContext, JobManager
from ..sources.enf import gridmap, references, service
from ..store import Store

router = APIRouter(tags=["enf"])

MAX_IMPORT_BYTES = 512 << 20


class ExtractRequest(BaseModel):
    nominal: Literal["auto", 50, 60, "50", "60"] = "auto"

    def value(self) -> str | int:
        return self.nominal if self.nominal == "auto" else int(self.nominal)


class MatchRequest(BaseModel):
    reference_id: str
    t0_utc: float
    t1_utc: float


# ---- per case ----------------------------------------------------------------------------------

@router.get("/api/cases/{case_id}/enf")
def get_enf(case_id: str, store: Store = Depends(get_store)):
    store.get_case(case_id)
    return {"analysis": store.read_json(case_id, service.ANALYSIS), "matches": service.list_matches(store, case_id)}


@router.post("/api/cases/{case_id}/enf/extract")
def extract(case_id: str, body: ExtractRequest | None = None, store: Store = Depends(get_store),
            jobs: JobManager = Depends(get_jobs)):
    body = body or ExtractRequest()
    case = store.get_case(case_id)
    if (case.get("recording") or {}).get("status") != "ready" or not store.audio_path(case_id).exists():
        raise HTTPException(400, "the case has no processed recording yet")
    if jobs.active(case_id, "enf.extract"):
        raise HTTPException(409, "an ENF extraction is already running for this case")

    def work(ctx: JobContext):
        doc = service.run_extract(store, case_id, body.value(), progress=ctx.sub(0.0, 0.99))
        store.touch(case_id)
        return {"nominal_hz": doc["nominal_hz"]}

    return jobs.submit("enf.extract", case_id, work)


@router.post("/api/cases/{case_id}/enf/match")
def match(case_id: str, body: MatchRequest, store: Store = Depends(get_store), jobs: JobManager = Depends(get_jobs)):
    store.get_case(case_id)
    service.check_match_request(store, case_id, body.reference_id, body.t0_utc, body.t1_utc)
    if jobs.active(case_id, "enf.match"):
        raise HTTPException(409, "an ENF match is already running for this case")

    case = store.get_case(case_id)

    def work(ctx: JobContext):
        doc = service.run_match(store, case_id, body.reference_id, body.t0_utc, body.t1_utc,
                                progress=ctx.sub(0.0, 0.99))
        codes = case.get("countries") or []
        if codes:   # a reference from another grid cannot confirm a recording made in these countries
            grids = C.grids_for(codes, case.get("date"))
            info = references.get_info(store.settings, body.reference_id)
            if not C.grid_matches(info.grid, grids):
                names = ", ".join(C.get(c).short_name for c in codes)
                doc["notes"].append(f"This reference is for the {info.grid} grid, but {names} "
                                    f"{'is' if len(codes) == 1 else 'are'} on "
                                    f"{' / '.join(grids) or 'a grid with no built-in reference'}. "
                                    "A match here would contradict the country you set.")
                store.write_json(case_id, f"enf/matches/{doc['id']}.json", doc)
        store.touch(case_id)
        return {"match_id": doc["id"]}

    return jobs.submit("enf.match", case_id, work)


@router.get("/api/cases/{case_id}/enf/matches/{match_id}")
def get_match(case_id: str, match_id: str, store: Store = Depends(get_store)):
    store.get_case(case_id)
    return service.get_match(store, case_id, match_id)


@router.delete("/api/cases/{case_id}/enf/matches/{match_id}", status_code=204)
def delete_match(case_id: str, match_id: str, store: Store = Depends(get_store)):
    store.get_case(case_id)
    with store.lock(case_id):
        service.delete_match(store, case_id, match_id)
    return Response(status_code=204)


# ---- global ---------------------------------------------------------------------------------------

@router.get("/api/enf/references")
def list_references(country: list[str] = Query(default=[]), date: str | None = None,
                    store: Store = Depends(get_store)):
    """All references; with ``country`` (repeatable) each gets ``in_area`` (its power grid covers
    one of those countries on ``date``), matching ones first."""
    refs = references.list_references(store.settings)
    if not country:
        return refs
    grids = C.grids_for(C.validate(country), date)
    for r in refs:
        r["in_area"] = C.grid_matches(r.get("grid", ""), grids)
    return sorted(refs, key=lambda r: not r["in_area"])


@router.post("/api/enf/references/import")
def import_reference(file: UploadFile = File(...), name: str = Form("Imported reference"), grid: str = Form("Unknown"),
                     nominal_hz: int = Form(...), store: Store = Depends(get_store)):
    raw = file.file.read(MAX_IMPORT_BYTES + 1)
    if len(raw) > MAX_IMPORT_BYTES:
        raise HTTPException(400, f"the file is larger than {MAX_IMPORT_BYTES >> 20} MB")
    if not raw.strip():
        raise HTTPException(400, "the uploaded file is empty")
    return references.import_reference(store.settings, raw, name, grid, nominal_hz)


@router.get("/api/enf/references/{reference_id}")
def get_reference(reference_id: str, store: Store = Depends(get_store)):
    return references.get_reference(store.settings, reference_id)


@router.delete("/api/enf/references/{reference_id}", status_code=204)
def delete_reference(reference_id: str, store: Store = Depends(get_store)):
    references.delete_reference(store.settings, reference_id)
    return Response(status_code=204)


@router.get("/api/enf/grid-map")
def grid_map():
    return gridmap.grid_map()
