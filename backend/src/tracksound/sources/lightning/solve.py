"""Lightning solve: where (and when) was the recording made?

Unknowns: listener position x and the recording clock T (UTC of recording time 0). Each
thunder onset matched to a catalogued strike is a range measurement once T is known; with
three or more matches T falls out too (like GPS with an unknown receiver clock). See
``solve_core`` for the observation model and the score.

Modes (docs/API.md): ``known_start`` searches T within start_utc +- start_tol_s;
``flash_sync`` takes T from video flashes coinciding with strike times (light is instant);
``free`` searches T over the whole catalogue (Hough voting per grid point).

Search: a coarse grid over the region (at most a few thousand points, tolerance widened by
the cell size) finds clock values by voting and scores them; the best distinct peaks are
re-searched on a fine grid (``spacing_m``), then the best point is polished by weighted
least squares on its matched pairs, which also gives the uncertainty.
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Callable

import numpy as np

from ...physics import pad_bbox
from .catalogue import StrikeArrays, describe_sigma, to_arrays
from .geo import bbox_size_m, frame_for_bbox, grid_points, in_bbox, square_bbox
from .flash_sync import flash_matches, sync_candidates
from .solve_core import MISS_WEIGHT, MISS_WEIGHT_MANUAL, Problem

ProgressFn = Callable[[float, str | None], None]

COARSE_POINTS = {"known_start": 6000, "flash_sync": 3000, "free": 2500}
FINE_SIDE = 70                 # fine windows are at most 70 x 70 points
HEAT_MAX = 20000
FLASH_TOL_S = 0.25             # flash time vs strike time
FLASH_WEIGHT = 1.0             # a coinciding flash counts like a matched onset
MAX_SYNC_CANDIDATES = 40
POLISH_ROUNDS = 3              # least-squares polish, re-associating the matches between rounds
POLISH_DROP_ABS = 0.5          # ... kept unless the score drops by more than this
POLISH_DROP_REL = 0.05         # ... or this fraction of it
AMBIGUOUS_ALTERNATIVES = 1.5   # mean other strikes fitting each matched onset: above this, matches are not distinctive


def choose_mode(start_utc: float | None, n_accepted_flashes: int, use_flashes: bool) -> str:
    if start_utc is not None:
        return "known_start"
    if use_flashes and n_accepted_flashes > 0:
        return "flash_sync"
    return "free"


def select_onsets(onsets: list[dict]) -> tuple[list[dict], str | None]:
    """Accepted onsets; if none are accepted, every unreviewed candidate (with a note)."""
    acc = [o for o in onsets if o.get("status") == "accepted"]
    if acc:
        return acc, None
    cand = [o for o in onsets if o.get("status") == "candidate"]
    if cand:
        return cand, (f"No onsets were accepted, so all {len(cand)} detected candidates were used; "
                      "accepting the real ones and rejecting the rest makes the solve more reliable.")
    return [], None


def _iso(t: float) -> str:
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


class _Search:
    """Coarse-to-fine search over (x, T) for one problem."""

    def __init__(self, prob: Problem, region: dict, spacing_m: float, mode: str, progress: ProgressFn):
        self.p, self.region, self.spacing, self.mode, self.progress = prob, region, spacing_m, mode, progress
        ew, ns = bbox_size_m(region)
        self.spacing_c = max(spacing_m, math.sqrt(max(ew * ns, 1.0) / COARSE_POINTS[mode]))
        self.heat: list[tuple[np.ndarray, np.ndarray, np.ndarray, float]] = []   # (y, u, score, cell size m)

    def sig_grid(self, spacing: float) -> float:
        return 0.5 * spacing / self.p.c

    def bin_w(self, sig_grid: float, width: float) -> float:
        p = self.p
        typ = math.sqrt(float(np.median(p.sk)) ** 2 + float(np.median(p.sp)) ** 2 + sig_grid ** 2 + 0.05 ** 2)
        return float(np.clip(0.5 * typ, 0.05, 2.0)) if width > 0.4 * typ else max(0.02, width / 4)

    def scan(self, gy, gu, intervals: list[tuple[float, float]], spacing: float, n_peaks: int,
             bonus: list[float] | None = None, progress: tuple[float, float] | None = None) -> dict:
        """Best (score, T, n_matched, interval index) per point over the clock intervals.

        ``progress`` maps this scan onto a (start, end) fraction of the job's progress."""
        n = len(gy)
        best = {"score": np.full(n, -np.inf), "T": np.full(n, np.nan), "n": np.zeros(n, np.int64),
                "iv": np.full(n, -1)}
        sg = self.sig_grid(spacing)
        steps, done = len(intervals) * n_peaks * 2, 0
        for ii, (lo, hi) in enumerate(intervals):
            bw = self.bin_w(sg, hi - lo)
            Tv, _ = self.p.vote_clock(gy, gu, lo, hi, sg, bw, n_peaks)
            extra = bonus[ii] if bonus else 0.0
            for c in range(Tv.shape[1]):
                T = Tv[:, c]
                for _ in range(2):             # score, then one mean-shift step on the clock
                    if progress:
                        self.progress(progress[0] + (progress[1] - progress[0]) * done / steps,
                                      f"Searching {len(gy)} grid points ({self.mode.replace('_', ' ')})")
                    done += 1
                    ps = self.p.evaluate(gy, gu, T, sg)
                    s = ps.score + extra
                    upd = np.isfinite(T) & (s > best["score"])
                    best["score"][upd], best["T"][upd], best["n"][upd], best["iv"][upd] = s[upd], T[upd], ps.n_matched[upd], ii
                    T = np.clip(T + np.clip(ps.dT, -3 * sg - bw, 3 * sg + bw), lo, hi)
        best["score"][~np.isfinite(best["score"])] = 0.0
        return best

    def coarse(self, intervals, n_peaks: int, bonus=None):
        frame = self.p.frame
        gy, gu, _, _ = grid_points(self.region, self.spacing_c, frame)
        res = self.scan(gy, gu, intervals, self.spacing_c, n_peaks, bonus, progress=(0.05, 0.4))
        self.heat.append((gy, gu, res["score"], self.spacing_c))
        return gy, gu, res

    def peaks(self, gy, gu, res, max_n: int, sep_m: float, sep_T: float) -> list[dict]:
        order = np.argsort(-res["score"], kind="stable")
        out: list[dict] = []
        for i in order[: 20000]:
            s = float(res["score"][i])
            if s <= 0.05 or len(out) >= max_n:
                break
            far = all(float(self.p.frame.dist(gy[i], gu[i], q["y"], q["u"])) >= sep_m or abs(res["T"][i] - q["T"]) > sep_T
                      for q in out)
            if far:
                out.append({"y": float(gy[i]), "u": float(gu[i]), "T": float(res["T"][i]), "score": s,
                            "n": int(res["n"][i]), "iv": int(res["iv"][i])})
        return out

    def refine(self, peak: dict, interval: tuple[float, float], bonus: float = 0.0) -> dict:
        frame = self.p.frame
        half = max(2.5 * self.spacing_c, 1500.0)
        lat, lon = frame.to_latlon(peak["y"], peak["u"])
        box = square_bbox(float(lat), float(lon), half)
        spacing = max(self.spacing, 2 * half / FINE_SIDE)
        gy, gu, _, _ = grid_points(box, spacing, frame)
        w = max(3 * self.sig_grid(self.spacing_c) + 2 * self.bin_w(self.sig_grid(self.spacing_c), 1e9), 1.0)
        lo, hi = max(interval[0], peak["T"] - w), min(interval[1], peak["T"] + w)
        if hi <= lo:
            lo, hi = interval
        res = self.scan(gy, gu, [(lo, hi)], spacing, 1, [bonus])
        self.heat.append((gy, gu, res["score"], spacing))
        i = int(np.argmax(res["score"]))
        return {"y": float(gy[i]), "u": float(gu[i]), "T": float(res["T"][i]), "score": float(res["score"][i]),
                "n": int(res["n"][i]), "spacing": spacing, "gy": gy, "gu": gu, "res": res, "interval": (lo, hi),
                "bonus": bonus}

    def polish(self, r: dict, T_prior, interval: tuple[float, float]) -> dict:
        """Least-squares polish of a refined peak (re-associating a few times).

        The soft-count score finds the right basin but, with a one-sigma kernel, is an
        inefficient estimator; weighted least squares on the matched pairs is the efficient
        one. The polished point is kept unless it loses a sizeable part of the score (which
        would mean the association changed for the worse)."""
        T_grid = float(np.clip(r["T"], *interval))
        ps0 = self.p.evaluate(np.array([r["y"]]), np.array([r["u"]]), np.array([T_grid]), 0.0)
        s0 = float(ps0.score[0]) + r.get("bonus", 0.0)
        y, u, T = r["y"], r["u"], T_grid
        cov = None
        for _ in range(POLISH_ROUNDS):
            y2, u2, T2, cov2, _ = self.p.polish(y, u, T, T_prior)
            if cov2 is None:
                break
            y, u, T, cov = y2, u2, float(np.clip(T2, *interval)), cov2
        ps = self.p.evaluate(np.array([y]), np.array([u]), np.array([T]), 0.0)
        s = float(ps.score[0]) + r.get("bonus", 0.0)
        if cov is not None and s >= s0 - max(POLISH_DROP_ABS, POLISH_DROP_REL * abs(s0)):
            return {**r, "y": y, "u": u, "T": T, "score": s, "n": int(ps.n_matched[0]), "cov": cov}
        if cov is None and T_prior is not None:
            # too few matches to determine the clock: it comes from the prior (flash or given start)
            T_p = float(np.clip(T_prior[0], *interval))
            pp = self.p.evaluate(np.array([r["y"]]), np.array([r["u"]]), np.array([T_p]), 0.0)
            sp = float(pp.score[0]) + r.get("bonus", 0.0)
            if sp >= s0 - 0.25:
                return {**r, "T": T_p, "score": sp, "n": int(pp.n_matched[0]), "cov": None}
        return {**r, "T": T_grid, "score": s0, "n": int(ps0.n_matched[0]), "cov": None}

    def heat_points(self) -> list[dict]:
        """Grid scores for the map: the coarse grid over the region plus the finer windows around
        the peaks, each point with its cell size ``cell_m`` (finer cells drawn over coarser)."""
        if not self.heat:
            return []
        y = np.concatenate([h[0] for h in self.heat])
        u = np.concatenate([h[1] for h in self.heat])
        s = np.concatenate([h[2] for h in self.heat])
        cell = np.concatenate([np.full(len(h[0]), h[3]) for h in self.heat])
        keep = np.flatnonzero(s > 0.05)
        if len(keep) > HEAT_MAX:
            keep = keep[np.argsort(-s[keep], kind="stable")[:HEAT_MAX]]
        lat, lon = self.p.frame.to_latlon(y[keep], u[keep])
        return [{"lat": round(float(a), 5), "lon": round(float(b), 5), "score": round(float(c), 3),
                 "cell_m": round(float(m), 1)} for a, b, c, m in zip(lat, lon, s[keep], cell[keep])]


