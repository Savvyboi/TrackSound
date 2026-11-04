import type { SpectrogramInfo } from "../../api/types";
import { TileCache, chooseLevel, type Tile } from "./tiles";

export type RasterResult = {
  /** Tiles the view needs, most important first (visible, then neighbours, then coarser fallbacks). */
  wanted: [number, number][];
  /** True when every visible column was drawn from the chosen level. */
  complete: boolean;
  level: number;
};

let colMax = new Uint8Array(1024);
let rowLo = new Int32Array(512);
let rowHi = new Int32Array(512);

/**
 * Draw the spectrogram for time [t0, t1] and frequency [fLo, fHi] into `out` (w × h packed RGBA).
 * Each pixel is the max over the tile cells it covers, so faint lines survive zooming out.
 * Columns with no loaded tile fall back to a coarser cached level, else `loading`.
 */
export function rasterize(
  out: Uint32Array,
  w: number,
  h: number,
  info: SpectrogramInfo,
  cache: TileCache,
  t0: number,
  t1: number,
  fLo: number,
  fHi: number,
  lut: Uint32Array,
  background: number,
  loading: number,
): RasterResult {
  const span = t1 - t0;
  const z = chooseLevel(info, span, w);
  const tf = info.tile_frames;
  const nBins = info.n_bins;

  // --- rows: bin range covered by each pixel row (y = 0 is the top = highest frequency)
  if (rowLo.length < h) {
    rowLo = new Int32Array(h);
    rowHi = new Int32Array(h);
  }
  const fSpan = fHi - fLo;
  let binMin = nBins;
  let binMax = -1;
  for (let y = 0; y < h; y++) {
    const fTop = fHi - (y / h) * fSpan;
    const fBot = fHi - ((y + 1) / h) * fSpan;
    let lo = Math.floor((fBot - info.f0) / info.df + 0.5);
    let hi = Math.floor((fTop - info.f0) / info.df + 0.5);
    if (hi < lo) hi = lo;
    if (hi < 0 || lo >= nBins) {
      rowLo[y] = 1;
      rowHi[y] = 0; // empty: outside the spectrogram
      continue;
    }
    if (lo < 0) lo = 0;
    if (hi > nBins - 1) hi = nBins - 1;
    rowLo[y] = lo;
    rowHi[y] = hi;
    if (lo < binMin) binMin = lo;
    if (hi > binMax) binMax = hi;
  }
  if (colMax.length < nBins) colMax = new Uint8Array(nBins);

  // --- which tiles we need
  const wanted: [number, number][] = [];
  const fpcZ = 2 ** z;
  const frameF = (t: number) => (t - info.t0) / info.dt + 0.5;
  const nTilesZ = cache.tilesAt(z);
  const firstTile = Math.max(0, Math.floor(Math.floor(frameF(t0)) / fpcZ / tf));
  const lastTile = Math.min(nTilesZ - 1, Math.floor(Math.floor(frameF(t1)) / fpcZ / tf));
  for (let i = firstTile; i <= lastTile; i++) wanted.push([z, i]);
  if (firstTile - 1 >= 0) wanted.push([z, firstTile - 1]);
  if (lastTile + 1 < nTilesZ) wanted.push([z, lastTile + 1]);
  // a coarse overview so something shows immediately while the detail loads
  const top = info.levels - 1;
  if (z !== top) {
    const zc = Math.min(top, z + 3);
    const fpcC = 2 ** zc;
    const a = Math.max(0, Math.floor(Math.floor(frameF(t0)) / fpcC / tf));
    const b = Math.min(cache.tilesAt(zc) - 1, Math.floor(Math.floor(frameF(t1)) / fpcC / tf));
    for (let i = a; i <= b; i++) wanted.push([zc, i]);
  }

  let complete = true;
  const touched = new Set<string>();

  for (let x = 0; x < w; x++) {
    const ta = t0 + (x / w) * span;
    const tb = t0 + ((x + 1) / w) * span;
    const kA = Math.floor(frameF(ta));
    let kB = Math.ceil(frameF(tb));
    if (kB <= kA) kB = kA + 1;
    if (kB <= 0 || kA >= info.n_frames || binMax < 0) {
      for (let y = 0; y < h; y++) out[y * w + x] = background;
      continue;
    }
    const fa = Math.max(0, kA);
    const fb = Math.min(info.n_frames, kB);

    // find a level with all needed tiles loaded: z first, then coarser
    let lvl = -1;
    for (let zz = z; zz <= top; zz++) {
      const fpc = 2 ** zz;
      const cA = Math.floor(fa / fpc);
      const cB = Math.max(cA + 1, Math.ceil(fb / fpc));
      const iA = Math.floor(cA / tf);
      const iB = Math.floor((cB - 1) / tf);
      let ok = true;
      for (let i = iA; i <= iB; i++) {
        if (!cache.has(zz, i)) {
          ok = false;
          break;
        }
      }
      if (ok) {
        lvl = zz;
        break;
      }
    }
    if (lvl < 0) {
      complete = false;
      for (let y = 0; y < h; y++) out[y * w + x] = rowHi[y] < rowLo[y] ? background : loading;
      continue;
    }
    if (lvl !== z) complete = false;

    // max over the columns this pixel covers, for the visible bins
    const fpc = 2 ** lvl;
    const cA = Math.floor(fa / fpc);
    const cB = Math.max(cA + 1, Math.ceil(fb / fpc));
    colMax.fill(0, binMin, binMax + 1);
    let tile: Tile | undefined;
    let tileIdx = -1;
    for (let c = cA; c < cB; c++) {
      const ti = Math.floor(c / tf);
      if (ti !== tileIdx) {
        tileIdx = ti;
        tile = cache.get(lvl, ti);
        if (tile) touched.add(TileCache.key(lvl, ti));
      }
      if (!tile) continue;
      const col = c - ti * tf;
      if (col >= tile.cols) continue;
      const d = tile.data;
      const cols = tile.cols;
      const rows = Math.min(tile.rows, nBins);
      const bEnd = Math.min(binMax, rows - 1);
      for (let b = binMin; b <= bEnd; b++) {
        const v = d[b * cols + col];
        if (v > colMax[b]) colMax[b] = v;
      }
    }
    for (let y = 0; y < h; y++) {
      const lo = rowLo[y];
      const hi = rowHi[y];
      if (hi < lo) {
        out[y * w + x] = background;
        continue;
      }
      let v = colMax[lo];
      for (let b = lo + 1; b <= hi; b++) if (colMax[b] > v) v = colMax[b];
      out[y * w + x] = lut[v];
    }
  }
  cache.touch([...touched]);
  return { wanted, complete, level: z };
}

/** dB value under a point, from whatever tile is cached (for the hover readout). */
export function sampleDb(info: SpectrogramInfo, cache: TileCache, t: number, f: number, preferLevel: number): number | null {
  const frame = Math.floor((t - info.t0) / info.dt + 0.5);
  const bin = Math.floor((f - info.f0) / info.df + 0.5);
  if (frame < 0 || frame >= info.n_frames || bin < 0 || bin >= info.n_bins) return null;
  for (let z = preferLevel; z < info.levels; z++) {
    const fpc = 2 ** z;
    const c = Math.floor(frame / fpc);
    const ti = Math.floor(c / info.tile_frames);
    const tile = cache.get(z, ti);
    if (!tile) continue;
    const col = c - ti * info.tile_frames;
    if (col >= tile.cols || bin >= tile.rows) return null;
    const v = tile.data[bin * tile.cols + col];
    return info.db_min + (v / 255) * (info.db_max - info.db_min);
  }
  return null;
}
