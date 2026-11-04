import type {
  AircraftEvent,
  CandidateCurves,
  EnfAnalysis,
  EnfMatch,
  EventStatus,
  Flash,
  SpectrogramInfo,
  ThunderEnvelope,
  ThunderOnset,
} from "../../api/types";
import { buildByteLut, packRGBA } from "../../lib/colormap";
import { clamp, fmtHz, fmtRec } from "../../lib/format";
import { idealCurve } from "../../lib/physics";
import { themeStore } from "../../lib/theme";
import { fmtClock, tzAbbr } from "../../lib/tz";
import {
  panTime,
  setFreq,
  setView,
  ws,
  zoomFreqAt,
  zoomTimeAt,
  type BoxDraft,
  type TimelineMode,
  type Tool,
} from "../../state/workspace";
import {
  AXIS_H,
  CLOCK_H,
  FONT,
  FONT_SMALL,
  GUTTER_LEFT,
  GUTTER_RIGHT,
  GUTTER_TOP,
  drawTimeAxis,
  drawValueAxis,
  readTheme,
  type Geom,
  type Theme,
} from "./axes";
import { histPercentile, paint, rasterize, sampleDb } from "./rasterize";
import { TileCache } from "./tiles";

export type MarkerKind = "onset" | "flash";

export type Scene = {
  mode: TimelineMode;
  caseId: string;
  version: string;
  info: SpectrogramInfo | null;
  duration: number;
  clock: { startUtc: number; tz: string } | null;
  c: number;
  tool: Tool;
  // aircraft
  events: AircraftEvent[];
  eventNumber: Map<string, number>;
  selectedEventId: string | null;
  checkedEventIds: string[];
  candCurves: CandidateCurves["events"] | null;
  /** icao → label (registration / type) for the modelled curves */
  candLabels?: Map<string, string>;
  pendingFit: BoxDraft | null;
  showOverlays: boolean;
  showWeak: boolean;
  // lightning
  envOverview: ThunderEnvelope | null;
  envDetail: ThunderEnvelope | null;
  onsets: ThunderOnset[];
  flashes: Flash[];
  selectedOnsetId: string | null;
  selectedFlashId: string | null;
  // ENF
  enfTrace: EnfAnalysis["trace"] | null;
  enfNominal: number | null;
  enfOverlay: EnfMatch["overlay"] | null;
};

export type Handlers = {
  onSelectEvent: (id: string | null) => void;
  onBox: (b: BoxDraft) => void;
  onAddMarker: (kind: MarkerKind, t: number) => void;
  onMoveMarker: (kind: MarkerKind, id: string, t: number) => void;
  onSelectMarker: (kind: MarkerKind, id: string | null) => void;
};

export const STATUS_COLOR: Record<EventStatus, string> = {
  candidate: "#fbbf24",
  accepted: "#4ade80",
  rejected: "#f87171",
};
export const BAND_COLORS = ["#7dd3fc", "#c4b5fd", "#f9a8d4", "#fcd34d", "#86efac"];
const ENF_COLOR = "#c084fc";
const REF_COLOR = "#fbbf24";
const CAND_COLOR = "#67e8f9";
const BG = packRGBA(4, 5, 7);
const LOADING = packRGBA(17, 19, 25);

type Drag =
  | { kind: "pan"; x: number; y: number; t0: number; t1: number; flo: number; fhi: number; area: "plot" | "faxis" | "taxis"; moved: boolean }
  | { kind: "box"; xa: number; ya: number; xb: number; yb: number }
  | { kind: "marker"; mk: MarkerKind; id: string; t: number; x: number; moved: boolean };

export class TimelineCanvas {
  scene: Scene;
  handlers: Handlers;
  private bctx: CanvasRenderingContext2D;
  private octx: CanvasRenderingContext2D;
  private cache: TileCache | null = null;
  private g: Geom = { W: 0, H: 0, x0: 0, x1: 0, y0: 0, y1: 0 };
  private dpr = 1;
  private img: ImageData | null = null;
  private lut: Uint32Array | null = null;
  private lutKey = "";
  private baseDirty = true;
  private raf = 0;
  private th: Theme;
  private hover: { x: number; y: number } | null = null;
  private drag: Drag | null = null;
  private cleanups: (() => void)[] = [];
  private level = 0;
  private yr: { lo: number; hi: number } = { lo: 0, hi: 1 };
  private hoverEventId: string | null = null;
  private snap: unknown[] = [];
  private disposed = false;

  constructor(
    private container: HTMLElement,
    private base: HTMLCanvasElement,
    private over: HTMLCanvasElement,
    private readout: HTMLElement,
    scene: Scene,
    handlers: Handlers,
  ) {
    this.scene = scene;
    this.handlers = handlers;
    this.bctx = base.getContext("2d", { alpha: false })!;
    this.octx = over.getContext("2d")!;
    this.th = readTheme(container);
    this.resetCache();

    const ro = new ResizeObserver(() => this.resize());
    ro.observe(container);
    this.cleanups.push(() => ro.disconnect());

    this.cleanups.push(
      ws.subscribe(() => {
        const s = ws.get();
        const snap = [s.view, s.freq, s.colormap, s.contrast, s.contrastAuto];
        const baseChanged = snap.some((v, i) => v !== this.snap[i]);
        this.snap = snap;
        this.request(baseChanged);
      }),
    );
    this.cleanups.push(
      themeStore.subscribe(() => {
        requestAnimationFrame(() => {
          this.th = readTheme(container);
          this.request(true);
        });
      }),
    );
    void document.fonts?.ready.then(() => this.request(false));

    const on = <K extends keyof HTMLElementEventMap>(el: HTMLElement, type: K, fn: (e: HTMLElementEventMap[K]) => void, opts?: AddEventListenerOptions) => {
      el.addEventListener(type, fn as EventListener, opts);
      this.cleanups.push(() => el.removeEventListener(type, fn as EventListener, opts));
    };
    on(over, "pointerdown", (e) => this.onDown(e));
    on(over, "pointermove", (e) => this.onMove(e));
    on(over, "pointerup", (e) => this.onUp(e));
    on(over, "pointercancel", () => {
      this.drag = null;
      this.request(false);
    });
    on(over, "pointerleave", () => {
      if (this.drag) return;
      this.hover = null;
      this.readout.style.opacity = "0";
      if (ws.get().hoverT !== null) ws.set({ hoverT: null });
      this.request(false);
    });
    on(over, "wheel", (e) => this.onWheel(e), { passive: false });
    on(over, "dblclick", (e) => this.onDblClick(e));
    this.resize();
  }