def _uncertainty(r: dict, spacing: float, frame, mean_alt: float = 0.0) -> float:
    """Radius (m) the true position is probably within (about 70-90 % in simulations).

    The larger of: the least-squares 1-sigma semi-major axis, scaled up by how many other
    strikes could explain each matched onset (misassociation risk), and the farthest
    fine-grid point whose score is within 1.15 of the best (the region of nearly equal fit).
    """
    fisher = 0.0
    if r.get("cov") is not None:
        ev = np.linalg.eigvalsh(np.asarray(r["cov"])[:2, :2])
        fisher = math.sqrt(max(float(ev.max()), 0.0)) * (1.0 + mean_alt)
    res, gy, gu = r["res"], r["gy"], r["gu"]
    near = res["score"] >= r["score"] - 1.15
    region = float(np.max(frame.dist(gy[near], gu[near], r["y"], r["u"]))) if near.any() else float(spacing)
    return float(max(fisher, region, 0.5 * spacing))


def solve(onsets: list[dict], flashes: list[dict], strikes: list[dict], *, c: float, duration_s: float,
          region: dict | None, date: str | None = None, start_utc: float | None = None, start_tol_s: float = 5.0,
          use_flashes: bool = True, spacing_m: float = 250.0, max_range_km: float = 25.0,
          progress: ProgressFn | None = None) -> dict:
    """Run a solve; returns the LightningSolve body (mode, best, candidates, heat, matches,
    flash_matches, ranges, notes). Raises ValueError for unusable input."""
    report = progress or (lambda f, m=None: None)
    notes: list[str] = []
    used, note = select_onsets(onsets)
    if note:
        notes.append(note)
    if not used:
        raise ValueError("no onsets (thunder or other bangs) to match: run 'detect thunder' or add onsets by hand")
    if not strikes:
        raise ValueError("no events to match: fetch GLM lightning or USGS events, import a catalogue, "
                         "or add a known event first")
    acc_flashes = [f for f in flashes if f.get("status") == "accepted"] if use_flashes else []
    mode = choose_mode(start_utc, len(acc_flashes), use_flashes)
    sa = to_arrays(strikes)
    R = float(max_range_km) * 1000.0
    if region is None:
        region = _region_from_strikes(sa, start_utc, duration_s, R)
        notes.append("The case has no search region, so the area around the catalogued strikes was searched.")
    frame = frame_for_bbox(region)
    manual_only = all(o.get("origin") == "manual" for o in used)
    prob = Problem([o["t"] for o in used], [o.get("sigma_s") or 0.3 for o in used], [o["id"] for o in used],
                   sa, c, duration_s, frame, R, MISS_WEIGHT_MANUAL if manual_only else MISS_WEIGHT)
    prob.on_block = _throttled(lambda: report(None, None))    # lets a job be cancelled mid-pass
    search = _Search(prob, region, spacing_m, mode, report)

    # ---- clock intervals for the mode ----
    T_prior = None
    bonus = None
    sync_info = None
    sync_sigma: list[float] = []
    if mode == "known_start":
        tol = max(0.0, float(start_tol_s))
        half = max(tol, 0.01)
        intervals = [(start_utc - half, start_utc + half)]
        T_prior = (start_utc, max(tol, 0.5))
        notes.append(f"Recording start taken as {_iso(start_utc)} (searched +-{tol:g} s).")
    elif mode == "flash_sync":
        cands, sync_info = sync_candidates(np.array([f["t"] for f in acc_flashes]), sa, FLASH_TOL_S, region, R,
                                           MAX_SYNC_CANDIDATES, date)
        if not cands:
            mode = "free"
            notes.append("No accepted flash coincides with a catalogued event, so the clock was searched freely.")
        else:
            intervals = [(x["T"] - 2 * x["sigma"], x["T"] + 2 * x["sigma"]) for x in cands]
            bonus = [FLASH_WEIGHT * x["count"] for x in cands]
            sync_sigma = [x["sigma"] for x in cands]
            notes.append(sync_info["note"])
            search = _Search(prob, region, spacing_m, "flash_sync", report)
    if mode == "free":
        lo, hi = float(sa.t.min()) - duration_s - 10.0, float(sa.t.max()) + prob.tt_max + 10.0
        if date:
            d0 = dt.datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()
            lo, hi = max(lo, d0 - duration_s - 3600.0), min(hi, d0 + 86400.0 + 3600.0)
        if hi <= lo:
            raise ValueError("no catalogued events on the case date: check the date or the catalogue")
        intervals = [(lo, hi)]
        search = _Search(prob, region, spacing_m, "free", report)
        notes.append("The recording clock was searched together with the position (no start time or accepted "
                     "flashes); this needs at least 3 well-matched onsets.")

    # ---- coarse ----
    report(0.05, f"Coarse search ({mode})")
    n_peaks = 3 if mode == "free" else 1
    gy, gu, cres = search.coarse(intervals, n_peaks, bonus)
    sep_m = max(3 * search.spacing_c, 2000.0)
    sep_T = max(5.0, 3 * search.sig_grid(search.spacing_c))
    peaks = search.peaks(gy, gu, cres, 6 if mode == "free" else 4, sep_m, sep_T)
    if not peaks:
        return _empty_result(mode, notes, search, prob)
    # ---- refine + polish ----
    refined = []
    for n, pk in enumerate(peaks):
        report(0.4 + 0.5 * n / len(peaks), f"Refining candidate {n + 1} of {len(peaks)}")
        iv = intervals[pk["iv"]] if pk["iv"] >= 0 else intervals[0]
        b = bonus[pk["iv"]] if bonus and pk["iv"] >= 0 else 0.0
        r = search.refine(pk, iv, b)
        prior = T_prior if mode == "known_start" else (
            (0.5 * (iv[0] + iv[1]), sync_sigma[pk["iv"]]) if mode == "flash_sync" else None)
        refined.append(search.polish(r, prior, iv))
    refined.sort(key=lambda r: -r["score"])
    best = refined[0]
    report(0.95, "Collecting results")
    return _result(mode, best, refined, search, prob, acc_flashes, notes, sa, region)


