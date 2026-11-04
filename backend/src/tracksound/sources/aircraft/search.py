"""Aircraft location searches with progress and cancellation, built on dopplertrack.

The physics, filters and scoring are dopplertrack's (``search``, ``score``, ``pipeline``); the
loops are re-expressed here so a web job can report progress, be cancelled, and keep what the
UI needs afterwards (which flights each candidate heard, the clock it implies, and the
modelled curves to draw on the spectrogram).

Clock convention (dopplertrack's): for a candidate point, ``t0_map = ev1.t_cpa - t_heard1``
maps unix time to recording time and the scorer's best ``shift`` moves the model later, so a
modelled sample emitted/heard at unix time u lands at recording time ``u + t0_map + shift``.
The recording therefore started at ``start_utc = -(t0_map + shift)``.
"""
from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd
from dopplertrack.adsb import Trace
from dopplertrack.audio import Spectrogram
from dopplertrack.geo import lla_to_enu
from dopplertrack.pipeline import pick_flight, segments_for_search
from dopplertrack.score import EventWindow, locked_score, modelled_curve
from dopplertrack.search import Grid, Segment, build_segments, cpa_table, empty_cpa_table, pair_candidates
from dopplertrack.tracks import EventFit

Progress = Callable[[float, str], None]

MAX_RANGE_M = 12_000.0
V_TOL = 0.35
D_TOL = (0.4, 2.5)
SHIFTS = np.arange(-8, 8.01, 0.5)
F0_SCALES = np.arange(0.95, 1.0501, 0.005)
MAX_HEAT = 20_000
TOP_CANDIDATES = 50
MAX_SEARCH_POINTS = 2_000_000

EVENT_FIT_FIELDS = ("t_cpa", "t_cpa_emit", "f0", "v", "d", "t_start", "t_end", "score", "f_pre", "f_post")


def to_fit(ev: dict) -> EventFit:
    return EventFit(**{k: float(ev[k]) for k in EVENT_FIT_FIELDS})


@dataclass
class Inputs:
    sp: Spectrogram
    traces: list[Trace]
    c: float
    elevation: Callable | None = None
    notes: list[str] = field(default_factory=list)
    area: object | None = None          # tracksound.countries.Area: refine keeps fine-grid points inside it


def _noop(_f: float, _m: str = "") -> None:
    pass


# ---- grid and closest approaches ---------------------------------------------------------

def grid_size(bbox: dict, spacing_m: float) -> int:
    lat0 = (bbox["lat_min"] + bbox["lat_max"]) / 2
    dlat = spacing_m / 111320.0
    dlon = spacing_m / (111320.0 * np.cos(np.radians(lat0)))
    return len(np.arange(bbox["lat_min"], bbox["lat_max"], dlat)) * len(np.arange(bbox["lon_min"], bbox["lon_max"], dlon))


def make_grid(inp: Inputs, area, spacing_m: float, point: dict | None = None) -> Grid:
    """Grid of candidate listener positions: one point, or the case's search area (the region,
    the countries, or the region inside the countries; see tracksound.countries)."""
    if point is not None:
        el = inp.elevation(np.array([point["lat"]]), np.array([point["lon"]])) if inp.elevation else np.zeros(1)
        return Grid.points([point["lat"]], [point["lon"]], np.maximum(el, 0.0))
    if area is None or area.bbox is None:
        raise ValueError("set a search region or a country first")
    lat, lon = area.grid(spacing_m)
    if len(lat) == 0:
        raise ValueError("the search area has no grid points: is the region outside the selected country?")
    if len(lat) > MAX_SEARCH_POINTS:
        from ...countries import suggest_spacing
        raise ValueError(f"{area.describe()} needs {len(lat):,} grid points at {spacing_m:.0f} m (the limit is "
                         f"{MAX_SEARCH_POINTS:,}). Use a coarser grid (about {suggest_spacing(area, MAX_SEARCH_POINTS):.0f} m), "
                         "then refine, or draw a smaller region.")
    elev = np.maximum(inp.elevation(lat, lon), 0.0) if inp.elevation else np.zeros_like(lat)
    b = area.bbox
    lat0, lon0 = (b["lat_min"] + b["lat_max"]) / 2, (b["lon_min"] + b["lon_max"]) / 2
    return Grid(lat, lon, elev, lla_to_enu(lat, lon, elev, lat0, lon0), lat0, lon0)


