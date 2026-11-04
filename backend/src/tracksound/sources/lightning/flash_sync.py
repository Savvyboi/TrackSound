"""Recording clock from video flashes.

Light from a strike arrives effectively instantly, so a flash seen at recording time ``f_k``
from strike j means ``start_utc = t_j - f_k``. Every (flash, strike) pair votes for such an
offset; the offset that makes the most *distinct* flashes coincide with strikes (within a
tolerance covering the video frame period and flash duration) wins. That is robust to
flashes the catalogue missed, strikes not seen on video, and false flash detections.
"""
from __future__ import annotations

import math

import numpy as np

from .catalogue import StrikeArrays
from .geo import LocalFrame, frame_for_bbox, in_bbox

MAX_VOTES = 3_000_000


def _counts(flash_t: np.ndarray, S: np.ndarray, cand: np.ndarray, tol: float) -> np.ndarray:
    count = np.zeros(len(cand))
    for f in flash_t:
        lo = np.searchsorted(S, f + cand - tol, side="left")
        hi = np.searchsorted(S, f + cand + tol, side="right")
        count += hi > lo
    return count


def _refine(flash_t: np.ndarray, S: np.ndarray, T: float, tol: float) -> tuple[float, int, float]:
    """(median offset of the coinciding pairs, number of flashes matched, rms residual)."""
    offs = []
    for f in flash_t:
        lo = np.searchsorted(S, f + T - tol, side="left")
        hi = np.searchsorted(S, f + T + tol, side="right")
        if hi > lo:
            k = lo + int(np.argmin(np.abs(S[lo:hi] - (f + T))))
            offs.append(S[k] - f)
    if not offs:
        return T, 0, math.inf
    Tm = float(np.median(offs))
    return Tm, len(offs), float(np.sqrt(np.mean((np.array(offs) - Tm) ** 2)))


def sync_candidates(flash_t: np.ndarray, sa: StrikeArrays, tol: float, region: dict, max_range_m: float,
                    max_cands: int = 40, date: str | None = None) -> tuple[list[dict], dict]:
    """Candidate clocks ``[{T, count, rms}]`` (best first) and ``{note, best_count}``."""
    flash_t = np.sort(np.asarray(flash_t, dtype=np.float64))
    S = sa.t
    if len(flash_t) == 0 or len(S) == 0:
        return [], {"note": "", "best_count": 0}
    sel = np.arange(len(S))
    if date:
        import datetime as dt
        d0 = dt.datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()
        sel = np.flatnonzero((S >= d0 - 7200) & (S <= d0 + 86400 + 7200))
    if len(sel) * len(flash_t) > MAX_VOTES:       # keep the strikes nearest the region
        fr = frame_for_bbox(region)
        y, u = fr.to_yu(sa.lat[sel], sa.lon[sel])
        d = fr.dist(y, u, 0.0, 0.0)
        sel = np.sort(sel[np.argsort(d, kind="stable")[: max(1, MAX_VOTES // len(flash_t))]])
    S_sel = S[sel]
    q = tol / 2.0
    votes = (S_sel[None, :] - flash_t[:, None]).ravel()
    cand = np.unique(np.round(votes / q)) * q
    count = _counts(flash_t, S, cand, tol)
    best_count = int(count.max()) if len(count) else 0
    out: list[dict] = []
    if best_count >= 2:
        floor = max(2, best_count - 1)
        order = np.argsort(-count, kind="stable")
        for i in order:
            if count[i] < floor or len(out) >= min(max_cands, 6):
                break
            T, n, rms = _refine(flash_t, S, float(cand[i]), tol)
            if any(abs(T - o["T"]) <= 2 * tol for o in out):
                continue
            out.append({"T": T, "count": n, "rms": rms})
        out.sort(key=lambda o: (-o["count"], o["rms"]))
        b = out[0]
        note = (f"Clock from video flashes: {b['count']} of {len(flash_t)} accepted flashes coincide with catalogued "
                f"strikes (spread {b['rms'] * 1000:.0f} ms).")
        if len(out) > 1 and out[1]["count"] == b["count"]:
            note += f" {len(out)} different clock offsets fit equally well; the thunder decides between them."
        return out, {"note": note, "best_count": best_count}
    # a single usable flash: every nearby strike could be its source
    fr = frame_for_bbox(region)
    near = np.flatnonzero(in_bbox(sa.lat, sa.lon, region))
    if len(near) == 0:
        return [], {"note": "", "best_count": best_count}
    y, u = fr.to_yu(sa.lat[near], sa.lon[near])
    near = near[np.argsort(fr.dist(y, u, 0.0, 0.0), kind="stable")]
    for j in near:
        for f in flash_t:
            T = float(S[j] - f)
            if any(abs(T - o["T"]) <= 2 * tol for o in out):
                continue
            out.append({"T": T, "count": 1, "rms": 0.0})
            if len(out) >= max_cands:
                break
        if len(out) >= max_cands:
            break
    note = (f"Only one flash can be matched at a time, so {len(out)} catalogued strikes in the region were tried as "
            "its source; the thunder decides between them.")
    return out, {"note": note, "best_count": best_count}


def flash_matches(flashes: list[dict], sa: StrikeArrays, T: float, tol: float, frame: LocalFrame,
                  y: float, u: float) -> list[dict]:
    """``[{flash_id, strike_id, residual_s}]`` at clock T (one strike per flash, nearest in time,
    then nearest to the listener)."""
    out: dict[int, dict] = {}
    for f in flashes:
        t = f["t"] + T
        lo = np.searchsorted(sa.t, t - tol, side="left")
        hi = np.searchsorted(sa.t, t + tol, side="right")
        if hi <= lo:
            continue
        js = np.arange(lo, hi)
        sy, su = frame.to_yu(sa.lat[js], sa.lon[js])
        key = np.abs(sa.t[js] - t) + 1e-9 * frame.dist(sy, su, y, u)
        j = int(js[np.argmin(key)])
        res = float(t - sa.t[j])
        if j not in out or abs(res) < abs(out[j]["residual_s"]):
            out[j] = {"flash_id": f["id"], "strike_id": sa.ids[j], "residual_s": round(res, 4)}
    return sorted(out.values(), key=lambda m: m["residual_s"])
