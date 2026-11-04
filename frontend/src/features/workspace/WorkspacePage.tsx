import { useQueryClient } from "@tanstack/react-query";
import { FolderX } from "lucide-react";
import { useEffect } from "react";
import { Link, useParams } from "react-router-dom";
import { useCase } from "../../api/queries";
import { Button } from "../../components/ui/Button";
import { EmptyState, ErrorNote, Kbd, Skeleton, Spinner } from "../../components/ui/misc";
import { Modal } from "../../components/ui/overlay";
import { isTypingTarget } from "../../lib/hooks";
import {
  fitAll,
  openCase,
  panTime,
  setTab,
  TABS,
  useWs,
  ws,
  zoomTimeCenter,
  modeForTab,
} from "../../state/workspace";
import { useAircraftActions } from "../aircraft/actions";
import { useSwallowWindowDrops } from "../home/DropZone";
import { MapPanel } from "../map/MapPanel";
import { stopPlayback } from "../timeline/playback";
import { togglePlay } from "../timeline/playControl";
import { TimelinePanel } from "../timeline/TimelinePanel";
import { CaseContext, caseDuration, recordingReady } from "./caseContext";
import { Header } from "./Header";
import { Sidebar } from "./Sidebar";
import { SplitSidebar, SplitV } from "./SplitPane";

export function WorkspacePage() {
  const { id = "" } = useParams();
  const q = useCase(id);
  const c = q.data;
  useSwallowWindowDrops();

  useEffect(() => {
    openCase(id);
    return () => stopPlayback();
  }, [id]);

  // keep the timeline bounds in sync with the recording
  const duration = c ? caseDuration(c) : 0;
  const info = c?.recording?.spectrogram;
  useEffect(() => {
    if (!c || !recordingReady(c)) return;
    const s = ws.get();
    if (s.duration !== duration) {
      const first = s.duration === 0;
      ws.set({ duration });
      if (first || s.view.t1 > duration) ws.set({ view: { t0: 0, t1: duration } });
    }
    if (info) {
      const lo = Math.max(info.f0, 40);
      const hi = Math.min(info.f0 + info.df * (info.n_bins - 1), 450);
      const b = s.freqBounds;
      if (Math.abs(b.lo - lo) > 0.01 || Math.abs(b.hi - hi) > 0.01) ws.set({ freqBounds: { lo, hi }, freq: { lo, hi } });
    }
  }, [c, duration, info]);

  if (q.isLoading)
    return (
      <div className="flex h-full flex-col">
        <div className="flex h-12 items-center gap-3 border-b border-line bg-surface px-4">
          <Skeleton className="h-5 w-40" />
          <Skeleton className="h-4 w-56" />
        </div>
        <div className="flex flex-1 items-center justify-center">
          <Spinner className="size-6 text-fg-faint" />
        </div>
      </div>
    );
  if (!c)
    return (
      <div className="flex h-full items-center justify-center p-6">
        <div className="w-full max-w-[420px]">
          <EmptyState
            icon={<FolderX />}
            title="Case not found"
            action={
              <Link to="/">
                <Button variant="primary">Back to cases</Button>
              </Link>
            }
          >
            It may have been deleted, or the server's data folder changed.
          </EmptyState>
          {q.error ? <ErrorNote error={q.error} className="mt-2" /> : null}
        </div>
      </div>
    );

  return (
    <CaseContext.Provider value={c}>
      <Shortcuts caseId={c.id} />
      <div className="flex h-full flex-col">
        <Header />
        <div className="min-h-0 flex-1">
          <SplitSidebar
            storageKey="ts.sidebarW"
            main={<SplitV storageKey="ts.splitV" top={<TimelinePanel />} bottom={<MapPanel key={c.id} />} />}
            side={<Sidebar />}
          />
        </div>
      </div>
      <ShortcutsDialog />
    </CaseContext.Provider>
  );
}