  dispose() {
    this.disposed = true;
    cancelAnimationFrame(this.raf);
    this.cleanups.forEach((f) => f());
    this.cache?.dispose();
  }

  setScene(scene: Scene) {
    const prev = this.scene;
    this.scene = scene;
    const geomChange = !!prev.clock !== !!scene.clock;
    if (prev.caseId !== scene.caseId || prev.version !== scene.version || prev.info !== scene.info) this.resetCache();
    if (geomChange) this.resize();
    this.request(prev.mode !== scene.mode || prev.info !== scene.info || geomChange);
    this.updateCursor();
  }

  private resetCache() {
    this.cache?.dispose();
    const s = this.scene;
    this.cache = s.info ? new TileCache(s.caseId, s.version, s.info) : null;
    if (this.cache) this.cache.onLoad = () => this.request(true);
  }

  private resize() {
    const r = this.container.getBoundingClientRect();
    const W = Math.max(10, Math.floor(r.width));
    const H = Math.max(10, Math.floor(r.height));
    this.dpr = Math.min(2, window.devicePixelRatio || 1);
    const clock = this.scene.clock ? CLOCK_H : 0;
    this.g = { W, H, x0: GUTTER_LEFT, x1: W - GUTTER_RIGHT, y0: GUTTER_TOP, y1: H - AXIS_H - clock };
    this.over.width = Math.round(W * this.dpr);
    this.over.height = Math.round(H * this.dpr);
    this.over.style.width = `${W}px`;
    this.over.style.height = `${H}px`;
    const g = this.g;
    Object.assign(this.base.style, {
      left: `${g.x0}px`,
      top: `${g.y0}px`,
      width: `${Math.max(1, g.x1 - g.x0)}px`,
      height: `${Math.max(1, g.y1 - g.y0)}px`,
    });
    this.request(true);
  }

  request(base: boolean) {
    if (base) this.baseDirty = true;
    if (!this.raf && !this.disposed) this.raf = requestAnimationFrame(() => this.draw());
  }

  // ---- coordinate transforms -----------------------------------------------------------

  private tx(t: number) {
    const v = ws.get().view;
    return this.g.x0 + ((t - v.t0) / (v.t1 - v.t0)) * (this.g.x1 - this.g.x0);
  }
  private xt(x: number) {
    const v = ws.get().view;
    return v.t0 + ((x - this.g.x0) / (this.g.x1 - this.g.x0)) * (v.t1 - v.t0);
  }
  private fy(f: number) {
    const fr = ws.get().freq;
    return this.g.y1 - ((f - fr.lo) / (fr.hi - fr.lo)) * (this.g.y1 - this.g.y0);
  }
  private yf(y: number) {
    const fr = ws.get().freq;
    return fr.lo + ((this.g.y1 - y) / (this.g.y1 - this.g.y0)) * (fr.hi - fr.lo);
  }
  private vy(v: number) {
    return this.g.y1 - ((v - this.yr.lo) / (this.yr.hi - this.yr.lo)) * (this.g.y1 - this.g.y0);
  }
  private yv(y: number) {
    return this.yr.lo + ((this.g.y1 - y) / (this.g.y1 - this.g.y0)) * (this.yr.hi - this.yr.lo);
  }

  // ---- drawing ------------------------------------------------------------------------

  private draw() {
    this.raf = 0;
    if (this.disposed) return;
    if (this.baseDirty) this.drawBase();
    this.drawOverlay();
  }

  private getLut(info: SpectrogramInfo, lo: number, hi: number) {
    const s = ws.get();
    const key = `${s.colormap}|${lo}|${hi}|${info.db_min}|${info.db_max}`;
    if (!this.lut || key !== this.lutKey) {
      this.lut = buildByteLut(s.colormap, info.db_min, info.db_max, lo, hi);
      this.lutKey = key;
    }
    return this.lut;
  }

  /** Contrast window: manual, or picked from the visible pixels (floor above the noise, ceiling at the brightest lines). */
  private contrastFor(info: SpectrogramInfo, hist: Uint32Array, complete: boolean): { lo: number; hi: number } {
    const s = ws.get();
    if (!s.contrastAuto) return s.contrast;
    const cur = s.autoContrast;
    const pa = histPercentile(hist, 0.62);
    const pb = histPercentile(hist, 0.9985);
    if (pa == null || pb == null) return cur ?? s.contrast;
    const db = (b: number) => info.db_min + (b / 255) * (info.db_max - info.db_min);
    const lo = Math.round((db(pa) + 0.5) * 2) / 2;
    const hi = Math.round(Math.max(lo + 13, db(pb) + 1) * 2) / 2;
    if (!cur || (complete && (Math.abs(cur.lo - lo) > 0.75 || Math.abs(cur.hi - hi) > 1.5))) {
      const next = { lo, hi };
      queueMicrotask(() => ws.set({ autoContrast: next }));
      return next;
    }
    return cur;
  }

  private drawBase() {
    this.baseDirty = false;
    const s = this.scene;
    const show = s.mode === "spectrogram" && !!s.info && !!this.cache;
    this.base.style.display = show ? "block" : "none";
    if (!show || !s.info || !this.cache) return;
    const g = this.g;
    const w = Math.max(1, Math.round(g.x1 - g.x0));
    const h = Math.max(1, Math.round(g.y1 - g.y0));
    if (this.base.width !== w || this.base.height !== h || !this.img) {
      this.base.width = w;
      this.base.height = h;
      this.img = this.bctx.createImageData(w, h);
    }
    const { view, freq } = ws.get();
    const out = new Uint32Array(this.img.data.buffer);
    const res = rasterize(w, h, s.info, this.cache, view.t0, view.t1, freq.lo, freq.hi);
    const cw = this.contrastFor(s.info, res.hist, res.complete);
    paint(out, w * h, this.getLut(s.info, cw.lo, cw.hi), BG, LOADING);
    this.bctx.putImageData(this.img, 0, 0);
    this.level = res.level;
    this.cache.want(res.wanted);
  }

  private clipPlot(ctx: CanvasRenderingContext2D) {
    const g = this.g;
    ctx.beginPath();
    ctx.rect(g.x0, g.y0, g.x1 - g.x0, g.y1 - g.y0);
    ctx.clip();
  }

