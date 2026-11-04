import type { BBox } from "../api/types";
import type { ColormapName } from "../lib/colormap";
import { clamp } from "../lib/format";
import { createStore, loadPref, savePref } from "../lib/store";

export type Tab = "setup" | "aircraft" | "lightning" | "enf" | "alerts" | "summary";
export type TimelineMode = "spectrogram" | "thunder" | "enf";
export type Tool = "pan" | "box" | "add-onset" | "add-flash";
export type MapTool = "none" | "region" | "pick-point";
export type Speed = 1 | 4 | 8;

export const TABS: Tab[] = ["setup", "aircraft", "lightning", "enf", "alerts", "summary"];

export function modeForTab(tab: Tab): TimelineMode {
  if (tab === "lightning") return "thunder";
  if (tab === "enf") return "enf";
  return "spectrogram";
}

export type Layers = {
  region: boolean;
  flights: boolean;
  aircraftHeat: boolean;
  candidates: boolean;
  strikes: boolean;
  lightning: boolean;
  enfGrid: boolean;
};

export type BoxDraft = { t0: number; t1: number; f0: number; f1: number };

export type MapFocus = { bbox?: BBox; center?: { lat: number; lon: number }; zoom?: number; n: number };

export type Playback = {
  state: "idle" | "loading" | "playing" | "paused";
  t: number | null;
  range: [number, number] | null;
};

type Prefs = {
  colormap: ColormapName;
  contrast: { lo: number; hi: number };
  contrastAuto: boolean;
  layers: Layers;
  speed: Speed;
  followPlayhead: boolean;
  flightsDuringOnly: boolean;
  showOverlays: boolean;
};

const DEFAULT_PREFS: Prefs = {
  colormap: "magma",
  contrast: { lo: 3, hi: 20 },
  contrastAuto: true,
  layers: { region: true, flights: true, aircraftHeat: true, candidates: true, strikes: true, lightning: true, enfGrid: false },
  speed: 4,
  followPlayhead: true,
  flightsDuringOnly: false,
  showOverlays: true,
};

const prefs = { ...DEFAULT_PREFS, ...loadPref<Partial<Prefs>>("ts.prefs", {}) };
prefs.layers = { ...DEFAULT_PREFS.layers, ...(prefs.layers ?? {}) };

export type WorkspaceState = Prefs & {
  caseId: string | null;
  duration: number;
  view: { t0: number; t1: number };
  freq: { lo: number; hi: number };
  freqBounds: { lo: number; hi: number };
  tab: Tab;
  tool: Tool;
  mapTool: MapTool;
  selectedEventId: string | null;
  checkedEventIds: string[];
  searchId: string | null;
  candidateRank: number | null;
  flightIdx: number | null;
  onsetId: string | null;
  flashId: string | null;
  solveId: string | null;
  matchId: string | null;
  alertId: string | null;
  hoverT: number | null;
  pickPoint: { lat: number; lon: number } | null;
  searchMode: "blind" | "point" | "single";
  mapFocus: MapFocus | null;
  playback: Playback;
  boxDraft: BoxDraft | null;
  pendingFit: BoxDraft | null;
  showShortcuts: boolean;
  /** contrast window chosen automatically from the visible spectrogram (dB) */
  autoContrast: { lo: number; hi: number } | null;
  /** show flyover fits whose score is close to pure noise */
  showWeak: boolean;
};

const INITIAL: Omit<WorkspaceState, keyof Prefs> = {
  caseId: null,
  duration: 0,
  view: { t0: 0, t1: 60 },
  freq: { lo: 40, hi: 450 },
  freqBounds: { lo: 40, hi: 450 },
  tab: "setup",
  tool: "pan",
  mapTool: "none",
  selectedEventId: null,
  checkedEventIds: [],
  searchId: null,
  candidateRank: null,
  flightIdx: null,
  onsetId: null,
  flashId: null,
  solveId: null,
  matchId: null,
  alertId: null,
  hoverT: null,
  pickPoint: null,
  searchMode: "blind",
  mapFocus: null,
  playback: { state: "idle", t: null, range: null },
  boxDraft: null,
  pendingFit: null,
  showShortcuts: false,
  autoContrast: null,
  showWeak: false,
};

export const ws = createStore<WorkspaceState>({ ...INITIAL, ...prefs });
export const useWs = ws.use;

