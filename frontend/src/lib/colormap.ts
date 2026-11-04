// 256-entry colour maps for the spectrogram, built from published control points
// (matplotlib's magma / inferno / viridis, sampled at 0, 0.1, ..., 1).

export type ColormapName = "magma" | "inferno" | "viridis" | "gray";

export const COLORMAPS: { id: ColormapName; label: string }[] = [
  { id: "magma", label: "Magma" },
  { id: "inferno", label: "Inferno" },
  { id: "viridis", label: "Viridis" },
  { id: "gray", label: "Grayscale" },
];

const STOPS: Record<ColormapName, string[]> = {
  magma: ["#000004", "#140e36", "#3b0f70", "#641a80", "#8c2981", "#b73779", "#de4968", "#f7705c", "#fe9f6d", "#fecf92", "#fcfdbf"],
  inferno: ["#000004", "#160b39", "#420a68", "#6a176e", "#932667", "#bc3754", "#dd513a", "#f37819", "#fca50a", "#f6d746", "#fcffa4"],
  viridis: ["#440154", "#482475", "#414487", "#355f8d", "#2a788e", "#21918c", "#22a884", "#44bf70", "#7ad151", "#bddf26", "#fde725"],
  gray: ["#000000", "#ffffff"],
};

function hexToRgb(h: string): [number, number, number] {
  const n = parseInt(h.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

const rgbCache = new Map<ColormapName, Uint8Array>();

/** 256 × RGB for a colour map. */
export function colormapRGB(name: ColormapName): Uint8Array {
  const hit = rgbCache.get(name);
  if (hit) return hit;
  const stops = STOPS[name].map(hexToRgb);
  const out = new Uint8Array(256 * 3);
  const n = stops.length - 1;
  for (let i = 0; i < 256; i++) {
    const x = (i / 255) * n;
    const k = Math.min(n - 1, Math.floor(x));
    const u = x - k;
    const a = stops[k];
    const b = stops[k + 1];
    out[i * 3] = Math.round(a[0] + (b[0] - a[0]) * u);
    out[i * 3 + 1] = Math.round(a[1] + (b[1] - a[1]) * u);
    out[i * 3 + 2] = Math.round(a[2] + (b[2] - a[2]) * u);
  }
  rgbCache.set(name, out);
  return out;
}

const littleEndian = new Uint8Array(new Uint32Array([0x0a0b0c0d]).buffer)[0] === 0x0d;

export function packRGBA(r: number, g: number, b: number, a = 255): number {
  return littleEndian ? ((a << 24) | (b << 16) | (g << 8) | r) >>> 0 : ((r << 24) | (g << 16) | (b << 8) | a) >>> 0;
}

/**
 * LUT from tile byte (0..255) to packed RGBA, with the contrast window [lo, hi] dB applied.
 * Bytes map to dB linearly: db = dbMin + byte / 255 * (dbMax - dbMin).
 */
export function buildByteLut(name: ColormapName, dbMin: number, dbMax: number, lo: number, hi: number, gamma = 1): Uint32Array {
  const rgb = colormapRGB(name);
  const lut = new Uint32Array(256);
  const span = Math.max(1e-6, hi - lo);
  for (let b = 0; b < 256; b++) {
    const db = dbMin + (b / 255) * (dbMax - dbMin);
    let u = (db - lo) / span;
    u = u < 0 ? 0 : u > 1 ? 1 : u;
    if (gamma !== 1) u = Math.pow(u, gamma);
    const k = Math.round(u * 255) * 3;
    lut[b] = packRGBA(rgb[k], rgb[k + 1], rgb[k + 2]);
  }
  return lut;
}

/** CSS linear-gradient of a colour map (for legends and pickers). */
export function colormapGradient(name: ColormapName, dir = "to right"): string {
  const stops = STOPS[name];
  return `linear-gradient(${dir}, ${stops.map((s, i) => `${s} ${((i / (stops.length - 1)) * 100).toFixed(0)}%`).join(", ")})`;
}

/** Colour at u in [0, 1] as "rgb(r g b)". */
export function colormapCss(name: ColormapName, u: number): string {
  const rgb = colormapRGB(name);
  const k = Math.round(Math.max(0, Math.min(1, u)) * 255) * 3;
  return `rgb(${rgb[k]} ${rgb[k + 1]} ${rgb[k + 2]})`;
}