  private drawOverlay() {
    const ctx = this.octx;
    const g = this.g;
    const s = this.scene;
    const st = ws.get();
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.clearRect(0, 0, g.W, g.H);

    if (s.mode !== "spectrogram" || !s.info) {
      ctx.fillStyle = "#040507";
      ctx.fillRect(g.x0, g.y0, g.x1 - g.x0, g.y1 - g.y0);
    }

    // value axis + grid
    if (s.mode === "spectrogram") {
      drawValueAxis(ctx, g, st.freq.lo, st.freq.hi, this.th, "Hz", { minPx: 30 });
    } else if (s.mode === "thunder") {
      this.computeEnvRange();
      drawValueAxis(ctx, g, this.yr.lo, this.yr.hi, this.th, "dB", { minPx: 30 });
    } else {
      this.computeEnfRange();
      drawValueAxis(ctx, g, this.yr.lo, this.yr.hi, this.th, "Hz", {
        minPx: 28,
        format: (v, step) => v.toFixed(Math.max(2, Math.ceil(-Math.log10(step) - 1e-9))),
      });
    }
    drawTimeAxis(ctx, g, st.view.t0, st.view.t1, this.th, s.clock, g.y0);

    ctx.save();
    this.clipPlot(ctx);
    if (s.mode === "spectrogram") this.drawAircraft(ctx);
    else if (s.mode === "thunder") this.drawThunder(ctx);
    else this.drawEnf(ctx);
    this.drawPlayback(ctx);
    this.drawHover(ctx);
    ctx.restore();

    // view edge shading when the recording ends inside the view
    this.updateReadout();
  }

  // ---- aircraft overlays --------------------------------------------------------------

  /** Screen-space polylines of a curve (split where it leaves the time window or has gaps). */
  private curveSegments(t: ArrayLike<number>, f: ArrayLike<number>, h: number, tMin: number, tMax: number): Float64Array[] {
    const v = ws.get().view;
    const lo = Math.max(tMin, v.t0 - (v.t1 - v.t0) * 0.05);
    const hi = Math.min(tMax, v.t1 + (v.t1 - v.t0) * 0.05);
    const segs: Float64Array[] = [];
    let cur: number[] = [];
    let lx = -Infinity;
    let ly = -Infinity;
    const flush = () => {
      if (cur.length >= 4) segs.push(Float64Array.from(cur));
      cur = [];
      lx = -Infinity;
    };
    for (let i = 0; i < t.length; i++) {
      const tt = t[i];
      if (tt < lo || tt > hi || !Number.isFinite(f[i])) {
        flush();
        continue;
      }
      const x = this.tx(tt);
      const y = this.fy(h * f[i]);
      // skip points closer than ~1.5 px (keeps normals stable)
      if (Math.abs(x - lx) + Math.abs(y - ly) < 1.5) continue;
      cur.push(x, y);
      lx = x;
      ly = y;
    }
    flush();
    return segs;
  }

  /** Resample a polyline at fixed arc-length steps (stable normals for the rails). */
  private static resample(seg: Float64Array, step: number): Float64Array {
    const out: number[] = [seg[0], seg[1]];
    let px = seg[0];
    let py = seg[1];
    let carry = 0;
    for (let i = 2; i < seg.length; i += 2) {
      const x = seg[i];
      const y = seg[i + 1];
      let dx = x - px;
      let dy = y - py;
      let len = Math.hypot(dx, dy);
      while (carry + len >= step) {
        const u = (step - carry) / len;
        px += dx * u;
        py += dy * u;
        out.push(px, py);
        dx = x - px;
        dy = y - py;
        len = Math.hypot(dx, dy);
        carry = 0;
      }
      carry += len;
      px = x;
      py = y;
    }
    out.push(seg[seg.length - 2], seg[seg.length - 1]);
    return Float64Array.from(out);
  }

  /** Offset a polyline along its normals by d px (a "rail" beside the curve). */
  private static rail(seg: Float64Array, d: number): Float64Array {
    const n = seg.length / 2;
    const out = new Float64Array(seg.length);
    for (let i = 0; i < n; i++) {
      const a = Math.max(0, i - 2);
      const b = Math.min(n - 1, i + 2);
      let tx = seg[b * 2] - seg[a * 2];
      let ty = seg[b * 2 + 1] - seg[a * 2 + 1];
      const len = Math.hypot(tx, ty) || 1;
      tx /= len;
      ty /= len;
      out[i * 2] = seg[i * 2] - ty * d;
      out[i * 2 + 1] = seg[i * 2 + 1] + tx * d;
    }
    return out;
  }

  private static polyline(ctx: CanvasRenderingContext2D, p: Float64Array) {
    ctx.moveTo(p[0], p[1]);
    for (let i = 2; i < p.length; i += 2) ctx.lineTo(p[i], p[i + 1]);
  }

  /**
   * Draw a fitted curve as two rails either side of it, so the sweep itself stays visible
   * between them (a line on top would hide exactly what you want to judge).
   */
  private drawRails(ctx: CanvasRenderingContext2D, raw: Float64Array[], gap: number, color: string, width: number, opts: { dash?: number[]; glow?: boolean; fill?: number } = {}) {
    for (const r of raw) {
      const seg = TimelineCanvas.resample(r, 3);
      if (seg.length < 4) continue;
      const up = TimelineCanvas.rail(seg, -gap);
      const dn = TimelineCanvas.rail(seg, gap);
      if (opts.fill) {
        ctx.beginPath();
        TimelineCanvas.polyline(ctx, up);
        for (let i = dn.length - 2; i >= 0; i -= 2) ctx.lineTo(dn[i], dn[i + 1]);
        ctx.closePath();
        ctx.fillStyle = hexA(color, opts.fill);
        ctx.fill();
      }
      ctx.beginPath();
      TimelineCanvas.polyline(ctx, up);
      TimelineCanvas.polyline(ctx, dn);
      ctx.setLineDash(opts.dash ?? []);
      // dark casing for legibility on bright areas
      ctx.strokeStyle = "rgba(0,0,0,0.55)";
      ctx.lineWidth = width + 2;
      ctx.stroke();
      ctx.strokeStyle = color;
      ctx.lineWidth = width;
      if (opts.glow) {
        ctx.shadowColor = color;
        ctx.shadowBlur = 8;
      }
      ctx.stroke();
      ctx.shadowBlur = 0;
      ctx.setLineDash([]);
    }
  }

  /** Events drawn on the spectrogram: weak noise fits are hidden unless asked for (or in use). */
  private visibleEvents(): AircraftEvent[] {
    const s = this.scene;
    if (!s.showOverlays) return s.events.filter((e) => e.id === s.selectedEventId);
    if (s.showWeak) return s.events;
    return s.events.filter(
      (e) => !isWeak(e) || e.status === "accepted" || e.origin === "manual" || e.id === s.selectedEventId || s.checkedEventIds.includes(e.id),
    );
  }

