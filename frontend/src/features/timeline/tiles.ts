import type { SpectrogramInfo } from "../../api/types";

export type Tile = {
  z: number;
  i: number;
  rows: number;
  cols: number;
  frame0: number;
  fpc: number; // level-0 frames per column (2**z)
  data: Uint8Array; // rows × cols, row-major, row 0 = lowest frequency
};

const MAX_INFLIGHT = 6;

/**
 * Decoded spectrogram tiles for one recording, kept in an LRU map. `want()` takes the tiles
 * the current view needs (most important first); stale requests are dropped from the queue.
 */
export class TileCache {
  private tiles = new Map<string, Tile>();
  private inflight = new Set<string>();
  private failed = new Map<string, number>();
  private queue: [number, number][] = [];
  private disposed = false;
  onLoad: () => void = () => {};

  constructor(
    readonly caseId: string,
    readonly version: string,
    readonly info: SpectrogramInfo,
    private maxTiles = 160,
  ) {}

  static key(z: number, i: number) {
    return `${z}:${i}`;
  }

  /** Number of tiles at level z. */
  tilesAt(z: number): number {
    const cols = Math.ceil(this.info.n_frames / 2 ** z);
    return Math.max(1, Math.ceil(cols / this.info.tile_frames));
  }

  get(z: number, i: number): Tile | undefined {
    return this.tiles.get(TileCache.key(z, i));
  }

  has(z: number, i: number): boolean {
    return this.tiles.has(TileCache.key(z, i));
  }

  /** Mark tiles as recently used so the LRU keeps them. */
  touch(keys: string[]) {
    for (const k of keys) {
      const t = this.tiles.get(k);
      if (t) {
        this.tiles.delete(k);
        this.tiles.set(k, t);
      }
    }
  }

  want(list: [number, number][]) {
    const now = performance.now();
    this.queue = list.filter(([z, i]) => {
      const k = TileCache.key(z, i);
      if (this.tiles.has(k) || this.inflight.has(k)) return false;
      const f = this.failed.get(k);
      return !(f && now - f < 4000);
    });
    this.pump();
  }

  get loading(): boolean {
    return this.inflight.size > 0;
  }

  dispose() {
    this.disposed = true;
    this.queue = [];
    this.tiles.clear();
  }

  private pump() {
    while (this.inflight.size < MAX_INFLIGHT && this.queue.length) {
      const [z, i] = this.queue.shift()!;
      void this.fetchTile(z, i);
    }
  }

  private async fetchTile(z: number, i: number) {
    const k = TileCache.key(z, i);
    this.inflight.add(k);
    try {
      const url = `/api/cases/${encodeURIComponent(this.caseId)}/spectrogram/tile?level=${z}&index=${i}&v=${encodeURIComponent(this.version)}`;
      const res = await fetch(url);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const buf = await res.arrayBuffer();
      if (this.disposed) return;
      const rows = Number(res.headers.get("X-Rows")) || this.info.n_bins;
      const cols = Number(res.headers.get("X-Cols")) || Math.floor(buf.byteLength / rows);
      const frame0Hdr = res.headers.get("X-Frame0");
      const fpcHdr = res.headers.get("X-Frames-Per-Col");
      const fpc = fpcHdr ? Number(fpcHdr) : 2 ** z;
      const frame0 = frame0Hdr != null ? Number(frame0Hdr) : i * this.info.tile_frames * fpc;
      const data = new Uint8Array(buf);
      if (data.length < rows * cols) throw new Error("short tile");
      this.tiles.set(k, { z, i, rows, cols, frame0, fpc, data });
      this.failed.delete(k);
      this.evict();
      this.onLoad();
    } catch {
      if (!this.disposed) this.failed.set(k, performance.now());
    } finally {
      this.inflight.delete(k);
      if (!this.disposed) this.pump();
    }
  }

  private evict() {
    while (this.tiles.size > this.maxTiles) {
      const first = this.tiles.keys().next().value;
      if (first === undefined) break;
      this.tiles.delete(first);
    }
  }
}

/** Pyramid level giving about one column per pixel or better for this view. */
export function chooseLevel(info: SpectrogramInfo, span: number, widthPx: number): number {
  const framesPerPx = span / info.dt / Math.max(1, widthPx);
  const z = Math.floor(Math.log2(Math.max(1, framesPerPx)));
  return Math.max(0, Math.min(info.levels - 1, z));
}
