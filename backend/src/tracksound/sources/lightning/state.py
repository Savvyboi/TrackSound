"""Lightning state in the case folder (``<case>/lightning/``), via the Store's JSON helpers.

    onsets.json            ThunderOnset[]
    flashes.json           Flash[]
    strikes.json           { set: StrikeSet, strikes: Strike[] }  (catalogue events + manual ones)
    solves/index.json      LightningSolveSummary[] (newest first)
    solves/<id>.json       LightningSolve
    envelope.npz           cached thunder band envelopes (rebuilt when audio.wav changes)
    luma.npz               cached per-frame video luma (rebuilt when the upload changes)

Callers hold ``store.lock(case_id)`` around read-modify-write sequences.
"""
from __future__ import annotations

from typing import Any

from ...store import NotFound, Store, new_id

ONSETS = "lightning/onsets.json"
FLASHES = "lightning/flashes.json"
STRIKES = "lightning/strikes.json"
SOLVES_INDEX = "lightning/solves/index.json"
ENVELOPE = "lightning/envelope.npz"
LUMA = "lightning/luma.npz"


def solve_relpath(sid: str) -> str:
    if not sid or "/" in sid or "\\" in sid or sid.startswith("."):
        raise NotFound(sid)
    return f"lightning/solves/{sid}.json"


# ---- onsets and flashes ---------------------------------------------------------------------

def new_onset(t: float, sigma_s: float = 0.3, strength: float = 0.0, duration_s: float | None = None,
              origin: str = "detected", status: str = "candidate") -> dict:
    return {"id": new_id("lo"), "t": round(float(t), 3), "sigma_s": round(float(sigma_s), 3),
            "strength": round(float(strength), 2), "duration_s": None if duration_s is None else round(float(duration_s), 2),
            "origin": origin, "status": status}


def new_flash(t: float, strength: float = 0.0, origin: str = "video", status: str = "candidate") -> dict:
    return {"id": new_id("lf"), "t": round(float(t), 3), "strength": round(float(strength), 2),
            "origin": origin, "status": status}


def read_onsets(store: Store, cid: str) -> list[dict]:
    return store.read_json(cid, ONSETS, [])


def write_onsets(store: Store, cid: str, onsets: list[dict]) -> None:
    store.write_json(cid, ONSETS, sorted(onsets, key=lambda o: o["t"]))


def read_flashes(store: Store, cid: str) -> list[dict]:
    return store.read_json(cid, FLASHES, [])


def write_flashes(store: Store, cid: str, flashes: list[dict]) -> None:
    store.write_json(cid, FLASHES, sorted(flashes, key=lambda f: f["t"]))


def replace_detected(current: list[dict], found: list[dict], auto_origin: str, tol_s: float) -> tuple[list[dict], int]:
    """Drop earlier automatic candidates and rejections, keep manual and accepted items, add the
    new detections that are not within tol_s of a kept item. Returns (items, number added)."""
    kept = [x for x in current if x.get("origin") != auto_origin or x.get("status") == "accepted"]
    kept_t = sorted(x["t"] for x in kept)
    import bisect
    added = []
    for f in found:
        i = bisect.bisect_left(kept_t, f["t"])
        near = any(abs(kept_t[j] - f["t"]) <= tol_s for j in (i - 1, i) if 0 <= j < len(kept_t))
        if not near:
            added.append(f)
    return kept + added, len(added)


def find(items: list[dict], item_id: str) -> dict:
    for x in items:
        if x["id"] == item_id:
            return x
    raise NotFound(item_id)


# ---- strikes --------------------------------------------------------------------------------

def read_strikes(store: Store, cid: str) -> tuple[dict | None, list[dict]]:
    doc = store.read_json(cid, STRIKES, None)
    if not doc:
        return None, []
    return doc.get("set"), doc.get("strikes", [])


def write_strikes(store: Store, cid: str, strike_set: dict, strikes: list[dict]) -> None:
    store.write_json(cid, STRIKES, {"set": strike_set, "strikes": strikes})


def delete_strikes(store: Store, cid: str) -> None:
    store.delete_file(cid, STRIKES)


MANUAL = "manual"


def _with_manual(source: str, has_manual: bool) -> str:
    parts = [p for p in source.split("+") if p and p != MANUAL]
    if has_manual:
        parts.append(MANUAL)
    return "+".join(parts) or MANUAL