def _throttled(fn: Callable[[], None], every_s: float = 0.25) -> Callable[[], None]:
    import time
    last = [0.0]

    def call() -> None:
        now = time.monotonic()
        if now - last[0] >= every_s:
            last[0] = now
            fn()
    return call


def _region_from_strikes(sa: StrikeArrays, start_utc, duration_s, R) -> dict:
    sel = np.ones(len(sa), bool)
    if start_utc is not None:
        sel = (sa.t >= start_utc - R / 300.0 - 60) & (sa.t <= start_utc + duration_s + 60)
        if not sel.any():
            sel = np.ones(len(sa), bool)
    box = {"lat_min": float(sa.lat[sel].min()), "lat_max": float(sa.lat[sel].max()) + 1e-4,
           "lon_min": float(sa.lon[sel].min()), "lon_max": float(sa.lon[sel].max()) + 1e-4}
    return pad_bbox(box, 0.5 * R)


def _latlon(prob: Problem, y: float, u: float) -> tuple[float, float]:
    la, lo = prob.frame.to_latlon(y, u)
    return round(float(la), 6), round(float(lo), 6)


def _empty_result(mode, notes, search, prob) -> dict:
    notes.append("No point in the region explains any onset with a catalogued strike: check the region, the "
                 "catalogue time window and the recording start time.")
    return {"mode": mode, "best": None, "candidates": [], "heat": search.heat_points(), "matches": [],
            "flash_matches": [], "ranges": [], "notes": notes}


