"""Worker processes that behave on every OS (and inside the packaged app).

"forkserver" is the safe choice on Linux and macOS: forking a server that already runs
threads can deadlock a child. Windows only has "spawn", which is also safe.
"""
from __future__ import annotations

import multiprocessing as mp


def start_method() -> str:
    return "forkserver" if "forkserver" in mp.get_all_start_methods() else "spawn"


def mp_context():
    return mp.get_context(start_method())


def set_default_start_method() -> None:
    """Make libraries that create their own pools (dopplertrack's flyover detection) use it."""
    if mp.get_start_method(allow_none=True) is None:
        try:
            mp.set_start_method(start_method())
        except (RuntimeError, ValueError):
            pass
