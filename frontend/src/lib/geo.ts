import type { Feature, FeatureCollection, Polygon } from "geojson";
import type { BBox } from "../api/types";

const R = 6371008.8;
const rad = Math.PI / 180;

export function haversine(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const dLat = (lat2 - lat1) * rad;
  const dLon = (lon2 - lon1) * rad;
  const a = Math.sin(dLat / 2) ** 2 + Math.cos(lat1 * rad) * Math.cos(lat2 * rad) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(a)));
}

export function bboxCenter(b: BBox): { lat: number; lon: number } {
  return { lat: (b.lat_min + b.lat_max) / 2, lon: (b.lon_min + b.lon_max) / 2 };
}

/** Width × height of a bbox in km (at its centre latitude). */
export function bboxSizeKm(b: BBox): { w: number; h: number } {
  const c = bboxCenter(b);
  return {
    w: haversine(c.lat, b.lon_min, c.lat, b.lon_max) / 1000,
    h: haversine(b.lat_min, c.lon, b.lat_max, c.lon) / 1000,
  };
}

/** Square-ish bbox of half-size `halfKm` around a point. */
export function bboxAround(lat: number, lon: number, halfKm: number): BBox {
  const dLat = halfKm / 111.32;
  const dLon = halfKm / (111.32 * Math.max(0.05, Math.cos(lat * rad)));
  return {
    lat_min: round6(Math.max(-90, lat - dLat)),
    lat_max: round6(Math.min(90, lat + dLat)),
    lon_min: round6(lon - dLon),
    lon_max: round6(lon + dLon),
  };
}

export function round6(v: number): number {
  return Math.round(v * 1e6) / 1e6;
}

export function normalizeBBox(a: { lat: number; lon: number }, b: { lat: number; lon: number }): BBox {
  return {
    lat_min: round6(Math.max(-90, Math.min(a.lat, b.lat))),
    lat_max: round6(Math.min(90, Math.max(a.lat, b.lat))),
    lon_min: round6(Math.min(a.lon, b.lon)),
    lon_max: round6(Math.max(a.lon, b.lon)),
  };
}

export function validBBox(b: Partial<BBox> | null | undefined): b is BBox {
  return (
    !!b &&
    [b.lat_min, b.lat_max, b.lon_min, b.lon_max].every((v) => typeof v === "number" && Number.isFinite(v)) &&
    (b.lat_min as number) < (b.lat_max as number) &&
    (b.lon_min as number) < (b.lon_max as number) &&
    (b.lat_min as number) >= -90 &&
    (b.lat_max as number) <= 90
  );
}

export function bboxPolygon(b: BBox): Feature<Polygon> {
  return {
    type: "Feature",
    properties: {},
    geometry: {
      type: "Polygon",
      coordinates: [
        [
          [b.lon_min, b.lat_min],
          [b.lon_max, b.lat_min],
          [b.lon_max, b.lat_max],
          [b.lon_min, b.lat_max],
          [b.lon_min, b.lat_min],
        ],
      ],
    },
  };
}

/** Geodesic-ish circle polygon (good enough at the km scale). */
export function circleRing(lat: number, lon: number, radiusM: number, n = 96): [number, number][] {
  const out: [number, number][] = [];
  const dLat = radiusM / 111320;
  const dLon = radiusM / (111320 * Math.max(0.05, Math.cos(lat * rad)));
  for (let i = 0; i <= n; i++) {
    const a = (i / n) * 2 * Math.PI;
    out.push([lon + dLon * Math.cos(a), lat + dLat * Math.sin(a)]);
  }
  return out;
}

export function fmtBBox(b: BBox): string {
  const ns = (v: number) => `${Math.abs(v).toFixed(3)}°${v >= 0 ? "N" : "S"}`;
  const ew = (v: number) => `${Math.abs(v).toFixed(3)}°${v >= 0 ? "E" : "W"}`;
  return `${ns(b.lat_min)}–${ns(b.lat_max)}, ${ew(b.lon_min)}–${ew(b.lon_max)}`;
}

export function fmtBBoxShort(b: BBox): string {
  const c = bboxCenter(b);
  const s = bboxSizeKm(b);
  return `${Math.abs(c.lat).toFixed(2)}°${c.lat >= 0 ? "N" : "S"} ${Math.abs(c.lon).toFixed(2)}°${c.lon >= 0 ? "E" : "W"} · ${Math.round(s.w)}×${Math.round(s.h)} km`;
}

export function emptyFC(): FeatureCollection {
  return { type: "FeatureCollection", features: [] };
}

/** Combine bboxes / points into one bounding box (for fitBounds). */
export function boundsOf(points: { lat: number; lon: number }[]): BBox | null {
  if (!points.length) return null;
  let a = Infinity, b = -Infinity, c = Infinity, d = -Infinity;
  for (const p of points) {
    a = Math.min(a, p.lat);
    b = Math.max(b, p.lat);
    c = Math.min(c, p.lon);
    d = Math.max(d, p.lon);
  }
  return { lat_min: a, lat_max: b, lon_min: c, lon_max: d };
}
