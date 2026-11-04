"""Command line: ``tracksound serve`` starts the local web app."""
from __future__ import annotations

import argparse
import threading
import webbrowser


def main(argv=None):
    ap = argparse.ArgumentParser(prog="tracksound", description="Acoustic geolocation of recordings, as a local web app.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="run the web app on this machine")
    s.add_argument("--host", default="127.0.0.1", help="interface to listen on (default: this machine only)")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--data", help="data folder (default: $TRACKSOUND_DATA or ~/.tracksound)")
    s.add_argument("--open", action="store_true", help="open the browser")
    s.add_argument("--reload", action="store_true", help="auto-reload on code changes (development)")

    d = sub.add_parser("demo", help="create a demo case with a synthetic recording (no downloads needed)")
    d.add_argument("--data", help="data folder")
    d.add_argument("--kind", choices=["aircraft", "lightning", "all"], default="all")

    a = ap.parse_args(argv)
    if a.cmd == "serve":
        import os
        import uvicorn
        if a.data:
            os.environ["TRACKSOUND_DATA"] = a.data
        url = f"http://{a.host}:{a.port}/"
        if a.open:
            threading.Timer(1.5, lambda: webbrowser.open(url)).start()
        print(f"TrackSound running at {url}")
        uvicorn.run("tracksound.app:create_app", factory=True, host=a.host, port=a.port, reload=a.reload,
                    log_level="info")
    elif a.cmd == "demo":
        from .config import load_settings
        from .demo import make_demo_cases
        settings = load_settings(a.data)
        for case in make_demo_cases(settings, kind=a.kind):
            print(f"created demo case {case['id']}: {case['name']}")


if __name__ == "__main__":
    main()
