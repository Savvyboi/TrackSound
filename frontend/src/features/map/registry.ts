import type { Map as MLMap } from "maplibre-gl";
import type { BBox } from "../../api/types";
import { round6 } from "../../lib/geo";

let current: MLMap | null = null;

export function registerMap(m: MLMap | null) {
  current = m;
  // handy for debugging from the browser console
  (window as unknown as { __trackSoundMap?: MLMap | null }).__trackSoundMap = m;
}

/** The visible map area as a region, or null when no map is shown. */
export function getMapBounds(): BBox | null {
  if (!current) return null;
  const b = current.getBounds();
  const lat_min = Math.max(-85, b.getSouth());
  const lat_max = Math.min(85, b.getNorth());
  let lon_min = b.getWest();
  let lon_max = b.getEast();
  if (lon_max - lon_min >= 360) {
    lon_min = -180;
    lon_max = 180;
  }
  return { lat_min: round6(lat_min), lat_max: round6(lat_max), lon_min: round6(lon_min), lon_max: round6(lon_max) };
}
