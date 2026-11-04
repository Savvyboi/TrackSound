"""Shared test fixtures: an isolated data folder, an API client, and synthetic recordings."""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from tracksound.app import create_app
from tracksound.config import load_settings


@pytest.fixture
def settings(tmp_path):
    return load_settings(tmp_path / "data")


@pytest.fixture
def client(settings):
    app = create_app(settings, job_workers=2)
    with TestClient(app) as c:
        yield c


def wait_job(client: TestClient, job_id: str, timeout: float = 120.0) -> dict:
    """Poll a job until it finishes; fail the test if it errors."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = client.get(f"/api/jobs/{job_id}").json()
        if j["status"] in ("done", "error", "cancelled"):
            assert j["status"] == "done", j
            return j
        time.sleep(0.1)
    raise AssertionError(f"job {job_id} did not finish in {timeout} s")


def write_tone_wav(path: Path, seconds: float = 120.0, sr: int = 8000, f0: float = 120.0, seed: int = 0) -> Path:
    """A noisy recording with one slowly falling tone, enough for a spectrogram."""
    t = np.arange(int(seconds * sr)) / sr
    f = f0 * (1.0 - 0.05 * t / seconds)
    x = 0.3 * np.random.default_rng(seed).standard_normal(len(t)) + 0.05 * np.sin(2 * np.pi * np.cumsum(f) / sr)
    sf.write(str(path), x.astype(np.float32), sr)
    return path


@pytest.fixture
def make_case(client, tmp_path):
    """Create a case with an uploaded and processed synthetic recording; returns the case JSON."""
    def _make(name="test", seconds=120.0, wav: Path | None = None):
        case = client.post("/api/cases", json={"name": name}).json()
        wav = wav or write_tone_wav(tmp_path / f"{name}.wav", seconds=seconds)
        with open(wav, "rb") as fh:
            r = client.post(f"/api/cases/{case['id']}/recording", files={"file": (wav.name, fh, "audio/wav")})
        assert r.status_code == 200, r.text
        wait_job(client, r.json()["job"]["id"])
        return client.get(f"/api/cases/{case['id']}").json()
    return _make
