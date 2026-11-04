"""Command line. ``tracksound`` on its own (or double-clicking the app) starts the web app and
opens it in the browser; ``tracksound serve`` and ``tracksound demo`` give more control."""
from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import webbrowser

from . import __version__

BANNER = """
  TrackSound {version} is running at {url}

  Your recordings stay on this computer. Data folder: {data}
  Keep this window open while you use TrackSound; close it (or press Ctrl+C) to stop.
"""


def _port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if sys.platform != "win32":
            # on Windows SO_REUSEADDR lets a bind succeed on a port another program uses
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def _is_tracksound(host: str, port: int) -> bool:
    import json
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/api/health", timeout=1.5) as r:
            return bool(json.load(r).get("ok"))
    except Exception:  # noqa: BLE001 - anything else on that port is "not us"
        return False


def _pick_port(host: str, port: int) -> tuple[int, bool]:
    """(port to use, True if TrackSound is already running there)."""
    if _port_free(host, port):
        return port, False
    if _is_tracksound(host, port):
        return port, True
    for p in range(port + 1, port + 50):
        if _port_free(host, p):
            return p, False
    raise SystemExit(f"no free port between {port} and {port + 49}; pass --port")


def serve(host: str = "127.0.0.1", port: int = 8000, data: str | None = None, open_browser: bool = True,
          reload: bool = False) -> None:
    import uvicorn
    if data:
        os.environ["TRACKSOUND_DATA"] = data
    from .config import load_settings
    settings = load_settings()
    port, running = _pick_port(host, port)
    url = f"http://{host}:{port}/"
    if running:
        print(f"TrackSound is already running at {url}")
        if open_browser:
            webbrowser.open(url)
        return
    print(BANNER.format(version=__version__, url=url, data=settings.data_dir), flush=True)
    if open_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run("tracksound.app:create_app", factory=True, host=host, port=port, reload=reload,
                log_level="warning" if not reload else "info")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv == ["--no-browser"]:
        argv = ["serve", "--open"] if not argv else ["serve"]       # double-click / plain `tracksound`
    ap = argparse.ArgumentParser(prog="tracksound", description="Acoustic geolocation of recordings, as a local web app.")
    ap.add_argument("--version", action="version", version=f"TrackSound {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="run the web app on this machine")
    s.add_argument("--host", default="127.0.0.1", help="interface to listen on (default: this machine only)")
    s.add_argument("--port", type=int, default=8000, help="port (the next free one is used if it is taken)")
    s.add_argument("--data", help="data folder (default: $TRACKSOUND_DATA or ~/.tracksound)")
    s.add_argument("--open", action="store_true", help="open the browser")
    s.add_argument("--reload", action="store_true", help="auto-reload on code changes (development)")

    d = sub.add_parser("demo", help="create demo cases with synthetic recordings (no downloads needed)")
    d.add_argument("--data", help="data folder")
    d.add_argument("--kind", choices=["aircraft", "lightning", "enf", "alerts", "all"], default="all")

    sub.add_parser("shortcut", help="add TrackSound to the desktop / start menu / applications menu")

    a = ap.parse_args(argv)
    if a.cmd == "serve":
        serve(a.host, a.port, a.data, a.open, a.reload)
    elif a.cmd == "shortcut":
        from .shortcuts import create
        for p in create():
            print(f"created {p}")
    elif a.cmd == "demo":
        from .config import load_settings
        from .demo import make_demo_cases
        settings = load_settings(a.data)
        for case in make_demo_cases(settings, kind=a.kind):
            print(f"created demo case {case['id']}: {case['name']}")


if __name__ == "__main__":
    main()