def replace_catalogue(store: Store, cid: str, source: str, strikes: list[dict], t0_utc: float | None = None,
                      t1_utc: float | None = None, bbox: dict | None = None) -> dict:
    """Store a fetched or imported catalogue, keeping the user's manual events. Returns the StrikeSet.
    Call with ``store.lock(cid)`` held."""
    from .catalogue import strike_set
    _, cur = read_strikes(store, cid)
    manual = [x for x in cur if x.get("source") == MANUAL]
    allx = sorted(strikes + manual, key=lambda x: x["t_utc"])
    sset = strike_set(_with_manual(source, bool(manual)), allx, t0_utc, t1_utc, bbox)
    if t0_utc is None and t1_utc is None and bbox is None:      # an import: describe the catalogue part only
        sset = {**strike_set(_with_manual(source, bool(manual)), strikes), "count": len(allx)}
    write_strikes(store, cid, sset, allx)
    return sset


def add_manual_strike(store: Store, cid: str, strike: dict) -> dict:
    """Add one user-supplied event (source "manual"). Call with ``store.lock(cid)`` held."""
    from .catalogue import strike_set
    sset, cur = read_strikes(store, cid)
    allx = sorted(cur + [strike], key=lambda x: x["t_utc"])
    if sset is None:
        sset = strike_set(MANUAL, allx)
    else:
        sset = {**sset, "source": _with_manual(sset["source"], True), "count": len(allx)}
    write_strikes(store, cid, sset, allx)
    return strike


def delete_strike(store: Store, cid: str, sid: str) -> None:
    """Remove one event. Call with ``store.lock(cid)`` held."""
    sset, cur = read_strikes(store, cid)
    if not any(x["id"] == sid for x in cur):
        raise NotFound(sid)
    rest = [x for x in cur if x["id"] != sid]
    has_manual = any(x.get("source") == MANUAL for x in rest)
    if not rest and (sset is None or sset["source"] == MANUAL):
        delete_strikes(store, cid)
        return
    sset = {**(sset or {}), "source": _with_manual((sset or {}).get("source", MANUAL), has_manual), "count": len(rest)}
    write_strikes(store, cid, sset, rest)


# ---- solves ---------------------------------------------------------------------------------

def summary(doc: dict) -> dict:
    return {"id": doc["id"], "created_at": doc["created_at"], "mode": doc["mode"], "best": doc.get("best")}


def list_solves(store: Store, cid: str) -> list[dict]:
    return store.read_json(cid, SOLVES_INDEX, [])


def save_solve(store: Store, cid: str, doc: dict) -> None:
    store.write_json(cid, solve_relpath(doc["id"]), doc)
    with store.lock(cid):
        idx = [s for s in list_solves(store, cid) if s["id"] != doc["id"]]
        idx.insert(0, summary(doc))
        idx.sort(key=lambda s: s["created_at"], reverse=True)
        store.write_json(cid, SOLVES_INDEX, idx)


def get_solve(store: Store, cid: str, sid: str) -> dict:
    doc = store.read_json(cid, solve_relpath(sid), None)
    if doc is None:
        raise NotFound(sid)
    return doc


def delete_solve(store: Store, cid: str, sid: str) -> None:
    rel = solve_relpath(sid)
    with store.lock(cid):
        idx = list_solves(store, cid)
        if not any(s["id"] == sid for s in idx) and store.read_json(cid, rel, None) is None:
            raise NotFound(sid)
        store.write_json(cid, SOLVES_INDEX, [s for s in idx if s["id"] != sid])
        store.delete_file(cid, rel)


# ---- overview and report --------------------------------------------------------------------

def overview(store: Store, cid: str) -> dict[str, Any]:
    """GET /api/cases/{id}/lightning."""
    strike_set, _ = read_strikes(store, cid)
    return {"onsets": read_onsets(store, cid), "flashes": read_flashes(store, cid), "strikes": strike_set,
            "solves": list_solves(store, cid)}


def report(store: Store, cid: str) -> dict[str, Any]:
    """The lightning part of the case report: state counts, the strike set and the best solve.

    ``best_solve`` is the highest-scoring solve's summary (all solves are listed too).
    """
    ov = overview(store, cid)
    solves = ov["solves"]
    best = max((s for s in solves if s.get("best")), key=lambda s: s["best"]["score"], default=None)
    return {
        "onsets": ov["onsets"], "flashes": ov["flashes"], "strikes": ov["strikes"], "solves": solves,
        "n_onsets_accepted": sum(o["status"] == "accepted" for o in ov["onsets"]),
        "n_flashes_accepted": sum(f["status"] == "accepted" for f in ov["flashes"]),
        "best_solve": best,
    }
