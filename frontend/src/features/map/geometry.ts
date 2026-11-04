import type { Feature, FeatureCollection } from "geojson";
import type { AircraftCandidate, FlightsGeoJSON, HeatPoint, LightningSolve, Strike } from "../../api/types";
import { circleRing } from "../../lib/geo";

export const EMPTY: FeatureCollection = { type: "FeatureCollection", features: [] };

/** Heat points with a normalised score u in [0, 1]. */
export function heatFC(points: HeatPoint[] | undefined | null): FeatureCollection {
  if (!points?.length) return EMPTY;
  let lo = Infinity;
  let hi = -Infinity;
  for (const p of points) {
    if (p.score < lo) lo = p.score;
    if (p.score > hi) hi = p.score;
  }
  const span = hi - lo || 1;
  return {
    type: "FeatureCollection",
    features: points.map((p) => ({
      type: "Feature",
      properties: { u: (p.score - lo) / span, score: p.score },
      geometry: { type: "Point", coordinates: [p.lon, p.lat] },
    })),
  };
}

/** Approximate grid spacing (m) of a heat map: median nearest-neighbour step along latitude rows. */
export function heatSpacing(points: HeatPoint[] | undefined | null, fallback: number): number {
  if (!points || points.length < 3) return fallback;
  const lats = [...new Set(points.slice(0, 4000).map((p) => p.lat.toFixed(6)))].map(Number).sort((a, b) => a - b);
  if (lats.length < 2) return fallback;
  const steps: number[] = [];
  for (let i = 1; i < Math.min(lats.length, 200); i++) steps.push(lats[i] - lats[i - 1]);
  steps.sort((a, b) => a - b);
  const m = steps[Math.floor(steps.length / 2)] * 111320;
  return m > 1 && m < 50000 ? m : fallback;
}

/** Circle radius (px) at zoom 0 for half a grid cell, at latitude `lat`. */
export function r0For(spacingM: number, lat: number): number {
  const mpp0 = 156543.03 * Math.cos((lat * Math.PI) / 180);
  return spacingM / 2 / Math.max(1, mpp0);
}

export function strikesFC(strikes: Strike[] | undefined, matched: Set<string>): FeatureCollection {
  if (!strikes?.length) return EMPTY;
  let lo = Infinity;
  let hi = -Infinity;
  for (const s of strikes) {
    if (s.t_utc < lo) lo = s.t_utc;
    if (s.t_utc > hi) hi = s.t_utc;
  }
  const span = hi - lo || 1;
  return {
    type: "FeatureCollection",
    features: strikes.map((s) => ({
      type: "Feature",
      properties: { id: s.id, u: (s.t_utc - lo) / span, t_utc: s.t_utc, energy: s.energy, kind: s.kind, source: s.source, matched: matched.has(s.id) },
      geometry: { type: "Point", coordinates: [s.lon, s.lat] },
    })),
  };
}

export function solveGeometry(solve: LightningSolve | undefined | null, strikes: Strike[] | undefined) {
  if (!solve) return { lMatch: EMPTY, ranges: EMPTY, lUnc: EMPTY, matched: new Set<string>() };
  const byId = new Map((strikes ?? []).map((s) => [s.id, s]));
  const matched = new Set<string>(solve.matches.map((m) => m.strike_id));
  for (const m of solve.flash_matches ?? []) matched.add(m.strike_id);
  const best = solve.best;
  const lMatch: FeatureCollection = {
    type: "FeatureCollection",
    features: best
      ? solve.matches
          .map((m) => byId.get(m.strike_id))
          .filter((s): s is Strike => !!s)
          .map((s) => ({
            type: "Feature" as const,
            properties: {},
            geometry: { type: "LineString" as const, coordinates: [[best.lon, best.lat], [s.lon, s.lat]] },
          }))
      : [],
  };
  const ranges: FeatureCollection = {
    type: "FeatureCollection",
    features: (solve.ranges ?? []).map((r) => ({
      type: "Feature",
      properties: { id: r.strike_id },
      geometry: { type: "LineString", coordinates: circleRing(r.lat, r.lon, r.distance_m) },
    })),
  };
  const lUnc: FeatureCollection = best
    ? {
        type: "FeatureCollection",
        features: [
          {
            type: "Feature",
            properties: {},
            geometry: { type: "Polygon", coordinates: [circleRing(best.lat, best.lon, Math.max(20, best.uncertainty_m))] },
          },
        ],
      }
    : EMPTY;
  return { lMatch, ranges, lUnc, matched };
}

/** Interpolated position of a flight at unix time t, or null if it wasn't flying then. */
export function positionAt(f: FlightsGeoJSON["features"][number], t: number): [number, number] | null {
  const times = f.properties.times;
  const coords = f.geometry.coordinates;
  if (!times?.length || t < times[0] || t > times[times.length - 1]) return null;
  let lo = 0;
  let hi = times.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (times[mid] <= t) lo = mid;
    else hi = mid;
  }
  const a = coords[lo];
  const b = coords[hi] ?? a;
  const ta = times[lo];
  const tb = times[hi] ?? ta;
  const u = tb > ta ? (t - ta) / (tb - ta) : 0;
  return [a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u];
}

/** Lines from a candidate point to where each heard aircraft was at closest approach. */
export function candidateGeometry(cand: AircraftCandidate | null | undefined, flights: FlightsGeoJSON | undefined, c: number) {
  if (!cand || !flights) return { links: EMPTY, cpa: EMPTY };
  const byIdx = new Map(flights.features.map((f) => [f.properties.idx, f]));
  const links: Feature[] = [];
  const cpa: Feature[] = [];
  for (const fl of cand.flights) {
    const f = byIdx.get(fl.flight_idx);
    if (!f) continue;
    const p = positionAt(f, fl.t_heard_utc - fl.d_m / c);
    if (!p) continue;
    links.push({ type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: [[cand.lon, cand.lat], p] } });
    cpa.push({ type: "Feature", properties: { reg: fl.reg ?? fl.icao }, geometry: { type: "Point", coordinates: p } });
  }
  return { links: { type: "FeatureCollection", features: links } as FeatureCollection, cpa: { type: "FeatureCollection", features: cpa } as FeatureCollection };
}