def grid_segments(grid: Grid, traces: list[Trace], max_range_m: float = MAX_RANGE_M) -> list[Segment]:
    e0, e1 = grid.enu[:, 0].min() - max_range_m, grid.enu[:, 0].max() + max_range_m
    n0, n1 = grid.enu[:, 1].min() - max_range_m, grid.enu[:, 1].max() + max_range_m
    return build_segments(traces, grid.lat0, grid.lon0, clip_enu=(e0, e1, n0, n1))


def cpa_with_progress(grid: Grid, segs: list[Segment], c: float, progress: Progress, chunk: int = 25) -> pd.DataFrame:
    """dopplertrack.search.cpa_table, a few segments at a time so progress can be reported."""
    parts = []
    for k in range(0, len(segs), chunk):
        progress(k / max(len(segs), 1), f"Closest approaches: {k} of {len(segs)} flight segments")
        part = cpa_table(grid, segs[k:k + chunk], max_range_m=MAX_RANGE_M, c=c)
        if len(part):
            part["seg"] = (part["seg"].to_numpy() + k).astype(np.int32)
            parts.append(part)
    return pd.concat(parts, ignore_index=True) if parts else empty_cpa_table()


def _cpa_at(seg: Segment, obs_enu, c: float) -> tuple[float, float, float]:
    """(t_heard_unix, slant_m, speed_mps) of seg's closest approach to obs."""
    d = np.linalg.norm(seg.enu - obs_enu, axis=1)
    j = int(np.argmin(d))
    return float(seg.t[j] + d[j] / c), float(d[j]), float(seg.speed[j])


def find_segment(segs: list[Segment], icao: str, t_ref: float) -> int:
    """Index of the segment of aircraft ``icao`` alive at (or nearest to) unix time t_ref."""
    cands = [i for i, s in enumerate(segs) if s.icao == icao]
    if not cands:
        raise ValueError(f"flight {icao} is not in the flight set any more (was it re-fetched?)")
    return min(cands, key=lambda i: 0.0 if segs[i].t[0] - 60 <= t_ref <= segs[i].t[-1] + 60
               else min(abs(segs[i].t[0] - t_ref), abs(segs[i].t[-1] - t_ref)))


_SEG_CACHE: "OrderedDict[tuple, tuple[list[Trace], list[Segment]]]" = OrderedDict()


def cached_segments(traces: list[Trace], bbox: dict, lat0: float, lon0: float) -> list[Segment]:
    """dopplertrack's segments_for_search, memoised: rebuilding segments for a thousand real
    flights takes seconds, and the UI asks for curves on every candidate click. The cache keeps
    a reference to the trace list, so its id cannot be reused while cached."""
    key = (id(traces), round(bbox["lat_min"], 6), round(bbox["lat_max"], 6), round(bbox["lon_min"], 6),
           round(bbox["lon_max"], 6), round(lat0, 7), round(lon0, 7))
    hit = _SEG_CACHE.get(key)
    if hit is not None and hit[0] is traces:
        _SEG_CACHE.move_to_end(key)
        return hit[1]
    segs = segments_for_search(traces, (bbox["lat_min"], bbox["lat_max"], bbox["lon_min"], bbox["lon_max"]), lat0, lon0)
    _SEG_CACHE[key] = (traces, segs)
    while len(_SEG_CACHE) > 4:
        _SEG_CACHE.popitem(last=False)
    return segs


def _flight_info(inp: Inputs, seg: Segment) -> dict:
    tr = inp.traces[seg.trace_idx]
    return {"flight_idx": int(seg.trace_idx), "icao": tr.icao, "reg": tr.reg, "type": tr.typ, "desc": tr.desc}


def _best_shift_scale(w: EventWindow, t_audio, ratio, shifts) -> tuple[float, float, float]:
    """(score, shift, f0_scale) maximising the event's sweep score."""
    sc = w.sweep_scores(t_audio, ratio, shifts, F0_SCALES)
    i, j = np.unravel_index(int(np.argmax(sc)), sc.shape)
    return float(sc[i, j]), float(np.asarray(shifts)[i]), float(F0_SCALES[j])