  private drawAircraft(ctx: CanvasRenderingContext2D) {
    const s = this.scene;
    const st = ws.get();
    const sel = s.selectedEventId;
    const events = this.visibleEvents();
    const order = [...events].sort((a, b) => Number(a.id === sel) - Number(b.id === sel) || statusRank(a.status) - statusRank(b.status));

    // selected event window
    const selEv = s.events.find((e) => e.id === sel);
    if (selEv) {
      const xa = this.tx(selEv.t_start);
      const xb = this.tx(selEv.t_end);
      ctx.fillStyle = "rgba(255,255,255,0.035)";
      ctx.fillRect(xa, this.g.y0, xb - xa, this.g.y1 - this.g.y0);
      ctx.strokeStyle = "rgba(255,255,255,0.18)";
      ctx.setLineDash([3, 4]);
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(Math.round(xa) + 0.5, this.g.y0);
      ctx.lineTo(Math.round(xa) + 0.5, this.g.y1);
      ctx.moveTo(Math.round(xb) + 0.5, this.g.y0);
      ctx.lineTo(Math.round(xb) + 0.5, this.g.y1);
      ctx.stroke();
      ctx.setLineDash([]);
    }

    const dimOthers = !!s.candCurves;
    for (const ev of order) {
      const curve = idealCurve(ev, s.c);
      const isSel = ev.id === sel;
      const isHover = ev.id === this.hoverEventId;
      const color = STATUS_COLOR[ev.status];
      const alpha = ev.status === "rejected" ? 0.4 : isWeak(ev) && ev.status !== "accepted" ? 0.55 : dimOthers && !isSel ? 0.5 : 1;
      const tMin = ev.t_start - 60;
      const tMax = ev.t_end + 60;
      ctx.globalAlpha = alpha;
      for (let h = 1; h <= Math.max(1, ev.harmonics); h++) {
        const segs = this.curveSegments(curve.t, curve.f, h, tMin, tMax);
        this.drawRails(ctx, segs, isSel ? 6 : 4.5, color, isSel ? 1.5 : isHover ? 1.4 : 1, {
          dash: h > 1 ? [5, 4] : undefined,
          glow: isSel,
          fill: isSel ? 0.07 : isHover ? 0.05 : 0,
        });
      }
      ctx.globalAlpha = 1;
      // number badge above the curve at the CPA
      const n = s.eventNumber.get(ev.id);
      const x = this.tx(ev.t_cpa);
      const y = this.fy(ev.f0);
      if (n != null && x > this.g.x0 - 20 && x < this.g.x1 + 20) {
        const checked = s.checkedEventIds.includes(ev.id);
        this.badge(ctx, x + 8, y - 16, isSel ? `${n} · ${ev.f0.toFixed(1)} Hz` : String(n), color, isSel || checked, alpha);
      }
    }

    // modelled curves of the selected search candidate
    if (s.candCurves) {
      for (const c of s.candCurves) {
        const f = smoothed(c.f);
        for (let h = 1; h <= Math.max(1, c.harmonics); h++) {
          const segs = this.curveSegments(c.t, f, h, -Infinity, Infinity);
          this.drawRails(ctx, segs, 3.5, CAND_COLOR, 1.4, { dash: h > 1 ? [6, 4] : undefined, glow: true });
        }
        // label at the curve's midpoint
        const mid = Math.floor(c.t.length / 2);
        if (c.t.length) {
          const x = this.tx(c.t[mid]);
          const y = this.fy(f[mid]);
          this.badge(ctx, x + 8, y + 18, s.candLabels?.get(c.icao) ?? c.icao.toUpperCase(), CAND_COLOR, true, 1);
        }
      }
    }

    // fit in progress
    if (s.pendingFit) this.drawBoxRect(ctx, s.pendingFit, "rgba(45,212,191,0.95)", "Fitting…", true);
    // drawn box awaiting confirmation
    if (st.boxDraft) this.drawBoxRect(ctx, st.boxDraft, "rgba(255,255,255,0.95)", null, false);
    // box being dragged
    if (this.drag?.kind === "box") {
      const d = this.drag;
      const b = this.boxFromDrag(d);
      this.drawBoxRect(ctx, b, "rgba(255,255,255,0.95)", `${fmtRec(b.t0, 1)}–${fmtRec(b.t1, 1)} · ${b.f0.toFixed(0)}–${b.f1.toFixed(0)} Hz`, false);
    }
  }

  private boxFromDrag(d: { xa: number; ya: number; xb: number; yb: number }): BoxDraft {
    const xa = clamp(Math.min(d.xa, d.xb), this.g.x0, this.g.x1);
    const xb = clamp(Math.max(d.xa, d.xb), this.g.x0, this.g.x1);
    const ya = clamp(Math.min(d.ya, d.yb), this.g.y0, this.g.y1);
    const yb = clamp(Math.max(d.ya, d.yb), this.g.y0, this.g.y1);
    return { t0: this.xt(xa), t1: this.xt(xb), f0: this.yf(yb), f1: this.yf(ya) };
  }

  private drawBoxRect(ctx: CanvasRenderingContext2D, b: BoxDraft, color: string, label: string | null, pulse: boolean) {
    const x = this.tx(b.t0);
    const y = this.fy(b.f1);
    const w = this.tx(b.t1) - x;
    const h = this.fy(b.f0) - y;
    ctx.fillStyle = pulse ? "rgba(45,212,191,0.10)" : "rgba(255,255,255,0.07)";
    ctx.fillRect(x, y, w, h);
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.25;
    ctx.setLineDash(pulse ? [5, 4] : [4, 3]);
    if (pulse) ctx.lineDashOffset = -((performance.now() / 40) % 18);
    ctx.strokeRect(Math.round(x) + 0.5, Math.round(y) + 0.5, Math.round(w), Math.round(h));
    ctx.setLineDash([]);
    ctx.lineDashOffset = 0;
    if (label) this.badge(ctx, x + 4, y - 9, label, color, true, 1);
    if (pulse) this.request(false);
  }

