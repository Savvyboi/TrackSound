"""FastAPI dependencies shared by all routers."""
from __future__ import annotations

from fastapi import Request

from .config import Settings
from .jobs import JobManager
from .store import Store


def get_store(request: Request) -> Store:
    return request.app.state.store


def get_jobs(request: Request) -> JobManager:
    return request.app.state.jobs


def get_settings(request: Request) -> Settings:
    return request.app.state.settings