# ---- result documents ----------------------------------------------------------------------

def _heat(lat, lon, score) -> list[dict]:
    lat, lon, score = map(np.asarray, (lat, lon, score))
    if len(score) > MAX_HEAT:
        keep = np.argpartition(-score, MAX_HEAT)[:MAX_HEAT]
        lat, lon, score = lat[keep], lon[keep], score[keep]
    return [{"lat": round(float(a), 6), "lon": round(float(b), 6), "score": round(float(s), 4)} for a, b, s in zip(lat, lon, score)]


def _margin(cands: list[dict]) -> float | None:
    if len(cands) < 2 or cands[0]["score"] <= 0:
        return None
    return float((cands[0]["score"] - cands[1]["score"]) / cands[0]["score"])


@dataclass
class Result:
    """What a search job writes: the public document parts plus internal data for refine/curves."""
    bbox: dict | None
    lat0: float
    lon0: float
    stats: dict
    candidates: list[dict]          # public AircraftCandidate dicts (rank filled in later)
    internal: list[dict]            # per candidate: t0_map, shift, flights [{event_id, icao, t_ref, scale}]
    heat: list[dict]
    notes: list[str]

    def public(self) -> dict:
        for r, c in enumerate(self.candidates, 1):
            c["rank"] = r
        return {"bbox": self.bbox, "stats": self.stats, "candidates": self.candidates,
                "best": self.candidates[0] if self.candidates else None, "margin": _margin(self.candidates),
                "heat": self.heat, "notes": self.notes}


# ---- two-event search (blind grid or one known point) ----------------------------------------

def two_event_search(inp: Inputs, ev1d: dict, ev2d: dict, area, spacing_m: float = 200.0,
                     point: dict | None = None, tol_s: float = 6.0, silence: bool = True,
                     progress: Progress = _noop) -> Result:
    t_start = time.time()
    if ev2d["t_cpa"] < ev1d["t_cpa"]:
        ev1d, ev2d = ev2d, ev1d
    ev1, ev2 = to_fit(ev1d), to_fit(ev2d)
    h1, h2 = int(ev1d.get("harmonics", 1)), int(ev2d.get("harmonics", 1))
    progress(0.01, "Building the grid" + (" (terrain elevation)" if inp.elevation else ""))
    grid = make_grid(inp, area, spacing_m, point)
    bbox = None if point is not None else area.bbox
    segs = grid_segments(grid, inp.traces)
    if not segs:
        raise ValueError("no airborne flight segments near the region; fetch flights for this date and region first")
    cpa = cpa_with_progress(grid, segs, inp.c, lambda f, m: progress(0.03 + 0.42 * f, m))
    dt = ev2.t_cpa - ev1.t_cpa
    progress(0.46, f"Pairing {len(cpa):,} closest approaches {dt:.0f} s apart")
    pairs = pair_candidates(cpa, dt, tol_s=tol_s,
                            v1=(ev1.v * (1 - V_TOL), ev1.v * (1 + V_TOL)), d1=(ev1.d * D_TOL[0], ev1.d * D_TOL[1]),
                            v2=(ev2.v * (1 - V_TOL), ev2.v * (1 + V_TOL)), d2=(ev2.d * D_TOL[0], ev2.d * D_TOL[1]),
                            silence_d_m=6000.0 if silence else None)
    w1 = EventWindow.from_fit(inp.sp, ev1, h1)
    w2 = EventWindow.from_fit(inp.sp, ev2, h2)
    rows = []
    n = len(pairs)
    for k, p in enumerate(pairs.itertuples()):
        if k % 100 == 0:
            progress(0.5 + 0.47 * k / max(n, 1), f"Scoring candidate {k:,} of {n:,}")
        tot, s1, s2, sh = locked_score(w1, w2, segs[p.seg1], segs[p.seg2], grid.enu[p.gidx], float(p.t_heard1), c=inp.c)
        rows.append((int(p.gidx), int(p.seg1), int(p.seg2), float(p.t_heard1), float(p.t_heard2), float(p.d1), float(p.v1),
                     float(p.d2), float(p.v2), tot, s1, s2, sh))
    progress(0.98, "Ranking")
    res = pd.DataFrame(rows, columns=["gidx", "seg1", "seg2", "t_heard1", "t_heard2", "d1", "v1", "d2", "v2",
                                      "score", "s1", "s2", "shift"]).sort_values("score", ascending=False)
    notes = list(inp.notes)
    if not len(res):
        notes.append("No grid point heard two flights matching these events the right time apart. Check the date, "
                     "widen the region, or try other events.")
    res["icao1"] = [segs[s].icao for s in res.seg1]
    res["icao2"] = [segs[s].icao for s in res.seg2]
    best = res.drop_duplicates(["icao1", "icao2"]).head(TOP_CANDIDATES)
    cands, internal = [], []
    for r in best.itertuples():
        g = int(r.gidx)
        t0_map = ev1.t_cpa - r.t_heard1
        cands.append({
            "lat": float(grid.lat[g]), "lon": float(grid.lon[g]), "elev_m": float(grid.elev[g]), "score": float(r.score),
            "start_utc": float(-(t0_map + r.shift)), "shift_s": float(r.shift),
            "flights": [
                {"event_id": ev1d["id"], **_flight_info(inp, segs[int(r.seg1)]), "t_heard_utc": float(r.t_heard1),
                 "d_m": float(r.d1), "v_mps": float(r.v1), "score": float(r.s1)},
                {"event_id": ev2d["id"], **_flight_info(inp, segs[int(r.seg2)]), "t_heard_utc": float(r.t_heard2),
                 "d_m": float(r.d2), "v_mps": float(r.v2), "score": float(r.s2)},
            ]})
        internal.append({"t0_map": float(t0_map), "shift": float(r.shift), "seg1": int(r.seg1), "seg2": int(r.seg2),
                         "t_heard1": float(r.t_heard1), "icao1": r.icao1, "icao2": r.icao2,
                         "flights": [{"event_id": ev1d["id"], "icao": r.icao1, "t_ref": float(r.t_heard1)},
                                     {"event_id": ev2d["id"], "icao": r.icao2, "t_ref": float(r.t_heard2)}]})
    per_point = res.groupby("gidx").score.max() if len(res) else pd.Series(dtype=float)
    gi = per_point.index.to_numpy(int)
    stats = {"n_points": int(len(grid)), "n_segments": int(len(segs)), "n_cpa": int(len(cpa)), "n_candidates": int(n),
             "elapsed_s": round(time.time() - t_start, 2)}
    return Result(bbox, float(grid.lat0), float(grid.lon0), stats, cands, internal,
                  _heat(grid.lat[gi], grid.lon[gi], per_point.to_numpy()), notes)