def _ambiguity_radius(search: _Search, best: dict, drop: float = 1.0) -> float:
    """How far from the best point the coarse grid still scores within ``drop`` of the coarse best."""
    gy, gu, sc, _ = search.heat[0]
    near = sc >= float(sc.max()) - drop
    if not near.any():
        return 0.0
    return float(np.max(search.p.frame.dist(gy[near], gu[near], best["y"], best["u"])))


def _result(mode, best, refined, search, prob, acc_flashes, notes, sa, region) -> dict:
    frame = prob.frame
    m = prob.matches_at(best["y"], best["u"], best["T"])
    alt = prob.alternatives(best["y"], best["u"], best["T"], m)
    mean_alt = float(np.mean(alt)) if len(alt) else 0.0
    unc = _uncertainty(best, best["spacing"], frame, mean_alt)
    if mean_alt >= AMBIGUOUS_ALTERNATIVES:
        unc = max(unc, _ambiguity_radius(search, best))
        notes.append(f"The matches are not distinctive: each onset also fits about {mean_alt:.0f} other catalogued "
                     f"event{'s' if round(mean_alt) != 1 else ''} (the catalogue is too dense for its "
                     f"{describe_sigma(sa.sigma_m)} accuracy, as in an active storm), so the position is ambiguous "
                     "over a wide area. A known start time, video flashes or a more accurate catalogue would help.")
    if best.get("cov") is None and len(m) <= 2:
        # one or two ranges do not fix a point: the fit is a ring (or two crossings) around the events
        unc = max(unc, _ambiguity_radius(search, best, drop=0.5))
    ew, ns = bbox_size_m(region)
    unc = min(unc, 0.5 * math.hypot(ew, ns))          # beyond this it just means "anywhere in the region"
    rms = float(math.sqrt(np.mean([x["r"] ** 2 for x in m]))) if m else 0.0
    lat, lon = _latlon(prob, best["y"], best["u"])
    best_doc = {"lat": lat, "lon": lon, "start_utc": round(best["T"], 3), "score": round(best["score"], 3),
                "n_matched": len(m), "rms_s": round(rms, 3), "uncertainty_m": round(unc, 1)}
    # distinct candidates among the refined peaks (every coarse peak was refined)
    cands: list[dict] = []
    for r in refined:
        far = all(float(frame.dist(r["y"], r["u"], q["y"], q["u"])) >= max(2000.0, 2 * search.spacing_c)
                  or abs(r["T"] - q["T"]) > 5.0 for q in cands)
        if far:
            cands.append(r)
    cands.sort(key=lambda r: -r["score"])
    cand_docs = []
    for r in cands[:8]:
        la, lo = _latlon(prob, r["y"], r["u"])
        cand_docs.append({"lat": la, "lon": lo, "start_utc": round(r["T"], 3), "score": round(r["score"], 3),
                          "n_matched": int(r["n"])})
    matches = [{"onset_id": prob.onset_ids[x["k"]], "strike_id": sa.ids[x["j"]], "residual_s": round(x["r"], 3),
                "distance_m": round(x["d"], 1)} for x in m]
    fm = flash_matches(acc_flashes, sa, best["T"], FLASH_TOL_S, frame, best["y"], best["u"])
    ranges = []
    by_strike = {x["strike_id"]: x for x in matches}
    onset_t = dict(zip(prob.onset_ids, prob.a))
    for f in fm:
        mt = by_strike.get(f["strike_id"])
        ft = next((x["t"] for x in acc_flashes if x["id"] == f["flash_id"]), None)
        if mt is None or ft is None:
            continue
        delay = float(onset_t[mt["onset_id"]] - ft)
        j = sa.ids.index(f["strike_id"])
        if delay > 0 and not sa.seismic[j]:        # flash-to-bang ranging is for air waves
            ranges.append({"strike_id": f["strike_id"], "lat": round(float(sa.lat[j]), 6),
                           "lon": round(float(sa.lon[j]), 6), "distance_m": round(delay * prob.c, 1)})
    _add_notes(notes, mode, best, best_doc, cand_docs, prob, sa, search, region, m, fm, acc_flashes)
    return {"mode": mode, "best": best_doc, "candidates": cand_docs, "heat": search.heat_points(), "matches": matches,
            "flash_matches": fm, "ranges": ranges, "notes": notes}