let lastPrefs = "";
ws.subscribe(() => {
  const s = ws.get();
  const p: Prefs = {
    colormap: s.colormap,
    contrast: s.contrast,
    contrastAuto: s.contrastAuto,
    layers: s.layers,
    speed: s.speed,
    followPlayhead: s.followPlayhead,
    flightsDuringOnly: s.flightsDuringOnly,
    showOverlays: s.showOverlays,
  };
  const key = JSON.stringify(p);
  if (key !== lastPrefs) {
    lastPrefs = key;
    savePref("ts.prefs", p);
  }
});

const tabKey = (id: string) => `ts.tab.${id}`;

/** Reset per-case state when a workspace opens. */
export function openCase(caseId: string) {
  if (ws.get().caseId === caseId) return;
  ws.set({ ...INITIAL, caseId, tab: loadPref<Tab>(tabKey(caseId), "setup") });
}

export function setTab(tab: Tab) {
  const s = ws.get();
  if (s.caseId) savePref(tabKey(s.caseId), tab);
  const tool = modeForTab(tab) === "spectrogram" ? (s.tool === "add-onset" || s.tool === "add-flash" ? "pan" : s.tool) : s.tool === "box" ? "pan" : s.tool;
  ws.set({ tab, tool, boxDraft: null, mapTool: s.mapTool === "pick-point" && tab !== "aircraft" ? "none" : s.mapTool });
}

// ---- time viewport ----------------------------------------------------------------------

export const MIN_SPAN = 2;

/** Set the visible time range, kept inside the recording. */
export function setView(t0: number, t1: number) {
  const D = ws.get().duration || 60;
  let span = clamp(t1 - t0, Math.min(MIN_SPAN, D), D);
  if (!Number.isFinite(span)) span = D;
  let a = t0;
  if (t1 - t0 !== span) a = (t0 + t1) / 2 - span / 2;
  a = clamp(a, 0, Math.max(0, D - span));
  ws.set({ view: { t0: a, t1: a + span } });
}

export function zoomTimeAt(t: number, factor: number) {
  const { view } = ws.get();
  const t0 = t - (t - view.t0) * factor;
  const t1 = t + (view.t1 - t) * factor;
  const D = ws.get().duration || 60;
  const span = t1 - t0;
  if (span < MIN_SPAN) {
    // keep the anchor under the cursor when clamping
    const u = (t - view.t0) / (view.t1 - view.t0);
    setView(t - u * MIN_SPAN, t + (1 - u) * MIN_SPAN);
    return;
  }
  if (span > D) {
    setView(0, D);
    return;
  }
  setView(t0, t1);
}

export function zoomTimeCenter(factor: number) {
  const { view } = ws.get();
  zoomTimeAt((view.t0 + view.t1) / 2, factor);
}

export function panTime(dt: number) {
  const { view } = ws.get();
  setView(view.t0 + dt, view.t1 + dt);
}

export function fitAll() {
  const D = ws.get().duration || 60;
  ws.set({ view: { t0: 0, t1: D } });
}

/** Show [t0, t1] with some context around it. */
export function zoomToRange(t0: number, t1: number, pad = 0.25) {
  const span = Math.max(MIN_SPAN * 4, t1 - t0);
  setView(t0 - span * pad, t1 + span * pad);
}

export function centerOn(t: number) {
  const { view } = ws.get();
  const span = view.t1 - view.t0;
  setView(t - span / 2, t + span / 2);
}

// ---- frequency viewport (spectrogram) ---------------------------------------------------

export function setFreq(lo: number, hi: number) {
  const b = ws.get().freqBounds;
  const full = b.hi - b.lo;
  let span = clamp(hi - lo, 8, full);
  let a = lo;
  if (hi - lo !== span) a = (lo + hi) / 2 - span / 2;
  a = clamp(a, b.lo, b.hi - span);
  if (!Number.isFinite(a)) {
    a = b.lo;
    span = full;
  }
  ws.set({ freq: { lo: a, hi: a + span } });
}

export function zoomFreqAt(f: number, factor: number) {
  const { freq } = ws.get();
  setFreq(f - (f - freq.lo) * factor, f + (freq.hi - f) * factor);
}

export function resetFreq() {
  const b = ws.get().freqBounds;
  ws.set({ freq: { ...b } });
}

// ---- selection helpers ------------------------------------------------------------------

export function focusMap(f: Omit<MapFocus, "n">) {
  ws.set({ mapFocus: { ...f, n: (ws.get().mapFocus?.n ?? 0) + 1 } });
}

export function toggleLayer(k: keyof Layers, v?: boolean) {
  const layers = ws.get().layers;
  ws.set({ layers: { ...layers, [k]: v ?? !layers[k] } });
}