  private badge(ctx: CanvasRenderingContext2D, x: number, y: number, text: string, color: string, strong: boolean, alpha: number) {
    ctx.save();
    ctx.globalAlpha = alpha;
    ctx.font = FONT_SMALL;
    const w = ctx.measureText(text).width + 9;
    const h = 15;
    const bx = Math.round(x);
    const by = Math.round(y - h / 2);
    ctx.fillStyle = strong ? color : "rgba(6,8,12,0.82)";
    ctx.strokeStyle = color;
    ctx.lineWidth = 1;
    roundRect(ctx, bx, by, w, h, 4);
    ctx.fill();
    if (!strong) ctx.stroke();
    ctx.fillStyle = strong ? "#06080c" : color;
    ctx.textAlign = "left";
    ctx.textBaseline = "middle";
    ctx.fillText(text, bx + 4.5, by + h / 2 + 0.5);
    ctx.restore();
  }

  // ---- thunder ------------------------------------------------------------------------

  private envFor(): ThunderEnvelope | null {
    const s = this.scene;
    const v = ws.get().view;
    const d = s.envDetail;
    if (d && d.t.length > 1 && d.t[0] <= v.t0 + (v.t1 - v.t0) * 0.02 && d.t[d.t.length - 1] >= v.t1 - (v.t1 - v.t0) * 0.02) return d;
    return s.envOverview ?? d;
  }

  private computeEnvRange() {
    const env = this.envFor();
    const v = ws.get().view;
    let lo = Infinity;
    let hi = -Infinity;
    if (env) {
      for (const b of env.bands) {
        for (let i = 0; i < env.t.length; i++) {
          const t = env.t[i];
          if (t < v.t0 || t > v.t1) continue;
          const x = b.db[i];
          if (!Number.isFinite(x)) continue;
          if (x < lo) lo = x;
          if (x > hi) hi = x;
        }
      }
    }
    if (!Number.isFinite(lo)) {
      lo = -60;
      hi = 0;
    }
    const pad = Math.max(2, (hi - lo) * 0.08);
    this.yr = { lo: lo - pad, hi: hi + pad };
  }

  private drawThunder(ctx: CanvasRenderingContext2D) {
    const s = this.scene;
    const g = this.g;
    const v = ws.get().view;
    const env = this.envFor();
    if (env && env.t.length) {
      const n = env.t.length;
      // onset strength as an area along the bottom
      const os = env.onset_strength ?? [];
      let maxOs = 0;
      for (let i = 0; i < os.length; i++) if (Number.isFinite(os[i]) && os[i] > maxOs) maxOs = os[i];
      if (maxOs > 0) {
        const hgt = (g.y1 - g.y0) * 0.3;
        ctx.beginPath();
        let started = false;
        let lastX = 0;
        for (let i = 0; i < n; i++) {
          const t = env.t[i];
          if (t < v.t0 - (v.t1 - v.t0) * 0.02 || t > v.t1 + (v.t1 - v.t0) * 0.02) continue;
          const x = this.tx(t);
          const y = g.y1 - (Math.max(0, os[i] || 0) / maxOs) * hgt;
          if (!started) {
            ctx.moveTo(x, g.y1);
            started = true;
          }
          ctx.lineTo(x, y);
          lastX = x;
        }
        if (started) {
          ctx.lineTo(lastX, g.y1);
          ctx.closePath();
          const grad = ctx.createLinearGradient(0, g.y1 - hgt, 0, g.y1);
          grad.addColorStop(0, "rgba(45,212,191,0.42)");
          grad.addColorStop(1, "rgba(45,212,191,0.04)");
          ctx.fillStyle = grad;
          ctx.fill();
        }
      }
      env.bands.forEach((b, k) => {
        ctx.beginPath();
        let pen = false;
        for (let i = 0; i < n; i++) {
          const t = env.t[i];
          const val = b.db[i];
          if (t < v.t0 - (v.t1 - v.t0) * 0.02 || t > v.t1 + (v.t1 - v.t0) * 0.02 || !Number.isFinite(val)) {
            pen = false;
            continue;
          }
          const x = this.tx(t);
          const y = this.vy(val);
          if (!pen) {
            ctx.moveTo(x, y);
            pen = true;
          } else ctx.lineTo(x, y);
        }
        ctx.strokeStyle = BAND_COLORS[k % BAND_COLORS.length];
        ctx.globalAlpha = 0.9;
        ctx.lineWidth = 1.2;
        ctx.stroke();
        ctx.globalAlpha = 1;
      });
    }

    // markers
    const dragging = this.drag?.kind === "marker" ? this.drag : null;
    const drawMarker = (mk: MarkerKind, id: string, t: number, status: EventStatus, sigma: number, label: string, selected: boolean) => {
      const tt = dragging && dragging.id === id ? dragging.t : t;
      const x = Math.round(this.tx(tt)) + 0.5;
      if (x < g.x0 - 30 || x > g.x1 + 30) return;
      const color = STATUS_COLOR[status];
      const alpha = status === "rejected" ? 0.5 : 1;
      ctx.globalAlpha = alpha;
      if (sigma > 0 && mk === "onset") {
        const w = Math.max(1, this.tx(tt + sigma) - this.tx(tt));
        ctx.fillStyle = hexA(color, 0.1);
        ctx.fillRect(x - w, g.y0, 2 * w, g.y1 - g.y0);
      }
      ctx.strokeStyle = color;
      ctx.lineWidth = selected ? 2 : 1.25;
      ctx.setLineDash(mk === "flash" ? [3, 3] : []);
      if (selected) {
        ctx.shadowColor = color;
        ctx.shadowBlur = 8;
      }
      ctx.beginPath();
      ctx.moveTo(x, g.y0 + (mk === "flash" ? 22 : 16));
      ctx.lineTo(x, g.y1);
      ctx.stroke();
      ctx.shadowBlur = 0;
      ctx.setLineDash([]);
      if (mk === "flash") this.bolt(ctx, x, g.y0 + 4, color, selected);
      else this.badge(ctx, x - 4, g.y0 + 9, label, color, selected, 1);
      ctx.globalAlpha = 1;
    };
    s.onsets.forEach((o, i) => drawMarker("onset", o.id, o.t, o.status, o.sigma_s, `T${i + 1}`, o.id === s.selectedOnsetId));
    s.flashes.forEach((f) => drawMarker("flash", f.id, f.t, f.status, 0, "", f.id === s.selectedFlashId));

    // add-mode cursor
    if ((s.tool === "add-onset" || s.tool === "add-flash") && this.hover && this.inPlot(this.hover.x, this.hover.y)) {
      const x = Math.round(this.hover.x) + 0.5;
      ctx.strokeStyle = s.tool === "add-flash" ? "rgba(250,204,21,0.8)" : "rgba(74,222,128,0.8)";
      ctx.setLineDash([2, 3]);
      ctx.beginPath();
      ctx.moveTo(x, g.y0);
      ctx.lineTo(x, g.y1);
      ctx.stroke();
      ctx.setLineDash([]);
    }
  }