# ---- one event with a known clock ------------------------------------------------------------

def single_event_search(inp: Inputs, evd: dict, area, t_cpa_utc: float, spacing_m: float = 200.0,
                        tol_s: float = 30.0, progress: Progress = _noop) -> Result:
    t_start = time.time()
    ev = to_fit(evd)
    h = int(evd.get("harmonics", 1))
    progress(0.01, "Building the grid" + (" (terrain elevation)" if inp.elevation else ""))
    grid = make_grid(inp, area, spacing_m)
    bbox = area.bbox
    segs_all = grid_segments(grid, inp.traces)
    segs = [s for s in segs_all if s.t[0] - 300 <= t_cpa_utc <= s.t[-1] + 300]
    if not segs:
        raise ValueError("no flight was airborne near the region at that time; check the start time and date")
    cpa = cpa_with_progress(grid, segs, inp.c, lambda f, m: progress(0.03 + 0.45 * f, m))
    m = cpa[(cpa.t_heard - t_cpa_utc).abs() <= tol_s]
    m = m[m.v.between(ev.v * (1 - V_TOL), ev.v * (1 + V_TOL)) & m.d.between(ev.d * D_TOL[0], ev.d * D_TOL[1])]
    w = EventWindow.from_fit(inp.sp, ev, h)
    rows = []
    n = len(m)
    for k, p in enumerate(m.itertuples()):
        if k % 100 == 0:
            progress(0.5 + 0.47 * k / max(n, 1), f"Scoring candidate {k:,} of {n:,}")
        seg = segs[int(p.seg)]
        obs = grid.enu[int(p.gidx)]
        to, ratio = modelled_curve(seg, obs, inp.c)
        t0_map = ev.t_cpa - float(p.t_heard)
        s, sh, _ = _best_shift_scale(w, to + t0_map, ratio, SHIFTS)
        rows.append((int(p.gidx), int(p.seg), float(p.t_heard), float(p.d), float(p.v), s, sh))
    res = pd.DataFrame(rows, columns=["gidx", "seg", "t_heard", "d", "v", "score", "shift"]).sort_values("score", ascending=False)
    res["icao"] = [segs[s].icao for s in res.seg]
    notes = list(inp.notes)
    if not len(res):
        notes.append("No flight passed the region at the right time with this speed and distance. Check the start time.")
    cands, internal = [], []
    for r in res.drop_duplicates(["icao"]).head(TOP_CANDIDATES).itertuples():
        g = int(r.gidx)
        t0_map = ev.t_cpa - r.t_heard
        cands.append({"lat": float(grid.lat[g]), "lon": float(grid.lon[g]), "elev_m": float(grid.elev[g]),
                      "score": float(r.score), "start_utc": float(-(t0_map + r.shift)), "shift_s": float(r.shift),
                      "flights": [{"event_id": evd["id"], **_flight_info(inp, segs[int(r.seg)]), "t_heard_utc": float(r.t_heard),
                                   "d_m": float(r.d), "v_mps": float(r.v), "score": float(r.score)}]})
        internal.append({"t0_map": float(t0_map), "shift": float(r.shift),
                         "flights": [{"event_id": evd["id"], "icao": r.icao, "t_ref": float(r.t_heard)}]})
    per_point = res.groupby("gidx").score.max() if len(res) else pd.Series(dtype=float)
    gi = per_point.index.to_numpy(int)
    stats = {"n_points": int(len(grid)), "n_segments": int(len(segs)), "n_cpa": int(len(cpa)), "n_candidates": int(n),
             "elapsed_s": round(time.time() - t_start, 2)}
    return Result(bbox, float(grid.lat0), float(grid.lon0), stats, cands, internal,
                  _heat(grid.lat[gi], grid.lon[gi], per_point.to_numpy()), notes)


