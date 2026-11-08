"""Serving the built web UI: content types and fallbacks."""
from __future__ import annotations

import mimetypes

import pytest
from fastapi.testclient import TestClient

from tracksound.app import create_app


@pytest.fixture
def web_client(settings, tmp_path, monkeypatch):
    web = tmp_path / "web"
    (web / "assets").mkdir(parents=True)
    (web / "index.html").write_text("<!doctype html><title>t</title>")
    (web / "assets" / "worker-abc.mjs").write_text("self.onmessage = () => {};")
    (web / "assets" / "app-abc.js").write_text("console.log(1);")
    (web / "assets" / "app-abc.css").write_text("body{}")
    monkeypatch.setenv("TRACKSOUND_WEB", str(web))
    with TestClient(create_app(settings, job_workers=1)) as c:
        yield c


def test_module_worker_is_javascript_even_if_the_os_says_otherwise(web_client, monkeypatch):
    # Windows' registry often maps .mjs to text/plain; browsers then refuse the map's worker
    monkeypatch.setitem(mimetypes.types_map, ".mjs", "text/plain")
    r = web_client.get("/assets/worker-abc.mjs")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/javascript")


def test_assets_have_their_types_and_are_cached(web_client):
    js = web_client.get("/assets/app-abc.js")
    assert js.headers["content-type"].startswith("text/javascript")
    assert "immutable" in js.headers["cache-control"]
    assert web_client.get("/assets/app-abc.css").headers["content-type"].startswith("text/css")


def test_pages_fall_back_to_index_but_missing_files_404(web_client):
    page = web_client.get("/cases/c_123")
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    assert page.headers["cache-control"] == "no-cache"
    assert web_client.get("/assets/old-chunk-123.js").status_code == 404
    assert web_client.get("/api/nope").status_code == 404