  private bolt(ctx: CanvasRenderingContext2D, x: number, y: number, color: string, selected: boolean) {
    ctx.save();
    ctx.translate(x - 6, y);
    ctx.beginPath();
    ctx.moveTo(7.5, 0);
    ctx.lineTo(1.5, 9);
    ctx.lineTo(5.5, 9);
    ctx.lineTo(3.5, 16);
    ctx.lineTo(10.5, 6);
    ctx.lineTo(6.5, 6);
    ctx.lineTo(8.5, 0);
    ctx.closePath();
    ctx.fillStyle = color;
    if (selected) {
      ctx.shadowColor = color;
      ctx.shadowBlur = 8;
    }
    ctx.fill();
    ctx.restore();
  }

  // ---- ENF -----------------------------------------------------------------------------

  private computeEnfRange() {
    const s = this.scene;
    const v = ws.get().view;
    let lo = Infinity;
    let hi = -Infinity;
    const take = (x: number | null | undefined) => {
      if (x == null || !Number.isFinite(x)) return;
      if (x < lo) lo = x;
      if (x > hi) hi = x;
    };
    const tr = s.enfTrace;
    if (tr && tr.f.length) {
      const i0 = Math.max(0, Math.floor((v.t0 - tr.t0) / tr.dt));
      const i1 = Math.min(tr.f.length - 1, Math.ceil((v.t1 - tr.t0) / tr.dt));
      for (let i = i0; i <= i1; i++) take(tr.f[i]);
    }
    const ov = s.enfOverlay;
    if (ov) {
      for (let i = 0; i < ov.t.length; i++) {
        if (ov.t[i] < v.t0 || ov.t[i] > v.t1) continue;
        take(ov.rec[i]);
        take(ov.ref[i]);
      }
    }
    if (!Number.isFinite(lo)) {
      const nom = s.enfNominal ?? 50;
      lo = nom - 0.05;
      hi = nom + 0.05;
    }
    const mid = (lo + hi) / 2;
    const half = Math.max(0.01, ((hi - lo) / 2) * 1.15);
    this.yr = { lo: mid - half, hi: mid + half };
  }

  private series(ctx: CanvasRenderingContext2D, getT: (i: number) => number, getV: (i: number) => number | null | undefined, n: number, color: string, width: number, dash: number[] = []) {
    const v = ws.get().view;
    const g = this.g;
    const pxPerPoint = n > 1 ? Math.abs(this.tx(getT(1)) - this.tx(getT(0))) : 1;
    ctx.strokeStyle = color;
    ctx.lineWidth = width;
    ctx.setLineDash(dash);
    ctx.beginPath();
    if (pxPerPoint < 0.5) {
      // decimate to one min/max pair per pixel column
      let col = -1;
      let mn = Infinity;
      let mx = -Infinity;
      const flush = () => {
        if (col >= 0 && Number.isFinite(mn)) {
          ctx.moveTo(col + 0.5, this.vy(mx));
          ctx.lineTo(col + 0.5, this.vy(mn) + 0.5);
        }
      };
      for (let i = 0; i < n; i++) {
        const t = getT(i);
        if (t < v.t0 || t > v.t1) continue;
        const c = Math.floor(this.tx(t));
        const val = getV(i);
        if (c !== col) {
          flush();
          col = c;
          mn = Infinity;
          mx = -Infinity;
        }
        if (val != null && Number.isFinite(val)) {
          if (val < mn) mn = val;
          if (val > mx) mx = val;
        }
      }
      flush();
    } else {
      let pen = false;
      for (let i = 0; i < n; i++) {
        const t = getT(i);
        const val = getV(i);
        if (t < v.t0 - (v.t1 - v.t0) * 0.02 || t > v.t1 + (v.t1 - v.t0) * 0.02 || val == null || !Number.isFinite(val)) {
          pen = false;
          continue;
        }
        const x = this.tx(t);
        const y = this.vy(val);
        if (!pen) {
          ctx.moveTo(x, y);
          pen = true;
        } else ctx.lineTo(x, y);
      }
    }
    ctx.stroke();
    ctx.setLineDash([]);
    void g;
  }

  private drawEnf(ctx: CanvasRenderingContext2D) {
    const s = this.scene;
    const g = this.g;
    if (s.enfNominal) {
      const y = Math.round(this.vy(s.enfNominal)) + 0.5;
      if (y > g.y0 && y < g.y1) {
        ctx.strokeStyle = "rgba(255,255,255,0.22)";
        ctx.setLineDash([4, 4]);
        ctx.beginPath();
        ctx.moveTo(g.x0, y);
        ctx.lineTo(g.x1, y);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = "rgba(255,255,255,0.45)";
        ctx.font = FONT_SMALL;
        ctx.textAlign = "right";
        ctx.textBaseline = "bottom";
        ctx.fillText(`${s.enfNominal} Hz nominal`, g.x1 - 6, y - 3);
      }
    }
    const ov = s.enfOverlay;
    if (ov && ov.t.length) {
      this.series(ctx, (i) => ov.t[i], (i) => ov.ref[i], ov.t.length, REF_COLOR, 1.4);
      this.series(ctx, (i) => ov.t[i], (i) => ov.rec[i], ov.t.length, ENF_COLOR, 1.6);
    } else if (s.enfTrace) {
      const tr = s.enfTrace;
      this.series(ctx, (i) => tr.t0 + i * tr.dt, (i) => tr.f[i], tr.f.length, ENF_COLOR, 1.5);
    }
  }

  // ---- playback, hover, readout --------------------------------------------------------

  private drawPlayback(ctx: CanvasRenderingContext2D) {
    const pb = ws.get().playback;
    const g = this.g;
    if (pb.range) {
      const xa = this.tx(pb.range[0]);
      const xb = this.tx(pb.range[1]);
      ctx.fillStyle = "rgba(45,212,191,0.55)";
      ctx.fillRect(xa, g.y0, xb - xa, 2);
    }
    if (pb.t != null && pb.state !== "idle") {
      const x = Math.round(this.tx(pb.t)) + 0.5;
      ctx.strokeStyle = "rgba(255,255,255,0.95)";
      ctx.lineWidth = 1.25;
      ctx.shadowColor = "rgba(45,212,191,0.9)";
      ctx.shadowBlur = 6;
      ctx.beginPath();
      ctx.moveTo(x, g.y0);
      ctx.lineTo(x, g.y1);
      ctx.stroke();
      ctx.shadowBlur = 0;
      ctx.fillStyle = "#fff";
      ctx.beginPath();
      ctx.moveTo(x - 5, g.y0);
      ctx.lineTo(x + 5, g.y0);
      ctx.lineTo(x, g.y0 + 6);
      ctx.closePath();
      ctx.fill();
    }
  }