# ---- refine: fine grid around one candidate, every event locked to one clock -------------------

def refine(inp: Inputs, parent: dict, parent_internal: dict, rank: int, events: dict[str, dict],
           extra_ids: list[str], spacing_m: float = 50.0, half_m: float = 600.0, progress: Progress = _noop) -> Result:
    """Port of dopplertrack.pipeline.refine with progress; also handles single-event parents."""
    t_start = time.time()
    cands = parent["candidates"]
    if not 1 <= rank <= len(cands):
        raise ValueError(f"rank {rank} is not in this search's results")
    cand, ci = cands[rank - 1], parent_internal["candidates"][rank - 1]
    lat0, lon0 = parent_internal["lat0"], parent_internal["lon0"]
    bbox = parent["bbox"] or {"lat_min": cand["lat"] - 0.2, "lat_max": cand["lat"] + 0.2,
                              "lon_min": cand["lon"] - 0.2, "lon_max": cand["lon"] + 0.2}
    segs = cached_segments(inp.traces, bbox, lat0, lon0)
    anchor_ids = [f["event_id"] for f in ci["flights"]]
    anchors = [events[e] for e in anchor_ids]
    anchor_segs = [segs[find_segment(segs, f["icao"], f["t_ref"])] for f in ci["flights"]]
    fits = [to_fit(e) for e in anchors]
    wins = [EventWindow.from_fit(inp.sp, f, int(e.get("harmonics", 1))) for f, e in zip(fits, anchors)]

    lat, lon = cand["lat"], cand["lon"]
    dlat = half_m / 111320.0
    dlon = half_m / (111320.0 * np.cos(np.radians(lat)))
    progress(0.02, "Building the fine grid")
    g = Grid.make(lat - dlat, lat + dlat, lon - dlon, lon + dlon, spacing_m, inp.elevation)
    if inp.area is not None and getattr(inp.area, "countries", None):
        keep = inp.area.contains(g.lat, g.lon)          # stay inside the selected countries
        if keep.any() and not keep.all():
            g = Grid(g.lat[keep], g.lon[keep], g.elev[keep], g.enu[keep], g.lat0, g.lon0)
    obs0 = lla_to_enu(lat, lon, cand.get("elev_m", 0.0), lat0, lon0)

    # extra events: choose each one's flight once, at the candidate, on the candidate's clock
    xs = [eid for eid in extra_ids if eid not in anchor_ids]
    x_events, x_segs, x_wins = [], [], []
    for eid in xs:
        e = events[eid]
        k, sc = pick_flight(inp.sp, to_fit(e), segs, obs0, ci["t0_map"], ci["shift"], int(e.get("harmonics", 1)), inp.c)
        if k < 0:
            inp.notes.append(f"Extra event at {e['t_cpa']:.0f} s: no flight near the candidate at that time, skipped.")
            continue
        x_events.append(e); x_segs.append(segs[k]); x_wins.append(EventWindow.from_fit(inp.sp, to_fit(e), int(e.get("harmonics", 1))))

    rows = []
    for i in range(len(g)):
        if i % 25 == 0:
            progress(0.05 + 0.92 * i / len(g), f"Scoring fine grid point {i} of {len(g)}")
        obs = lla_to_enu(g.lat[i], g.lon[i], g.elev[i], lat0, lon0)
        t_heard1, d1, v1 = _cpa_at(anchor_segs[0], obs, inp.c)
        t0_map = fits[0].t_cpa - t_heard1
        if len(anchors) == 2:
            tot, s1, s2, sh = locked_score(wins[0], wins[1], anchor_segs[0], anchor_segs[1], obs, t_heard1, c=inp.c)
            per = [s1, s2]
        else:
            to, r = modelled_curve(anchor_segs[0], obs, inp.c)
            s1, sh, _ = _best_shift_scale(wins[0], to + t0_map, r, SHIFTS)
            per, tot = [s1], s1
        for seg, w in zip(x_segs, x_wins):
            to, r = modelled_curve(seg, obs, inp.c)
            s = float(w.sweep_scores(to + t0_map, r, [sh], F0_SCALES).max())
            per.append(s); tot += s
        rows.append((i, tot, sh, t0_map, per))
    rows.sort(key=lambda r: -r[1])

    all_events = anchors + x_events
    all_segs = anchor_segs + x_segs
    out_c, out_i = [], []
    for i, tot, sh, t0_map, per in rows[:20]:
        obs = lla_to_enu(g.lat[i], g.lon[i], g.elev[i], lat0, lon0)
        flights = []
        for e, seg, s in zip(all_events, all_segs, per):
            th, d, v = _cpa_at(seg, obs, inp.c)
            flights.append({"event_id": e["id"], **_flight_info(inp, seg), "t_heard_utc": th, "d_m": d, "v_mps": v, "score": s})
        out_c.append({"lat": float(g.lat[i]), "lon": float(g.lon[i]), "elev_m": float(g.elev[i]), "score": float(tot),
                      "start_utc": float(-(t0_map + sh)), "shift_s": float(sh), "flights": flights})
        out_i.append({"t0_map": float(t0_map), "shift": float(sh),
                      "flights": [{"event_id": f["event_id"], "icao": f["icao"], "t_ref": f["t_heard_utc"]} for f in flights]})
    idx = np.array([r[0] for r in rows], int)
    stats = {"n_points": int(len(g)), "n_segments": int(len(segs)), "n_cpa": 0, "n_candidates": int(len(g)),
             "elapsed_s": round(time.time() - t_start, 2)}
    res = Result(bbox, lat0, lon0, stats, out_c, out_i,
                 _heat(g.lat[idx], g.lon[idx], np.array([r[1] for r in rows])), list(inp.notes))
    return res


