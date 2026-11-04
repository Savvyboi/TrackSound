"""FastAPI application: JSON API under /api, the built web UI everywhere else."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from .config import Settings, load_settings
from .jobs import JobManager
from .store import NotFound, Store


def web_dir() -> Path | None:
    """The built frontend: $TRACKSOUND_WEB, a packaged copy, or frontend/dist in a checkout."""
    candidates = []
    if os.environ.get("TRACKSOUND_WEB"):
        candidates.append(Path(os.environ["TRACKSOUND_WEB"]))
    here = Path(__file__).resolve()
    candidates.append(here.parent / "web")
    candidates.append(here.parents[3] / "frontend" / "dist")   # <repo>/backend/src/tracksound/app.py
    for c in candidates:
        if (c / "index.html").exists():
            return c
    return None


def _safe_process_start():
    """Jobs run in threads and some (flyover detection) start worker processes. Forking a
    multi-threaded server can deadlock, so start workers from a clean server process instead."""
    import multiprocessing as mp
    if mp.get_start_method(allow_none=True) is None:
        try:
            mp.set_start_method("forkserver")
        except (RuntimeError, ValueError):
            pass


def create_app(settings: Settings | None = None, job_workers: int = 3) -> FastAPI:
    _safe_process_start()
    settings = settings or load_settings()
    store = Store(settings)
    jobs = JobManager(workers=job_workers)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        jobs.shutdown()

    app = FastAPI(title="TrackSound", version="0.1.0", lifespan=lifespan,
                  docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None)
    app.state.settings = settings
    app.state.store = store
    app.state.jobs = jobs

    # the Vite dev server (npm run dev) runs on another port
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                       allow_methods=["*"], allow_headers=["*"],
                       expose_headers=["X-Rows", "X-Cols", "X-Level", "X-Index", "X-Frame0", "X-Frames-Per-Col"])

    @app.exception_handler(NotFound)
    async def _not_found(request: Request, exc: NotFound):
        return JSONResponse({"detail": f"not found: {exc.args[0] if exc.args else ''}"}, status_code=404)

    @app.exception_handler(ValueError)
    async def _bad_value(request: Request, exc: ValueError):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    from .api import aircraft, alerts, cases, enf, jobs as jobs_api, lightning, report, weather
    for r in (cases.router, jobs_api.router, aircraft.router, lightning.router, enf.router, alerts.router,
              report.router, weather.router):
        app.include_router(r)

    web = web_dir()
    if web is not None:
        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str):
            if path.startswith("api/"):
                return JSONResponse({"detail": "not found"}, status_code=404)
            f = (web / path).resolve()
            if path and f.is_file() and web.resolve() in f.parents:
                return FileResponse(f)
            return FileResponse(web / "index.html")
    else:
        @app.get("/", include_in_schema=False)
        async def no_ui():
            return JSONResponse({"detail": "The web UI is not built. Run `npm install && npm run build` in frontend/, "
                                           "or use the dev server (npm run dev). API docs: /api/docs"})

    logging.getLogger("tracksound").setLevel(logging.INFO)
    return app
