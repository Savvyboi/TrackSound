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

from ...physics import pad_bbox
from .catalogue import StrikeArrays
from .geo import LocalFrame, frame_for_bbox, in_bbox

MAX_VOTES = 3_000_000
SINGLE_SIGMA_S = 0.1      # one flash vs one strike time (video frame, flash duration, catalogue timing)
MIN_SIGMA_S = 0.02


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
    """Candidate clocks ``[{T, count, rms, sigma}]`` (best first) and ``{note, best_count}``.

    ``sigma`` is the clock's uncertainty from the matched flashes (their spread over sqrt(n-1),
    at least 20 ms; 0.1 s when a single flash is matched).
    """
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
        # every offset with (nearly) the best count, one per cluster, ranked by count then spread
        floor = max(2, best_count - 1)
        keep = np.flatnonzero(count >= floor)
        for i in keep[np.argsort(-count[keep], kind="stable")]:
            T, n, rms = _refine(flash_t, S, float(cand[i]), tol)
            if n >= floor and not any(abs(T - o["T"]) <= 2 * tol for o in out):
                out.append({"T": T, "count": n, "rms": rms, "sigma": max(MIN_SIGMA_S, rms / math.sqrt(n - 1))})
        out.sort(key=lambda o: (-o["count"], o["rms"]))
        out = out[:min(max_cands, 6)]
        b = out[0]
        note = (f"Clock from video flashes: {b['count']} of {len(flash_t)} accepted flashes coincide with catalogued "
                f"events (spread {b['rms'] * 1000:.0f} ms).")
        if len(out) > 1 and out[1]["count"] == b["count"]:
            note += f" {len(out)} different clock offsets fit equally well; the sound decides between them."
        return out, {"note": note, "best_count": best_count}
    # a single usable flash: every event within range of the region could be its source
    fr = frame_for_bbox(region)
    near = np.flatnonzero(in_bbox(sa.lat, sa.lon, pad_bbox(region, max_range_m)))
    if len(near) == 0:
        return [], {"note": "", "best_count": best_count}
    y, u = fr.to_yu(sa.lat[near], sa.lon[near])
    near = near[np.argsort(fr.dist(y, u, 0.0, 0.0), kind="stable")]
    for j in near:
        for f in flash_t:
            T = float(S[j] - f)
            if any(abs(T - o["T"]) <= 2 * tol for o in out):
                continue
            out.append({"T": T, "count": 1, "rms": 0.0, "sigma": SINGLE_SIGMA_S})
            if len(out) >= max_cands:
                break
        if len(out) >= max_cands:
            break
    note = ("Only one flash can be matched at a time, so " +
            (f"{len(out)} catalogued events near the region were tried as its source; the sound decides between them."
             if len(out) != 1 else "the clock comes from the one catalogued event near the region."))
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