# ---- modelled curves for display -----------------------------------------------------------------

def candidate_curves(inp: Inputs, search: dict, internal: dict, rank: int, events: dict[str, dict],
                     max_points: int = 600) -> dict:
    """Fundamental frequency each candidate flight would produce at the candidate point,
    on the recording's time axis, for drawing over the spectrogram."""
    cands = search["candidates"]
    if not 1 <= rank <= len(cands):
        raise ValueError(f"rank {rank} is not in this search's results")
    cand, ci = cands[rank - 1], internal["candidates"][rank - 1]
    lat0, lon0 = internal["lat0"], internal["lon0"]
    bbox = search["bbox"] or {"lat_min": cand["lat"] - 0.2, "lat_max": cand["lat"] + 0.2,
                              "lon_min": cand["lon"] - 0.2, "lon_max": cand["lon"] + 0.2}
    segs = cached_segments(inp.traces, bbox, lat0, lon0)
    obs = lla_to_enu(cand["lat"], cand["lon"], cand.get("elev_m", 0.0), lat0, lon0)
    out = []
    for f in ci["flights"]:
        e = events.get(f["event_id"])
        if e is None:
            continue
        k = find_segment(segs, f["icao"], f["t_ref"])
        to, ratio = modelled_curve(segs[k], obs, inp.c)
        w = EventWindow.from_fit(inp.sp, to_fit(e), int(e.get("harmonics", 1)))
        t_audio = to + ci["t0_map"]
        _, _, scale = _best_shift_scale(w, t_audio, ratio, [ci["shift"]])
        t = t_audio + ci["shift"]
        keep = np.isfinite(ratio) & (t >= e["t_start"] - 180) & (t <= e["t_end"] + 180)
        t, fr = t[keep], e["f0"] * scale * ratio[keep]
        if len(t) > max_points:
            sel = np.linspace(0, len(t) - 1, max_points).astype(int)
            t, fr = t[sel], fr[sel]
        out.append({"event_id": f["event_id"], "flight_idx": int(segs[k].trace_idx), "icao": f["icao"],
                    "harmonics": int(e.get("harmonics", 1)),
                    "t": np.round(t, 3).tolist(), "f": np.round(fr, 3).tolist()})
    return {"events": out}