  private inPlot(x: number, y: number) {
    const g = this.g;
    return x >= g.x0 && x <= g.x1 && y >= g.y0 && y <= g.y1;
  }

  private drawHover(ctx: CanvasRenderingContext2D) {
    const h = this.hover;
    if (!h || this.drag?.kind === "pan") return;
    const g = this.g;
    if (!this.inPlot(h.x, h.y)) return;
    ctx.strokeStyle = "rgba(255,255,255,0.32)";
    ctx.lineWidth = 1;
    ctx.setLineDash([2, 3]);
    ctx.beginPath();
    const x = Math.round(h.x) + 0.5;
    ctx.moveTo(x, g.y0);
    ctx.lineTo(x, g.y1);
    if (this.scene.mode === "spectrogram") {
      const y = Math.round(h.y) + 0.5;
      ctx.moveTo(g.x0, y);
      ctx.lineTo(g.x1, y);
    }
    ctx.stroke();
    ctx.setLineDash([]);
  }

  private updateReadout() {
    const h = this.hover;
    const el = this.readout;
    if (!h || !this.inPlot(h.x, h.y)) {
      el.style.opacity = "0";
      return;
    }
    const s = this.scene;
    const t = this.xt(h.x);
    const parts: string[] = [`<b>${fmtRec(t, 2)}</b>`];
    if (s.clock) parts.push(`${fmtClock(s.clock.startUtc + t, s.clock.tz)} ${tzAbbr(s.clock.startUtc + t, s.clock.tz)}`);
    if (s.mode === "spectrogram") {
      const f = this.yf(h.y);
      parts.push(fmtHz(f));
      if (s.info && this.cache) {
        const db = sampleDb(s.info, this.cache, t, f, this.level);
        if (db != null) parts.push(`${db.toFixed(1)} dB`);
      }
      if (this.hoverEventId) {
        const n = s.eventNumber.get(this.hoverEventId);
        if (n != null) parts.push(`flyover ${n}`);
      }
    } else if (s.mode === "thunder") {
      parts.push(`${this.yv(h.y).toFixed(1)} dB`);
    } else {
      const tr = s.enfTrace;
      if (tr) {
        const i = Math.round((t - tr.t0) / tr.dt);
        const f = tr.f[i];
        if (f != null && Number.isFinite(f)) parts.push(`ENF ${f.toFixed(3)} Hz`);
      }
    }
    el.innerHTML = parts.join('<span class="sep">·</span>');
    el.style.opacity = "1";
  }

  // ---- hit testing --------------------------------------------------------------------

  private hitEvent(x: number, y: number): string | null {
    const s = this.scene;
    if (s.mode !== "spectrogram") return null;
    let best: string | null = null;
    let bestD = 9;
    const v = ws.get().view;
    for (const ev of this.visibleEvents()) {
      const curve = idealCurve(ev, s.c);
      const tMin = Math.max(ev.t_start - 60, v.t0);
      const tMax = Math.min(ev.t_end + 60, v.t1);
      for (let hh = 1; hh <= Math.max(1, ev.harmonics); hh++) {
        let px = NaN;
        let py = NaN;
        for (let i = 0; i < curve.t.length; i++) {
          const tt = curve.t[i];
          if (tt < tMin || tt > tMax) {
            px = NaN;
            continue;
          }
          const cx = this.tx(tt);
          const cy = this.fy(hh * curve.f[i]);
          if (!Number.isNaN(px)) {
            const d = segDist(x, y, px, py, cx, cy);
            if (d < bestD) {
              bestD = d;
              best = ev.id;
            }
          }
          px = cx;
          py = cy;
        }
      }
    }
    return best;
  }

  private hitMarker(x: number): { mk: MarkerKind; id: string; t: number } | null {
    const s = this.scene;
    if (s.mode !== "thunder") return null;
    let best: { mk: MarkerKind; id: string; t: number } | null = null;
    let bestD = 6;
    for (const o of s.onsets) {
      const d = Math.abs(this.tx(o.t) - x);
      if (d < bestD) {
        bestD = d;
        best = { mk: "onset", id: o.id, t: o.t };
      }
    }
    for (const f of s.flashes) {
      const d = Math.abs(this.tx(f.t) - x);
      if (d < bestD) {
        bestD = d;
        best = { mk: "flash", id: f.id, t: f.t };
      }
    }
    return best;
  }

  // ---- input --------------------------------------------------------------------------

  private local(e: PointerEvent | MouseEvent | WheelEvent) {
    const r = this.over.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  }

  private area(x: number, y: number): "plot" | "faxis" | "taxis" {
    if (x < this.g.x0) return "faxis";
    if (y > this.g.y1) return "taxis";
    return "plot";
  }

  private updateCursor(x?: number, y?: number) {
    const s = this.scene;
    let c = "default";
    if (this.drag?.kind === "pan") c = "grabbing";
    else if (this.drag?.kind === "marker") c = "ew-resize";
    else if (this.drag?.kind === "box") c = "crosshair";
    else if (x != null && y != null) {
      const a = this.area(x, y);
      if (a === "faxis") c = s.mode === "spectrogram" ? "ns-resize" : "default";
      else if (a === "taxis") c = "ew-resize";
      else if (s.mode === "spectrogram" && s.tool === "box") c = "crosshair";
      else if (s.mode === "thunder" && (s.tool === "add-onset" || s.tool === "add-flash")) c = "copy";
      else if (s.mode === "thunder" && this.hitMarker(x)) c = "ew-resize";
      else if (this.hoverEventId) c = "pointer";
      else c = "grab";
    }
    this.over.style.cursor = c;
  }

