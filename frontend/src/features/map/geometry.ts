import type { Feature, FeatureCollection } from "geojson";
import type { AircraftCandidate, FlightsGeoJSON, HeatPoint, LightningSolve, Strike } from "../../api/types";
import { colormapRGB } from "../../lib/colormap";
import { circleRing } from "../../lib/geo";

export const EMPTY: FeatureCollection = { type: "FeatureCollection", features: [] };

/**
 * Per-point grid cell size (m): the distance to the nearest other point, found with a spatial
 * hash. Handles multi-resolution heat maps (a coarse grid plus a fine grid around the peak).
 */
function cellSizes(xs: Float64Array, ys: Float64Array, fallback: number): Float64Array {
  const n = xs.length;
  const out = new Float64Array(n).fill(fallback);
  if (n < 2) return out;
  // bucket size: the coarsest typical spacing, from a sample of brute-force neighbour distances
  const samples = Math.min(n, 64);
  const est: number[] = [];
  for (let s = 0; s < samples; s++) {
    const i = Math.floor((s * n) / samples);
    let best = Infinity;
    for (let j = 0; j < n; j++) {
      if (j === i) continue;
      const q = (xs[j] - xs[i]) ** 2 + (ys[j] - ys[i]) ** 2;
      if (q > 1e-4 && q < best) best = q;
    }
    if (Number.isFinite(best)) est.push(Math.sqrt(best));
  }
  est.sort((p, q) => p - q);
  const B = Math.max(1, est.length ? est[Math.floor(est.length * 0.9)] : fallback);
  const grid = new Map<string, number[]>();
  const key = (gx: number, gy: number) => `${gx},${gy}`;
  for (let i = 0; i < n; i++) {
    const k = key(Math.floor(xs[i] / B), Math.floor(ys[i] / B));
    const l = grid.get(k);
    if (l) l.push(i);
    else grid.set(k, [i]);
  }
  // the 4th-nearest neighbour: on a regular (even anisotropic) grid that's the larger axis step
  const K = 4;
  const nearest = new Float64Array(K);
  for (let i = 0; i < n; i++) {
    const gx = Math.floor(xs[i] / B);
    const gy = Math.floor(ys[i] / B);
    nearest.fill(Infinity);
    for (let dx = -1; dx <= 1; dx++)
      for (let dy = -1; dy <= 1; dy++) {
        const l = grid.get(key(gx + dx, gy + dy));
        if (!l) continue;
        for (const j of l) {
          if (j === i) continue;
          const q = (xs[j] - xs[i]) ** 2 + (ys[j] - ys[i]) ** 2;
          if (q <= 1e-4 || q >= nearest[K - 1]) continue;
          let k = K - 1;
          while (k > 0 && nearest[k - 1] > q) {
            nearest[k] = nearest[k - 1];
            k--;
          }
          nearest[k] = q;
        }
      }
    let pick = Infinity;
    for (let k = K - 1; k >= 0; k--) {
      if (Number.isFinite(nearest[k])) {
        pick = nearest[k];
        break;
      }
    }
    out[i] = Number.isFinite(pick) ? Math.min(Math.sqrt(pick), B * 1.5) : B;
  }
  return out;
}

export type HeatImage = { url: string; coordinates: [[number, number], [number, number], [number, number], [number, number]] };

/**
 * Rasterize a (possibly multi-resolution) score grid into one image by nearest-point lookup:
 * every pixel takes the score of the closest grid point (within about one coarse cell), so
 * coarse and fine grids blend without gaps. Score maps to the magma ramp and opacity.
 */