def estimate(traces: list[Trace], area, spacing_m: float, mode: str, t_cpa_utc: float | None = None,
             point: dict | None = None) -> dict:
    """Rough cost of a search, plus a coarser spacing to suggest when it would be slow.
    Calibrated on dopplertrack's published run (380 k points x 3 068 segments, about 19
    minutes on a laptop) and on our own runs on a 4-core machine (about 2.5 us per pair)."""
    if mode == "point":
        p = point or {"lat": 0.0, "lon": 0.0}
        bbox = {"lat_min": p["lat"] - 0.01, "lat_max": p["lat"] + 0.01, "lon_min": p["lon"] - 0.01, "lon_max": p["lon"] + 0.01}
        n_points, area_km2 = 1, 0.0
    else:
        bbox = area.bbox
        if area.countries:
            area_km2 = area.area_km2()
            n_points = int(area_km2 * 1e6 / spacing_m ** 2)
        else:
            n_points = grid_size(bbox, spacing_m)
            area_km2 = n_points * (spacing_m / 1000) ** 2
    lat0, lon0 = (bbox["lat_min"] + bbox["lat_max"]) / 2, (bbox["lon_min"] + bbox["lon_max"]) / 2
    corners = lla_to_enu(np.array([bbox["lat_min"], bbox["lat_max"]]), np.array([bbox["lon_min"], bbox["lon_max"]]),
                         np.zeros(2), lat0, lon0)
    clip = (corners[:, 0].min() - MAX_RANGE_M, corners[:, 0].max() + MAX_RANGE_M,
            corners[:, 1].min() - MAX_RANGE_M, corners[:, 1].max() + MAX_RANGE_M)
    segs = build_segments(traces, lat0, lon0, clip_enu=clip)
    if mode == "single" and t_cpa_utc is not None:
        segs = [s for s in segs if s.t[0] - 300 <= t_cpa_utc <= s.t[-1] + 300]

    def cost(n: float) -> float:
        return 5.0 + 2.5e-6 * n * len(segs) + (0.5 * len(segs) if mode == "point" else 0.0)

    est = cost(n_points)
    suggested = None
    if mode != "point" and (est > 600 or n_points > MAX_SEARCH_POINTS):
        for sp in (250, 300, 400, 500, 750, 1000, 1500, 2000, 3000, 5000, 10000):
            n = n_points * (spacing_m / sp) ** 2
            if sp > spacing_m and cost(n) <= 600 and n <= MAX_SEARCH_POINTS:
                suggested = float(sp)
                break
    return {"n_points": int(n_points), "n_segments": int(len(segs)), "est_seconds": float(round(est, 1)),
            "area_km2": round(float(area_km2), 1), "suggested_spacing_m": suggested,
            "too_many_points": bool(n_points > MAX_SEARCH_POINTS)}
