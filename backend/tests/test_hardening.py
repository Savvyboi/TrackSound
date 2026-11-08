"""Input validation, friendly errors, and the memory-lean spectrogram (0.2.1)."""
from __future__ import annotations

import numpy as np
import pytest
from dopplertrack.audio import compute_spectrogram as library_spectrogram

from tracksound.jobs import _error_text
from tracksound.media import _unreadable
from tracksound.spectrogram import SPEC_PARAMS, compute_spectrogram, quantise


@pytest.fixture
def case_id(client):
    return client.post("/api/cases", json={"name": "t"}).json()["id"]


@pytest.mark.parametrize("body", [
    {"date": "2026-02-30"},            # not a calendar date
    {"date": "0002-11-05"},            # a half-typed year
    {"name": "   "},
    {"start_utc": -99999999999},
])
def test_bad_case_values_are_rejected(client, case_id, body):
    r = client.patch(f"/api/cases/{case_id}", json=body)
    assert r.status_code == 422, r.text


def test_good_case_values_are_saved(client, case_id):
    r = client.patch(f"/api/cases/{case_id}", json={"date": "2024-02-29", "name": "  Garden  ", "start_utc": 1789388701})
    assert r.status_code == 200
    assert r.json()["date"] == "2024-02-29" and r.json()["name"] == "Garden"


def test_create_rejects_blank_name(client):
    assert client.post("/api/cases", json={"name": " "}).status_code == 422


def test_delete_case_removes_it(client, case_id, settings):
    assert client.delete(f"/api/cases/{case_id}").status_code == 204
    assert client.get(f"/api/cases/{case_id}").status_code == 404
    assert not (settings.cases_dir / case_id).exists()
    assert client.delete(f"/api/cases/{case_id}").status_code == 404


def test_job_errors_read_as_sentences():
    assert _error_text(ValueError("the file has no audio stream")) == "The file has no audio stream"
    assert _error_text(KeyError("x")).startswith("KeyError")


def test_unreadable_file_message_hides_paths():
    msg = _unreadable("[mp3 @ 0000022a1d202380] Failed to read frame size\nC:\\data\\c_x\\original.mp3: Invalid argument",
                      "C:\\data\\c_x\\original.mp3")
    assert "C:\\" not in msg and "0000022a" not in msg and "Invalid argument" in msg


@pytest.mark.parametrize("whiten", [600.0, None])
@pytest.mark.parametrize("dur", [30, 1500])
def test_spectrogram_matches_the_library_exactly(whiten, dur):
    rng = np.random.default_rng(1)
    sr = 4000
    t = np.arange(dur * sr) / sr
    x = (0.05 * rng.standard_normal(len(t)) + 0.02 * np.sin(2 * np.pi * 120 * t)).astype(np.float32)
    p = dict(SPEC_PARAMS, whiten_window_s=whiten)
    a, b = library_spectrogram(x, sr, **p), compute_spectrogram(x, sr, **p)
    assert np.array_equal(a.S, b.S) and np.array_equal(a.t, b.t) and np.array_equal(a.f, b.f)


def test_quantise_handles_nan_and_inf():
    S = np.array([[np.nan, np.inf, -np.inf, -10.0, 10.0, 30.0, 99.0]], np.float32)
    assert quantise(S).tolist() == [[0, 255, 0, 0, 127, 255, 255]]