  private onDown(e: PointerEvent) {
    if (e.button !== 0) return;
    const { x, y } = this.local(e);
    const a = this.area(x, y);
    const s = this.scene;
    const st = ws.get();
    this.over.setPointerCapture(e.pointerId);
    if (a === "plot" && s.mode === "spectrogram" && (s.tool === "box" || e.altKey) && s.info) {
      this.drag = { kind: "box", xa: x, ya: y, xb: x, yb: y };
      if (st.boxDraft) ws.set({ boxDraft: null });
    } else if (a === "plot" && s.mode === "thunder" && s.tool === "pan" && this.hitMarker(x)) {
      const m = this.hitMarker(x)!;
      this.drag = { kind: "marker", mk: m.mk, id: m.id, t: m.t, x, moved: false };
      this.handlers.onSelectMarker(m.mk, m.id);
    } else {
      this.drag = { kind: "pan", x, y, t0: st.view.t0, t1: st.view.t1, flo: st.freq.lo, fhi: st.freq.hi, area: a, moved: false };
    }
    this.updateCursor(x, y);
    this.request(false);
  }

  private onMove(e: PointerEvent) {
    const { x, y } = this.local(e);
    this.hover = { x, y };
    const s = this.scene;
    const d = this.drag;
    if (d?.kind === "pan") {
      const dx = x - d.x;
      const dy = y - d.y;
      if (Math.abs(dx) + Math.abs(dy) > 3) d.moved = true;
      if (d.moved) {
        const g = this.g;
        if (d.area !== "faxis") {
          const dt = (-dx / (g.x1 - g.x0)) * (d.t1 - d.t0);
          setView(d.t0 + dt, d.t1 + dt);
        }
        if (s.mode === "spectrogram" && d.area !== "taxis") {
          const df = (dy / (g.y1 - g.y0)) * (d.fhi - d.flo);
          setFreq(d.flo + df, d.fhi + df);
        }
      }
    } else if (d?.kind === "box") {
      d.xb = x;
      d.yb = y;
    } else if (d?.kind === "marker") {
      if (Math.abs(x - d.x) > 2) d.moved = true;
      if (d.moved) d.t = clamp(this.xt(x), 0, s.duration);
    } else {
      const prev = this.hoverEventId;
      this.hoverEventId = this.inPlot(x, y) ? this.hitEvent(x, y) : null;
      if (prev !== this.hoverEventId) this.request(false);
    }
    const t = this.inPlot(x, y) ? this.xt(x) : null;
    if (t !== ws.get().hoverT) ws.set({ hoverT: t });
    this.updateCursor(x, y);
    this.request(false);
  }

  private onUp(e: PointerEvent) {
    const { x, y } = this.local(e);
    const d = this.drag;
    this.drag = null;
    try {
      this.over.releasePointerCapture(e.pointerId);
    } catch {
      /* ignore */
    }
    const s = this.scene;
    if (d?.kind === "box") {
      const b = this.boxFromDrag(d);
      if (Math.abs(this.tx(b.t1) - this.tx(b.t0)) > 6 && Math.abs(this.fy(b.f0) - this.fy(b.f1)) > 6) this.handlers.onBox(b);
    } else if (d?.kind === "marker") {
      if (d.moved) this.handlers.onMoveMarker(d.mk, d.id, d.t);
    } else if (d?.kind === "pan" && !d.moved && d.area === "plot") {
      // a click
      if (s.mode === "spectrogram") {
        this.handlers.onSelectEvent(this.hitEvent(x, y));
      } else if (s.mode === "thunder") {
        if (s.tool === "add-onset") this.handlers.onAddMarker("onset", clamp(this.xt(x), 0, s.duration));
        else if (s.tool === "add-flash") this.handlers.onAddMarker("flash", clamp(this.xt(x), 0, s.duration));
        else {
          const m = this.hitMarker(x);
          this.handlers.onSelectMarker(m?.mk ?? "onset", m?.id ?? null);
        }
      }
    }
    this.updateCursor(x, y);
    this.request(false);
  }

  private onWheel(e: WheelEvent) {
    e.preventDefault();
    const { x, y } = this.local(e);
    const s = this.scene;
    const scale = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? this.g.H : 1;
    const dx = e.deltaX * scale;
    const dy = e.deltaY * scale;
    const a = this.area(x, y);
    if (s.mode === "spectrogram" && (e.shiftKey || a === "faxis")) {
      const delta = Math.abs(dy) >= Math.abs(dx) ? dy : dx;
      zoomFreqAt(clamp(this.yf(y), ws.get().freq.lo, ws.get().freq.hi), Math.exp(delta * 0.0018));
      return;
    }
    if (!e.ctrlKey && Math.abs(dx) > Math.abs(dy)) {
      const v = ws.get().view;
      panTime((dx / (this.g.x1 - this.g.x0)) * (v.t1 - v.t0));
      return;
    }
    const k = e.ctrlKey ? 0.01 : 0.0018;
    const t = clamp(this.xt(x), ws.get().view.t0, ws.get().view.t1);
    zoomTimeAt(t, Math.exp(dy * k));
  }

  private onDblClick(e: MouseEvent) {
    const { x, y } = this.local(e);
    if (this.area(x, y) !== "plot") return;
    if (this.scene.mode === "thunder" && this.scene.tool !== "pan") return;
    zoomTimeAt(this.xt(x), e.shiftKey ? 2 : 0.5);
  }
}

const smoothCache = new WeakMap<ArrayLike<number>, Float64Array>();
/** Light centred moving average (7 samples) to remove track-position jitter from modelled curves. */
function smoothed(f: ArrayLike<number>): Float64Array {
  const hit = smoothCache.get(f);
  if (hit) return hit;
  const n = f.length;
  const out = new Float64Array(n);
  const k = 3;
  for (let i = 0; i < n; i++) {
    let sum = 0;
    let cnt = 0;
    for (let j = Math.max(0, i - k); j <= Math.min(n - 1, i + k); j++) {
      const v = f[j];
      if (Number.isFinite(v)) {
        sum += v;
        cnt++;
      }
    }
    out[i] = cnt ? sum / cnt : NaN;
  }
  smoothCache.set(f, out);
  return out;
}

function isWeak(e: AircraftEvent) {
  return (e.quality ?? (e.score >= 3 ? "possible" : "weak")) === "weak";
}

function statusRank(s: EventStatus) {
  return s === "rejected" ? 0 : s === "candidate" ? 1 : 2;
}

function segDist(px: number, py: number, ax: number, ay: number, bx: number, by: number) {
  const dx = bx - ax;
  const dy = by - ay;
  const l2 = dx * dx + dy * dy;
  let u = l2 > 0 ? ((px - ax) * dx + (py - ay) * dy) / l2 : 0;
  u = u < 0 ? 0 : u > 1 ? 1 : u;
  const qx = ax + u * dx - px;
  const qy = ay + u * dy - py;
  return Math.sqrt(qx * qx + qy * qy);
}

function roundRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

function hexA(hex: string, a: number) {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
}

export { FONT };