export function heatImage(points: HeatPoint[] | undefined | null, fallbackSpacingM: number): HeatImage | null {
  if (!points?.length) return null;
  const n = points.length;
  const lat0 = points.reduce((a, p) => a + p.lat, 0) / n;
  const kx = 111320 * Math.cos((lat0 * Math.PI) / 180);
  const ky = 111320;
  const xs = new Float64Array(n);
  const ys = new Float64Array(n);
  let lo = Infinity;
  let hi = -Infinity;
  for (let i = 0; i < n; i++) {
    xs[i] = points[i].lon * kx;
    ys[i] = points[i].lat * ky;
    lo = Math.min(lo, points[i].score);
    hi = Math.max(hi, points[i].score);
  }
  const size = cellSizes(xs, ys, fallbackSpacingM);
  const sorted = Array.from(size).sort((p, q) => p - q);
  const fine = sorted[Math.floor(sorted.length * 0.05)] || fallbackSpacingM;
  const coarse = sorted[Math.floor(sorted.length * 0.95)] || fallbackSpacingM;
  const reach = coarse * 0.75;
  let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
  for (let i = 0; i < n; i++) {
    x0 = Math.min(x0, xs[i]);
    x1 = Math.max(x1, xs[i]);
    y0 = Math.min(y0, ys[i]);
    y1 = Math.max(y1, ys[i]);
  }
  x0 -= reach;
  x1 += reach;
  y0 -= reach;
  y1 += reach;
  const res = Math.max(fine / 2, (x1 - x0) / 720, (y1 - y0) / 720, 1);
  const W = Math.max(1, Math.ceil((x1 - x0) / res));
  const H = Math.max(1, Math.ceil((y1 - y0) / res));
  // spatial hash for the nearest-point lookup
  const B = Math.max(reach, res);
  const grid = new Map<number, number[]>();
  const hkey = (gx: number, gy: number) => gx * 1_000_003 + gy;
  for (let i = 0; i < n; i++) {
    const k = hkey(Math.floor((xs[i] - x0) / B), Math.floor((ys[i] - y0) / B));
    const l = grid.get(k);
    if (l) l.push(i);
    else grid.set(k, [i]);
  }
  const rgb = colormapRGB("magma");
  const span = hi - lo || 1;
  const canvas = document.createElement("canvas");
  canvas.width = W;
  canvas.height = H;
  const ctx = canvas.getContext("2d");
  if (!ctx) return null;
  const img = ctx.createImageData(W, H);
  const d = img.data;
  const reach2 = reach * reach;
  for (let r = 0; r < H; r++) {
    const y = y1 - (r + 0.5) * res;
    const gy = Math.floor((y - y0) / B);
    for (let c = 0; c < W; c++) {
      const x = x0 + (c + 0.5) * res;
      const gx = Math.floor((x - x0) / B);
      let best = -1;
      let bestD = reach2;
      for (let dx = -1; dx <= 1; dx++)
        for (let dy = -1; dy <= 1; dy++) {
          const l = grid.get(hkey(gx + dx, gy + dy));
          if (!l) continue;
          for (const j of l) {
            const q = (xs[j] - x) ** 2 + (ys[j] - y) ** 2;
            // a point only reaches as far as its own grid cell (fine points don't spill far)
            if (q < bestD && q <= (size[j] * 0.75) ** 2 + 1) {
              bestD = q;
              best = j;
            }
          }
        }
      if (best < 0) continue;
      const u = (points[best].score - lo) / span;
      const k = Math.round((0.2 + 0.8 * u) * 255) * 3;
      const o = (r * W + c) * 4;
      d[o] = rgb[k];
      d[o + 1] = rgb[k + 1];
      d[o + 2] = rgb[k + 2];
      d[o + 3] = Math.round(255 * (0.2 + 0.72 * Math.pow(u, 1.3)));
    }
  }
  ctx.putImageData(img, 0, 0);
  return {
    url: canvas.toDataURL("image/png"),
    coordinates: [
      [x0 / kx, y1 / ky],
      [x1 / kx, y1 / ky],
      [x1 / kx, y0 / ky],
      [x0 / kx, y0 / ky],
    ],
  };
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
  const p = positionHeadingAt(f, t);
  return p ? [p[0], p[1]] : null;
}

/** [lon, lat, bearing° from north] of a flight at unix time t, or null. */
export function positionHeadingAt(f: FlightsGeoJSON["features"][number], t: number): [number, number, number] | null {
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
  const lat = a[1] + (b[1] - a[1]) * u;
  const bearing = (Math.atan2((b[0] - a[0]) * Math.cos((lat * Math.PI) / 180), b[1] - a[1]) * 180) / Math.PI;
  return [a[0] + (b[0] - a[0]) * u, lat, bearing];
}

/** Flights GeoJSON with a `heli` flag joined from the flight set's categories. */
export function withCategories(fc: FlightsGeoJSON | undefined, cats: Map<number, string | null | undefined>): FlightsGeoJSON | undefined {
  if (!fc) return fc;
  return {
    ...fc,
    features: fc.features.map((f) => ({ ...f, properties: { ...f.properties, heli: isHelicopter(cats.get(f.properties.idx)) } })),
  };
}

export function isHelicopter(category: string | null | undefined): boolean {
  return !!category && /heli|gyro|tiltrotor/i.test(category);
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
