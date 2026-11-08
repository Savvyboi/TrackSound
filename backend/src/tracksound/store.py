"""Cases on disk: one folder per case, JSON documents written atomically.

Layout of a case folder (``<data>/cases/<case_id>/``):

    case.json            the Case document (see docs/API.md)
    original.<ext>       the uploaded file, untouched
    audio.wav            mono 4 kHz float32 copy every source reads (see audio_io)
    spectrogram.npz      whitened 40-450 Hz spectrogram (dopplertrack format)
    tiles/level_<z>.npy  uint8 display pyramid served to the browser
    aircraft/ ...        aircraft source state
    lightning/ ...       lightning source state
    enf/ ...             mains-hum (ENF) source state

Every source stores its own JSON with ``read_json`` / ``write_json`` under its subfolder.
Read-modify-write sequences must hold ``store.lock(case_id)``.
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from .config import Settings


class NotFound(KeyError):
    """A case or document does not exist (the API turns this into HTTP 404)."""


def new_id(prefix: str) -> str:
    """Short random id such as ``c_k3f9a2qd``."""
    alphabet = "abcdefghijkmnpqrstuvwxyz23456789"
    return prefix + "_" + "".join(secrets.choice(alphabet) for _ in range(8))


def _dump_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1, allow_nan=False, default=_json_default)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def _json_default(o):
    # numpy scalars and arrays sneak into documents easily; store them as plain JSON
    import numpy as np
    if isinstance(o, np.generic):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(f"not JSON serialisable: {type(o).__name__}")


CASE_FIELDS_EDITABLE = {"name", "notes", "date", "timezone", "region", "countries", "start_utc", "start_utc_source",
                        "temperature_c"}


class Store:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.root = settings.cases_dir
        self._locks: dict[str, threading.RLock] = {}
        self._locks_guard = threading.Lock()
        # deleted cases are moved here first; finish removing any a previous run left behind
        self.trash = settings.data_dir / ".trash"
        if self.trash.exists():
            for old in self.trash.iterdir():
                shutil.rmtree(old, ignore_errors=True)

    # ---- locking -------------------------------------------------------------------------
    def lock(self, case_id: str) -> threading.RLock:
        with self._locks_guard:
            return self._locks.setdefault(case_id, threading.RLock())

    # ---- paths ---------------------------------------------------------------------------
    def case_dir(self, case_id: str) -> Path:
        if not case_id or "/" in case_id or "\\" in case_id or case_id.startswith("."):
            raise NotFound(case_id)
        return self.root / case_id

    def path(self, case_id: str, relpath: str) -> Path:
        """Path of a file inside a case folder (it may not exist yet)."""
        base = self.case_dir(case_id).resolve()
        p = (base / relpath).resolve()
        if base not in p.parents and p != base:
            raise ValueError(f"path escapes the case folder: {relpath}")
        return p

    def audio_path(self, case_id: str) -> Path:
        return self.case_dir(case_id) / "audio.wav"

    def spectrogram_path(self, case_id: str) -> Path:
        return self.case_dir(case_id) / "spectrogram.npz"

    def original_path(self, case_id: str) -> Path | None:
        d = self.case_dir(case_id)
        for p in d.glob("original.*"):
            return p
        return None

    # ---- generic JSON documents -----------------------------------------------------------
    def read_json(self, case_id: str, relpath: str, default: Any = None) -> Any:
        p = self.path(case_id, relpath)
        if not p.exists():
            return default
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)

    def write_json(self, case_id: str, relpath: str, data: Any) -> None:
        _dump_atomic(self.path(case_id, relpath), data)

    def delete_file(self, case_id: str, relpath: str) -> None:
        p = self.path(case_id, relpath)
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()

    # ---- cases ---------------------------------------------------------------------------
    def list_cases(self) -> list[dict]:
        out = []
        if not self.root.exists():
            return out
        for d in self.root.iterdir():
            f = d / "case.json"
            if f.exists():
                try:
                    out.append(json.loads(f.read_text(encoding="utf-8")))
                except (OSError, json.JSONDecodeError):
                    continue
        out.sort(key=lambda c: c.get("updated_at", 0), reverse=True)
        return out

    def create_case(self, name: str) -> dict:
        cid = new_id("c")
        now = time.time()
        case = {
            "id": cid, "name": name.strip() or "Untitled case", "created_at": now, "updated_at": now, "notes": "",
            "date": None, "timezone": None, "region": None, "countries": [],
            "start_utc": None, "start_utc_source": None, "temperature_c": None,
            "recording": None,
        }
        self.case_dir(cid).mkdir(parents=True, exist_ok=False)
        self.write_json(cid, "case.json", case)
        return case

    def get_case(self, case_id: str) -> dict:
        case = self.read_json(case_id, "case.json") if self.case_dir(case_id).exists() else None
        if case is None:
            raise NotFound(case_id)
        case.setdefault("countries", [])          # cases from before the country filter
        return case

    def update_case(self, case_id: str, **fields) -> dict:
        """Merge fields into case.json (top-level keys; ``recording`` may be set too)."""
        with self.lock(case_id):
            case = self.get_case(case_id)
            case.update(fields)
            case["updated_at"] = time.time()
            self.write_json(case_id, "case.json", case)
            return case

    def update_recording(self, case_id: str, **fields) -> dict:
        with self.lock(case_id):
            case = self.get_case(case_id)
            rec = dict(case.get("recording") or {})
            rec.update(fields)
            case["recording"] = rec
            case["updated_at"] = time.time()
            self.write_json(case_id, "case.json", case)
            return case

    def touch(self, case_id: str) -> None:
        self.update_case(case_id)

    def delete_case(self, case_id: str) -> None:
        d = self.case_dir(case_id)
        if not (d / "case.json").exists():
            raise NotFound(case_id)
        # move the folder aside in one step, then remove it: if a file inside is still open
        # (Windows refuses), nothing is touched and the case stays whole instead of half-deleted
        self.trash.mkdir(parents=True, exist_ok=True)
        gone = self.trash / f"{case_id}-{secrets.token_hex(4)}"
        d.rename(gone)
        shutil.rmtree(gone, ignore_errors=True)
        with self._locks_guard:
            self._locks.pop(case_id, None)