/** Global keyboard shortcuts for the workspace. */
function Shortcuts({ caseId }: { caseId: string }) {
  const qc = useQueryClient();
  const actions = useAircraftActions(caseId);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isTypingTarget(e.target) || e.metaKey || e.ctrlKey) return;
      const s = ws.get();
      const mode = modeForTab(s.tab);
      const k = e.key;
      if (k === " ") {
        e.preventDefault();
        togglePlay(qc, caseId);
      } else if (k === "Escape") {
        if (s.showShortcuts) ws.set({ showShortcuts: false });
        else if (s.boxDraft || s.tool !== "pan" || s.mapTool !== "none") ws.set({ boxDraft: null, tool: "pan", mapTool: "none" });
        else if (s.selectedEventId) ws.set({ selectedEventId: null });
      } else if (k === "b" || k === "B") {
        if (mode === "spectrogram") ws.set({ tool: s.tool === "box" ? "pan" : "box", boxDraft: null });
      } else if (k === "h" || k === "H") {
        ws.set({ tool: "pan", boxDraft: null });
      } else if (k === "r" || k === "R") {
        if (s.tab === "aircraft" && s.selectedEventId) void actions.setStatus(s.selectedEventId, "rejected");
        else ws.set({ mapTool: s.mapTool === "region" ? "none" : "region" });
      } else if (k === "a" || k === "A") {
        if (s.tab === "aircraft" && s.selectedEventId) void actions.setStatus(s.selectedEventId, "accepted");
      } else if (k === "+" || k === "=") {
        zoomTimeCenter(0.5);
      } else if (k === "-" || k === "_") {
        zoomTimeCenter(2);
      } else if (k === "0") {
        fitAll();
      } else if (k === "ArrowLeft" || k === "ArrowRight") {
        e.preventDefault();
        const span = s.view.t1 - s.view.t0;
        panTime((k === "ArrowLeft" ? -1 : 1) * span * (e.shiftKey ? 0.5 : 0.1));
      } else if (k >= "1" && k <= "5") {
        setTab(TABS[Number(k) - 1]);
      } else if (k === "?") {
        ws.set({ showShortcuts: !s.showShortcuts });
      } else if (k === "c" || k === "C") {
        ws.set({ showOverlays: !s.showOverlays });
      } else if (k === "o" || k === "O") {
        if (mode === "thunder") ws.set({ tool: s.tool === "add-onset" ? "pan" : "add-onset" });
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [qc, caseId, actions]);
  return null;
}

const SHORTCUTS: [string[], string][] = [
  [["Space"], "Play / pause (selected flyover or visible range)"],
  [["Wheel"], "Zoom time around the cursor"],
  [["Shift", "Wheel"], "Zoom frequency (or wheel over the Hz axis)"],
  [["Drag"], "Pan time and frequency"],
  [["+", "−", "0"], "Zoom in, out, whole recording"],
  [["←", "→"], "Pan (hold Shift for bigger steps)"],
  [["B"], "Box tool: drag around a sweep to fit a flyover (or hold Alt)"],
  [["H"], "Pan tool"],
  [["C"], "Show or hide fitted flyover curves"],
  [["A", "R"], "Accept / reject the selected flyover"],
  [["O"], "Add thunder onsets by clicking (Lightning tab)"],
  [["R"], "Draw the search region on the map (nothing selected)"],
  [["1", "…", "5"], "Switch sidebar tab"],
  [["Esc"], "Cancel the current tool or selection"],
];

function ShortcutsDialog() {
  const open = useWs((s) => s.showShortcuts);
  return (
    <Modal open={open} onClose={() => ws.set({ showShortcuts: false })} className="max-w-[480px]" labelledBy="kbd-title">
      <div className="p-5">
        <h2 id="kbd-title" className="text-base font-semibold text-fg">
          Keyboard shortcuts
        </h2>
        <div className="mt-3 flex flex-col divide-y divide-line">
          {SHORTCUTS.map(([keys, what]) => (
            <div key={what} className="flex items-center justify-between gap-4 py-2 text-xs">
              <span className="text-fg-muted">{what}</span>
              <span className="flex shrink-0 items-center gap-1">
                {keys.map((k) => (
                  <Kbd key={k}>{k}</Kbd>
                ))}
              </span>
            </div>
          ))}
        </div>
      </div>
    </Modal>
  );
}
