"""ENF work on a case: extraction and matching jobs, and the JSON documents of docs/API.md.

Case files (under ``<case>/enf/``):

    analysis.json         the EnfAnalysis (with the trace)
    matches/<em_id>.json  one EnfMatch each, with its overlay
"""
from __future__ import annotations

import math
import time
from typing import Callable

import numpy as np

from ...store import NotFound, Store, new_id
from . import references
from .extract import AudioSource, EnfParams, EnfTrace, extract_trace
from .match import MatchParams, match_trace, params_for_trace

ANALYSIS = "enf/analysis.json"
MATCH_DIR = "enf/matches"
MAX_WINDOW_DAYS = 31
REF_PAD_S = 900            # extra reference on both sides (edge effects of smoothing/high-pass)

Progress = Callable[[float, str], None]


# ---- documents ------------------------------------------------------------------------------------

def _nullable(x: np.ndarray, nd: int = 5) -> list[float | None]:
    return [round(float(v), nd) if np.isfinite(v) else None for v in x]


def analysis_doc(tr: EnfTrace, p: EnfParams, requested: str | int) -> dict:
    return {
        "created_at": time.time(),
        "nominal_hz": tr.nominal_hz,
        "confidence": round(float(tr.confidence), 3),
        "harmonics": [{"n": h.n, "freq_hz": h.freq_hz, "snr_db": h.snr_db, "used": h.used} for h in tr.harmonics],
        "trace": {"t0": tr.t0, "dt": tr.dt, "f": _nullable(tr.f)},
        "quality": round(tr.quality, 4),
        "notes": tr.notes,
        # extras (not in the contract): what produced the trace
        "requested_nominal": requested,
        "window_s": p.window_s,
    }


def _trace_array(analysis: dict) -> np.ndarray:
    return np.array([np.nan if v is None else float(v) for v in analysis["trace"]["f"]], dtype=float)


def summary(match: dict) -> dict:
    """An EnfMatch without its overlay (for lists)."""
    return {k: v for k, v in match.items() if k != "overlay"}


# ---- extraction -------------------------------------------------------------------------------------

def run_extract(store: Store, case_id: str, nominal: str | int = "auto", progress: Progress | None = None,
                p: EnfParams = EnfParams()) -> dict:
    progress = progress or (lambda f, m: None)
    path = store.audio_path(case_id)
    if not path.exists():
        raise ValueError("the recording has not been processed yet")
    tr = extract_trace(AudioSource(path=path), nominal=nominal, p=p, progress=progress)
    doc = analysis_doc(tr, p, nominal)
    store.write_json(case_id, ANALYSIS, doc)
    return doc


# ---- matching ------------------------------------------------------------------------------------------

def match_params(analysis: dict, f: np.ndarray) -> MatchParams:
    """Matching parameters for a trace: smoothing equal to the extraction window, preprocessing
    chosen from the trace quality."""
    return params_for_trace(f, float(analysis.get("window_s", EnfParams().window_s)))


def check_match_request(store: Store, case_id: str, reference_id: str, t0_utc: float, t1_utc: float) -> dict:
    """Validate a match request before queueing it; returns the analysis. Raises ValueError/NotFound."""
    analysis = store.read_json(case_id, ANALYSIS)
    if analysis is None:
        raise ValueError("extract the ENF first (POST .../enf/extract)")
    if analysis.get("nominal_hz") is None:
        raise ValueError("no mains hum was found in this recording, so there is nothing to match")
    info = references.get_info(store.settings, reference_id)
    if info.nominal_hz != analysis["nominal_hz"]:
        raise ValueError(f"the recording's hum is from a {analysis['nominal_hz']} Hz grid but '{info.name}' is a "
                         f"{info.nominal_hz} Hz grid")
    if not (math.isfinite(t0_utc) and math.isfinite(t1_utc)) or t1_utc < t0_utc:
        raise ValueError("t1_utc must not be before t0_utc")
    if t1_utc - t0_utc > MAX_WINDOW_DAYS * 86400:
        raise ValueError(f"search at most {MAX_WINDOW_DAYS} days at a time")
    if t0_utc < 0 or t1_utc > time.time() + 86400:
        raise ValueError("the search window must lie between 1970 and now")
    return analysis


def run_match(store: Store, case_id: str, reference_id: str, t0_utc: float, t1_utc: float,
              progress: Progress | None = None) -> dict:
    """Load the reference for [t0_utc, t1_utc] (+ the recording's length), correlate, save an EnfMatch."""
    progress = progress or (lambda f, m: None)
    analysis = check_match_request(store, case_id, reference_id, t0_utc, t1_utc)
    info = references.get_info(store.settings, reference_id)
    f = _trace_array(analysis)
    rec_t0 = float(analysis["trace"]["t0"])
    dt_ = float(analysis["trace"].get("dt", 1.0))
    if abs(dt_ - 1.0) > 1e-9:
        raise ValueError("only 1 s traces can be matched")
    span_a = math.floor(t0_utc + rec_t0) - REF_PAD_S
    span_b = math.ceil(t1_utc + rec_t0 + len(f)) + REF_PAD_S
    series = references.load_reference(store.settings, reference_id, span_a, span_b,
                                       lambda x, m: progress(0.8 * x, m))
    progress(0.85, "Correlating")
    notes = list(series.notes)
    if not np.isfinite(series.values).any():
        res_best, res = None, None
        notes.append(f"'{info.name}' has no data between the requested times.")
    else:
        res = match_trace(f, rec_t0, series.values.astype(float), series.t0, t0_utc, t1_utc, match_params(analysis, f))
        notes = res.notes + notes
        res_best = None if res.start_utc is None else {"start_utc": round(res.start_utc, 2), "corr": res.corr, "z": res.z}
    doc = {
        "id": new_id("em"), "created_at": time.time(), "reference_id": reference_id,
        "t0_utc": float(t0_utc), "t1_utc": float(t1_utc),
        "best": res_best,
        "top": res.top if res else [],
        "overlay": res.overlay if res else None,
        "notes": notes,
        # extras (not in the contract)
        "reference_name": info.name, "reference_valid_fraction": round(series.valid_fraction(), 4),
        "n_offsets": res.n_offsets if res else 0, "overlap_s": res.overlap_s if res else 0,
        "method": res.method if res else None,
        "analysis_created_at": analysis.get("created_at"),
    }
    store.write_json(case_id, f"{MATCH_DIR}/{doc['id']}.json", doc)
    return doc


def list_matches(store: Store, case_id: str) -> list[dict]:
    d = store.path(case_id, MATCH_DIR)
    out = []
    if d.exists():
        for p in d.glob("em_*.json"):
            doc = store.read_json(case_id, f"{MATCH_DIR}/{p.name}")
            if doc:
                out.append(summary(doc))
    out.sort(key=lambda m: m.get("created_at", 0), reverse=True)
    return out


def _match_rel(mid: str) -> str:
    if not mid.startswith("em_") or not mid[3:].isalnum():
        raise NotFound(mid)
    return f"{MATCH_DIR}/{mid}.json"


def get_match(store: Store, case_id: str, mid: str) -> dict:
    doc = store.read_json(case_id, _match_rel(mid))
    if doc is None:
        raise NotFound(mid)
    return doc


def delete_match(store: Store, case_id: str, mid: str) -> None:
    rel = _match_rel(mid)
    if not store.path(case_id, rel).exists():
        raise NotFound(mid)
    store.delete_file(case_id, rel)
