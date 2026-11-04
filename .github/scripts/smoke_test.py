"""Smoke test for a built TrackSound app: start it, use it over HTTP, stop it.

Usage: python smoke_test.py path/to/TrackSound[.exe]

Checks that the web UI is served, the bundled data (countries) loads, an upload is decoded
by the bundled ffmpeg into a spectrogram, and flyover detection runs (worker processes inside
the frozen app). Standard library only, so it runs on a bare CI runner.
"""
from __future__ import annotations

import io
import json
import math
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
import wave

PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"


def call(method: str, path: str, body: bytes | None = None, headers: dict | None = None, timeout: float = 30):
    req = urllib.request.Request(BASE + path, data=body, method=method, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
        return json.loads(data) if r.headers.get("content-type", "").startswith("application/json") else data


def wait_job(jid: str, timeout: float = 300) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = call("GET", f"/api/jobs/{jid}")
        if j["status"] in ("done", "error", "cancelled"):
            if j["status"] != "done":
                raise SystemExit(f"job failed: {j}")
            return j
        time.sleep(1)
    raise SystemExit(f"job {jid} timed out")


def tone_wav(seconds: float = 90.0, sr: int = 8000) -> bytes:
    """Noise-free falling tone (a fake flyover), 16-bit mono WAV."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        ph, frames = 0.0, bytearray()
        for i in range(int(seconds * sr)):
            t = i / sr
            f = 120.0 - 25.0 * math.tanh((t - seconds / 2) / 8.0)
            ph += 2 * math.pi * f / sr
            frames += struct.pack("<h", int(8000 * math.sin(ph)))
        w.writeframes(bytes(frames))
    return buf.getvalue()


def multipart(field: str, filename: str, content: bytes) -> tuple[bytes, str]:
    b = uuid.uuid4().hex
    body = (f"--{b}\r\nContent-Disposition: form-data; name=\"{field}\"; filename=\"{filename}\"\r\n"
            "Content-Type: audio/wav\r\n\r\n").encode() + content + f"\r\n--{b}--\r\n".encode()
    return body, f"multipart/form-data; boundary={b}"


def main(exe: str) -> None:
    data = tempfile.mkdtemp(prefix="tracksound-smoke-")
    proc = subprocess.Popen([exe, "serve", "--port", str(PORT), "--data", data])
    try:
        for _ in range(120):
            try:
                if call("GET", "/api/health", timeout=2)["ok"]:
                    break
            except Exception:
                time.sleep(1)
        else:
            raise SystemExit("the app did not start within 2 minutes")
        html = call("GET", "/").decode()
        assert "<title>TrackSound</title>" in html, "web UI not served"
        countries = call("GET", "/api/countries")
        assert any(c["code"] == "PT" for c in countries), "country data missing"
        case = call("POST", "/api/cases", json.dumps({"name": "smoke"}).encode(), {"content-type": "application/json"})
        body, ctype = multipart("file", "tone.wav", tone_wav())
        up = call("POST", f"/api/cases/{case['id']}/recording", body, {"content-type": ctype}, timeout=120)
        wait_job(up["job"]["id"])
        rec = call("GET", f"/api/cases/{case['id']}")["recording"]
        assert rec["status"] == "ready" and abs(rec["duration_s"] - 90) < 1, rec
        det = call("POST", f"/api/cases/{case['id']}/aircraft/detect",
                   json.dumps({"band": [80, 160], "top": 2}).encode(), {"content-type": "application/json"})
        wait_job(det["id"], timeout=600)
        evs = call("GET", f"/api/cases/{case['id']}/aircraft/events")
        assert evs, "flyover detection found nothing"
        print(f"smoke test passed: UI, {len(countries)} countries, decode + spectrogram, detection ({len(evs)} events)")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    main(sys.argv[1])
