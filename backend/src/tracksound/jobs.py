"""In-process background jobs with progress and cooperative cancellation.

Usage from a router::

    def work(ctx: JobContext):
        for i, item in enumerate(items):
            ctx.progress(i / len(items), f"item {i}")   # raises JobCancelled if cancelled
            ...
        return {"count": len(items)}                     # small JSON-able result

    job = jobs.submit("lightning.detect_thunder", case_id, work)
    return job            # the Job document (see docs/API.md)

Jobs live in memory only: they are lost when the server restarts, so a job must write its
real output into the case folder (via the Store) before returning.
"""
from __future__ import annotations

import logging
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from .store import new_id

log = logging.getLogger("tracksound.jobs")


class JobCancelled(Exception):
    """Raised inside a job when the user cancelled it."""


def _error_text(e: Exception) -> str:
    """What the user sees for a failed job. ValueErrors are our own plain-language messages
    ("the file has no audio stream"); anything else keeps its type, which helps bug reports."""
    if isinstance(e, ValueError) and str(e):
        msg = str(e)
        return msg[0].upper() + msg[1:]
    return f"{type(e).__name__}: {e}"


class JobContext:
    def __init__(self, manager: "JobManager", job_id: str):
        self._m = manager
        self.job_id = job_id

    @property
    def cancelled(self) -> bool:
        return self._m._cancel_flags.get(self.job_id, False)

    def check_cancelled(self) -> None:
        if self.cancelled:
            raise JobCancelled()

    def progress(self, fraction: float | None = None, message: str | None = None) -> None:
        """Report progress (0..1) and/or a status message. Raises JobCancelled if cancelled."""
        self._m._update(self.job_id, progress=fraction, message=message)
        self.check_cancelled()

    def sub(self, start: float, end: float) -> Callable[[float, str | None], None]:
        """A progress callback mapping 0..1 onto [start, end] of this job's progress."""
        def cb(fraction: float, message: str | None = None) -> None:
            self.progress(start + (end - start) * max(0.0, min(1.0, fraction)), message)
        return cb


class JobManager:
    def __init__(self, workers: int = 3, keep: int = 500):
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="tracksound-job")
        self._jobs: dict[str, dict] = {}
        self._cancel_flags: dict[str, bool] = {}
        self._lock = threading.Lock()
        self._keep = keep

    def submit(self, kind: str, case_id: str | None, fn: Callable[[JobContext], Any],
               on_done: Callable[[dict], None] | None = None) -> dict:
        jid = new_id("j")
        job = {"id": jid, "case_id": case_id, "kind": kind, "status": "queued", "progress": 0.0, "message": "Queued",
               "created_at": time.time(), "started_at": None, "finished_at": None, "result": None, "error": None}
        with self._lock:
            self._jobs[jid] = job
            self._cancel_flags[jid] = False
            self._trim()
        self._pool.submit(self._run, jid, fn, on_done)
        return dict(job)

    def _trim(self):
        if len(self._jobs) <= self._keep:
            return
        done = [j for j in self._jobs.values() if j["status"] in ("done", "error", "cancelled")]
        done.sort(key=lambda j: j["finished_at"] or 0)
        for j in done[: len(self._jobs) - self._keep]:
            self._jobs.pop(j["id"], None)
            self._cancel_flags.pop(j["id"], None)

    def _update(self, jid: str, **fields):
        with self._lock:
            job = self._jobs.get(jid)
            if job is None:
                return
            for k, v in fields.items():
                if v is None and k in ("progress", "message"):
                    continue
                if k == "progress":
                    v = float(max(0.0, min(1.0, v)))
                job[k] = v

    def _run(self, jid: str, fn, on_done):
        if self._cancel_flags.get(jid):
            self._update(jid, status="cancelled", message="Cancelled", finished_at=time.time())
            return
        self._update(jid, status="running", started_at=time.time(), message="Running")
        ctx = JobContext(self, jid)
        try:
            result = fn(ctx)
            self._update(jid, status="done", progress=1.0, result=result, finished_at=time.time(),
                         message=(self._jobs[jid].get("message") if self._jobs[jid].get("message") not in ("Running", "Queued") else "Done"))
        except JobCancelled:
            self._update(jid, status="cancelled", message="Cancelled", finished_at=time.time())
        except Exception as e:  # noqa: BLE001 - every failure must land in the job document
            log.error("job %s failed:\n%s", jid, traceback.format_exc())
            self._update(jid, status="error", error=_error_text(e), message="Failed", finished_at=time.time())
        finally:
            if on_done:
                try:
                    on_done(self.get(jid))
                except Exception:  # noqa: BLE001
                    log.exception("on_done hook failed for job %s", jid)

    def get(self, jid: str) -> dict | None:
        with self._lock:
            j = self._jobs.get(jid)
            return dict(j) if j else None

    def list(self, case_id: str | None = None) -> list[dict]:
        with self._lock:
            js = [dict(j) for j in self._jobs.values() if case_id is None or j["case_id"] == case_id]
        js.sort(key=lambda j: j["created_at"], reverse=True)
        return js

    def cancel(self, jid: str) -> dict | None:
        with self._lock:
            if jid not in self._jobs:
                return None
            self._cancel_flags[jid] = True
            if self._jobs[jid]["status"] == "queued":
                self._jobs[jid].update(status="cancelled", message="Cancelled", finished_at=time.time())
        return self.get(jid)

    def active(self, case_id: str, kind: str) -> dict | None:
        """The queued/running job of this kind for this case, if any."""
        for j in self.list(case_id):
            if j["kind"] == kind and j["status"] in ("queued", "running"):
                return j
        return None

    def shutdown(self):
        for jid in list(self._cancel_flags):
            self._cancel_flags[jid] = True
        self._pool.shutdown(wait=False, cancel_futures=True)