def _add_notes(notes, mode, best, best_doc, cand_docs, prob, sa, search, region, m, fm, acc_flashes) -> None:
    frame = prob.frame
    med_sigma = float(np.median(sa.sigma_m))
    glm_share = np.mean([src.startswith("glm") for src in sa.sources]) if len(sa) else 0.0
    if med_sigma >= 2000:
        why = " (GLM flash centroids)" if glm_share > 0.5 else ""
        notes.append(f"Catalogue positions are only accurate to {describe_sigma(sa.sigma_m)}{why}, "
                     "so expect a few km of uncertainty.")
    n = len(m)
    if n == 1:
        d = m[0]["d"]
        notes.append(f"Only one event matched, at {d / 1000:.1f} km: that fixes the distance to it, not the direction, "
                     "so the listener can be anywhere on that ring (drawn on the map). More events, a second "
                     "bang, or a smaller search region narrow it down.")
    elif n < 3:
        notes.append(f"Only {n} onsets matched: weak result.")
    elif n < 5 and mode == "free":
        notes.append(f"Only {n} onsets matched with a free clock: treat the result with care.")
    if any(sa.seismic[x["j"]] for x in m):
        notes.append("Earthquakes are modelled as heard from the P-wave arrival (about 6 km/s from the hypocentre), "
                     "so they fix the clock well but the position only weakly.")
    unexplained = prob.K - n
    if n and unexplained > 0:
        notes.append(f"{unexplained} of {prob.K} onsets are not explained by a catalogued event "
                     "(false detections, or events the catalogue missed).")
    missed = prob.missed_strikes(best["y"], best["u"], best["T"])
    if missed:
        notes.append(f"{len(missed)} catalogued event{'s' if len(missed) != 1 else ''} within about 5 km should have "
                     "been heard during the recording but no onset is near the predicted time.")
    if len(cand_docs) > 1 and best_doc["score"] > 0:
        r2 = cand_docs[1]
        d = float(frame.dist(*frame.to_yu(r2["lat"], r2["lon"]), best["y"], best["u"]))
        distinct = d > max(2.0 * best_doc["uncertainty_m"], 3000.0) or abs(r2["start_utc"] - best_doc["start_utc"]) > 10.0
        if r2["score"] >= 0.85 * best_doc["score"] and distinct:
            notes.append(f"Another candidate {d / 1000:.1f} km away (start {_iso(r2['start_utc'])}) scores almost as "
                         f"well ({r2['score']:.1f} vs {best_doc['score']:.1f}): the result is ambiguous.")
    lat, lon = best_doc["lat"], best_doc["lon"]
    inner = square_bbox(lat, lon, 1.5 * search.spacing_c)
    if not (in_bbox(inner["lat_min"], inner["lon_min"], region) and in_bbox(inner["lat_max"], inner["lon_max"], region)):
        notes.append("The best point is at the edge of the search region; the true position may lie outside it.")
    if mode == "flash_sync" and fm:
        notes.append(f"{len(fm)} of {len(acc_flashes)} accepted flashes match catalogued events at the best clock.")
    notes.append(f"Recording start estimated as {_iso(best_doc['start_utc'])}.")
