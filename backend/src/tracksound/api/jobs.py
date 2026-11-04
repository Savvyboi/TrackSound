"""Background job status and cancellation."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..deps import get_jobs
from ..jobs import JobManager

router = APIRouter(prefix="/api", tags=["jobs"])


@router.get("/jobs")
def list_jobs(case_id: str | None = None, jobs: JobManager = Depends(get_jobs)):
    return jobs.list(case_id)


@router.get("/jobs/{job_id}")
def get_job(job_id: str, jobs: JobManager = Depends(get_jobs)):
    j = jobs.get(job_id)
    if j is None:
        raise HTTPException(404, "no such job (jobs are forgotten when the server restarts)")
    return j


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, jobs: JobManager = Depends(get_jobs)):
    j = jobs.cancel(job_id)
    if j is None:
        raise HTTPException(404, "no such job")
    return j
