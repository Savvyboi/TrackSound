import { useQueryClient } from "@tanstack/react-query";
import {
  AudioWaveform,
  BoxSelect,
  Hand,
  Keyboard,
  Maximize2,
  Minus,
  Pause,
  Play,
  Plus,
  SlidersHorizontal,
  Square,
  StretchVertical,
  Zap,
  Activity,
  FileAudio,
  RotateCcw,
} from "lucide-react";
import { useMemo, useState } from "react";
import { errorMessage } from "../../api/client";
import { useStartJob } from "../../api/jobs";
import {
  casePath,
  useAircraftEvents,
  useCandidateCurves,
  useEnf,
  useEnfMatch,
  useEnvelope,
  useLightning,
  useSearch,
  useAlerts,
} from "../../api/queries";
import { Button, IconButton } from "../../components/ui/Button";
import { Segmented, Slider, Switch } from "../../components/ui/form";
import { EmptyState, Kbd, Spinner, Unavailable } from "../../components/ui/misc";
import { Popover, usePopover } from "../../components/ui/overlay";
import { toast } from "../../components/ui/toast";
import { cn } from "../../lib/cn";
import { COLORMAPS, colormapGradient } from "../../lib/colormap";
import { clamp, fmtDuration, fmtRec } from "../../lib/format";
import { useDebounced } from "../../lib/hooks";
import {
  fitAll,
  modeForTab,
  resetFreq,
  setTab,
  useWs,
  ws,
  zoomTimeCenter,
  type BoxDraft,
  type Speed,
  type Tool,
} from "../../state/workspace";
import { eventNumbers } from "../aircraft/actions";
import { useLightningActions } from "../lightning/actions";
import { DropZone } from "../home/DropZone";
import { UploadProgressCard, useUploadView } from "../home/UploadProgress";
import { startUpload } from "../home/uploads";
import { caseC, caseDuration, caseTz, recordingReady, useCaseData } from "../workspace/caseContext";
import { BAND_COLORS, STATUS_COLOR, type Handlers, type Scene } from "./TimelineCanvas";
import { TimelineView } from "./TimelineView";
import { stopPlayback } from "./playback";
import { playTarget, togglePlay } from "./playControl";

export function TimelinePanel() {
  const c = useCaseData();
  const ready = recordingReady(c);
  const upload = useUploadView(c);
  const qc = useQueryClient();

  if (!ready) {
    return (
      <div className="flex h-full flex-col bg-surface">
        <div className="flex h-10 shrink-0 items-center gap-2 border-b border-line px-3 text-xs font-medium text-fg-muted">
          <FileAudio className="size-3.5" /> Recording
        </div>
        <div className="flex min-h-0 flex-1 items-center justify-center p-6">
          {upload ? (
            <div className="w-full max-w-[560px]">
              <UploadProgressCard c={c} view={upload} />
              {upload.phase === "error" && (
                <DropZone compact className="mt-4" title="Upload the recording again" onFile={(f) => startUpload(qc, c.id, f)} />
              )}
            </div>
          ) : (
            <DropZone compact className="w-full max-w-[640px]" title="Upload a recording to begin" onFile={(f) => startUpload(qc, c.id, f)} />
          )}
        </div>
      </div>
    );
  }
  return <ReadyTimeline />;
}

function ReadyTimeline() {
  const c = useCaseData();
  const tab = useWs((s) => s.tab);
  const mode = modeForTab(tab);
  const tool = useWs((s) => s.tool);
  const selectedEventId = useWs((s) => s.selectedEventId);
  const checkedEventIds = useWs((s) => s.checkedEventIds);
  const searchId = useWs((s) => s.searchId);
  const rank = useWs((s) => s.candidateRank);
  const pendingFit = useWs((s) => s.pendingFit);
  const onsetId = useWs((s) => s.onsetId);
  const flashId = useWs((s) => s.flashId);
  const matchId = useWs((s) => s.matchId);
  const showOverlays = useWs((s) => s.showOverlays);
  const showWeak = useWs((s) => s.showWeak);
  const alertId = useWs((s) => s.alertId);
  const alerts = useAlerts(c.id, tab === "alerts");
  const alertMarks = useMemo(
    () => (tab === "alerts" ? (alerts.data?.headers ?? []).map((h) => ({ t: h.t, label: h.event, active: h.id === alertId })) : []),
    [tab, alerts.data, alertId],
  );
  const rec = c.recording!;
  const info = rec.spectrogram!;
  const D = caseDuration(c);

  const events = useAircraftEvents(c.id);
  const curves = useCandidateCurves(c.id, searchId, rank);
  const search = useSearch(c.id, searchId);
  const candLabels = useMemo(() => {
    const m = new Map<string, string>();
    const k = search.data?.candidates.find((x) => x.rank === rank);
    for (const f of k?.flights ?? []) m.set(f.icao, [f.reg ?? f.icao.toUpperCase(), f.type].filter(Boolean).join(" · "));
    return m;
  }, [search.data, rank]);
  const lightning = useLightning(c.id, mode === "thunder" || tab === "lightning");
  const enf = useEnf(c.id, mode === "enf");
  const match = useEnfMatch(c.id, mode === "enf" ? matchId : null);
  const lActions = useLightningActions(c.id);

  // thunder envelope: whole recording, plus detail for the visible window when zoomed in
  const thunder = mode === "thunder";
  const view = useWs((s) => (thunder ? s.view : null));
  const dv = useDebounced(view, 220);
  const envOverview = useEnvelope(c.id, 0, Math.ceil(D), 3000, thunder);
  const span = dv ? dv.t1 - dv.t0 : D;
  const detailOn = thunder && !!dv && span < D * 0.5;
  const q = (x: number) => Math.round(x * 10) / 10;
  const envDetail = useEnvelope(c.id, dv ? q(Math.max(0, dv.t0 - span * 0.1)) : 0, dv ? q(Math.min(D, dv.t1 + span * 0.1)) : 0, 1800, detailOn);

  const startUtc = c.start_utc;
  const tz = caseTz(c);
  const clock = useMemo(() => (startUtc != null ? { startUtc, tz } : null), [startUtc, tz]);
  const numbers = useMemo(() => eventNumbers(events.data), [events.data]);

  const scene: Scene = useMemo(
    () => ({
      mode,
      caseId: c.id,
      version: String(rec.uploaded_at),
      info,
      duration: D,
      clock,
      c: caseC(c),
      tool,
      events: events.data ?? [],
      eventNumber: numbers,
      selectedEventId,
      checkedEventIds,
      candCurves: rank != null && searchId ? (curves.data?.events ?? null) : null,
      candLabels,
      pendingFit,
      showOverlays,
      showWeak,
      alertMarks,
      envOverview: envOverview.data ?? null,
      envDetail: detailOn ? (envDetail.data ?? null) : null,
      onsets: lightning.data?.onsets ?? [],
      flashes: lightning.data?.flashes ?? [],
      selectedOnsetId: onsetId,
      selectedFlashId: flashId,
      enfTrace: enf.data?.analysis?.trace ?? null,
      enfNominal: enf.data?.analysis?.nominal_hz ?? null,
      enfOverlay: matchId ? (match.data?.overlay ?? null) : null,
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [mode, c.id, c.temperature_c, rec.uploaded_at, info, D, clock, tool, events.data, numbers, selectedEventId, checkedEventIds, rank, searchId, curves.data, candLabels, pendingFit, showOverlays, showWeak, alertMarks, envOverview.data, envDetail.data, detailOn, lightning.data, onsetId, flashId, enf.data, matchId, match.data],
  );

  const handlers: Handlers = {
    onSelectEvent: (id) => {
      ws.set({ selectedEventId: id });
      if (id && (tab === "setup" || tab === "summary")) setTab("aircraft");
    },
    onBox: (b) => ws.set({ boxDraft: b }),
    onAddMarker: (kind, t) => (kind === "onset" ? lActions.addOnset(t) : lActions.addFlash(t)),
    onMoveMarker: (kind, id, t) => (kind === "onset" ? lActions.patchOnset(id, { t }) : lActions.patchFlash(id, { t })),
    onSelectMarker: (kind, id) => ws.set(kind === "onset" ? { onsetId: id, flashId: null } : { flashId: id, onsetId: null }),
  };

  return (
    <div className="flex h-full flex-col bg-surface">
      <Toolbar />
      <div className="relative min-h-0 flex-1">
        <TimelineView scene={scene} handlers={handlers} />
        {mode === "spectrogram" && <BoxFitCard />}
        {mode === "thunder" && (
          <ThunderOverlay
            unavailable={!!(envOverview.error && (envOverview.error as { unavailable?: boolean }).unavailable)}
            loading={envOverview.isLoading}
            bands={envOverview.data?.bands ?? []}
            addMode={tool === "add-onset" || tool === "add-flash" ? tool : null}
          />
        )}
        {mode === "enf" && (
          <EnfOverlay
            unavailable={!!(enf.error && (enf.error as { unavailable?: boolean }).unavailable)}
            loading={enf.isLoading}
            hasAnalysis={!!enf.data?.analysis}
            noHum={enf.data?.analysis?.nominal_hz === null}
            overlay={!!(matchId && match.data?.overlay)}
          />
        )}
        {mode === "spectrogram" && events.data && events.data.length === 0 && tab === "aircraft" && <SpectrogramHint />}
      </div>
      <Overview duration={D} qcCaseId={c.id} />
    </div>
  );
}

// ---- toolbar ------------------------------------------------------------------------------

function Toolbar() {
  const c = useCaseData();
  const qc = useQueryClient();
  const tab = useWs((s) => s.tab);
  const mode = modeForTab(tab);
  const tool = useWs((s) => s.tool);
  const speed = useWs((s) => s.speed);
  const pbState = useWs((s) => s.playback.state);
  const display = usePopover();
  // re-render the play label when the selection changes
  useWs((s) => s.selectedEventId);
  useWs((s) => s.boxDraft);
  useWs((s) => s.onsetId);
  useWs((s) => s.flashId);
  const target = playTarget(qc, c.id);
  const setTool = (t: Tool) => ws.set({ tool: tool === t ? "pan" : t, boxDraft: null });
  const spec = c.recording?.spectrogram;
  const fLo = spec ? spec.f0 : 40;
  const fHi = spec ? spec.f0 + spec.df * (spec.n_bins - 1) : 450;

  const title =
    mode === "spectrogram" ? (
      <>
        <AudioWaveform className="size-3.5 text-accent" /> Spectrogram{" "}
        <span className="hidden font-normal text-fg-faint @5xl:inline">
          {Math.round(fLo)}–{Math.round(fHi)} Hz, whitened
        </span>
      </>
    ) : mode === "thunder" ? (
      <>
        <Zap className="size-3.5 text-src-lightning" /> Thunder envelope
      </>
    ) : (
      <>
        <Activity className="size-3.5 text-src-enf" /> Mains hum <span className="hidden font-normal text-fg-faint @4xl:inline">frequency (ENF)</span>
      </>
    );

  return (
    <div className="@container flex h-10 shrink-0 items-center gap-1 overflow-hidden border-b border-line px-2 whitespace-nowrap">
      <div className="flex min-w-0 items-center gap-1.5 px-1.5 text-xs font-semibold text-fg">{title}</div>
      <Divider />
      <IconButton label="Pan and zoom (H)" active={tool === "pan"} onClick={() => ws.set({ tool: "pan", boxDraft: null })}>
        <Hand />
      </IconButton>
      {mode === "spectrogram" && (
        <IconButton label="Box tool: drag around a sweep to fit a flyover (B, or hold Alt)" active={tool === "box"} onClick={() => setTool("box")}>
          <BoxSelect />
        </IconButton>
      )}
      {mode === "thunder" && (
        <>
          <Button size="sm" variant="ghost" active={tool === "add-onset"} icon={<Plus />} onClick={() => setTool("add-onset")} tip="Click on the timeline to add a thunder onset">
            Onset
          </Button>
          {c.recording?.has_video && (
            <Button size="sm" variant="ghost" active={tool === "add-flash"} icon={<Zap />} onClick={() => setTool("add-flash")} tip="Click on the timeline to add a lightning flash seen in the video">
              Flash
            </Button>
          )}
        </>
      )}
      <Divider />
      <IconButton label="Zoom out (−)" onClick={() => zoomTimeCenter(2)}>
        <Minus />
      </IconButton>
      <IconButton label="Zoom in (+)" onClick={() => zoomTimeCenter(0.5)}>
        <Plus />
      </IconButton>
      <IconButton label="Show the whole recording (0)" onClick={fitAll}>
        <Maximize2 />
      </IconButton>
      {mode === "spectrogram" && (
        <IconButton label="Reset frequency range (Shift+wheel zooms frequency)" onClick={resetFreq}>
          <StretchVertical />
        </IconButton>
      )}
      <ViewRange />
      <div className="flex-1" />
      <div className="flex items-center gap-1.5">
        <span className="hidden text-2xs text-fg-faint @6xl:inline">{target.label}</span>
        <IconButton
          label={pbState === "playing" ? "Pause (Space)" : `Play ${target.label.toLowerCase()} (Space)`}
          variant="secondary"
          onClick={() => togglePlay(qc, c.id)}
          className={cn(pbState === "playing" && "!border-accent-line !text-accent")}
        >
          {pbState === "loading" ? <Spinner className="size-3.5" /> : pbState === "playing" ? <Pause /> : <Play />}
        </IconButton>
        {pbState !== "idle" && (
          <IconButton label="Stop" onClick={stopPlayback}>
            <Square />
          </IconButton>
        )}
        <Segmented<Speed>
          size="xs"
          ariaLabel="Playback speed"
          value={speed}
          onChange={(v) => ws.set({ speed: v })}
          options={[
            { value: 1, label: "×1", tip: "Real speed" },
            { value: 4, label: "×4", tip: "4× faster, two octaves up: makes 100 Hz tones audible" },
            { value: 8, label: "×8", tip: "8× faster, three octaves up" },
          ]}
        />
      </div>
      {mode === "spectrogram" && (
        <>
          <Divider />
          <IconButton ref={display.anchor} label="Display: colours and contrast" active={display.open} onClick={display.toggle}>
            <SlidersHorizontal />
          </IconButton>
          <Popover open={display.open} onClose={() => display.setOpen(false)} anchor={display.anchor} width={280}>
            <DisplaySettings />
          </Popover>
        </>
      )}
      <IconButton label="Keyboard shortcuts (?)" onClick={() => ws.set({ showShortcuts: true })}>
        <Keyboard />
      </IconButton>
    </div>
  );
}

function Divider() {
  return <div className="mx-1 h-5 w-px bg-line" />;
}

function ViewRange() {
  const view = useWs((s) => s.view);
  const span = view.t1 - view.t0;
  const dec = span < 20 ? 1 : 0;
  return (
    <div className="ml-2 hidden items-center gap-1.5 text-2xs text-fg-faint @4xl:flex tnum">
      <span className="text-fg-muted">
        {fmtRec(view.t0, dec)} – {fmtRec(view.t1, dec)}
      </span>
      <span>({fmtDuration(span)})</span>
    </div>
  );
}

function DisplaySettings() {
  const c = useCaseData();
  const info = c.recording!.spectrogram!;
  const colormap = useWs((s) => s.colormap);
  const manual = useWs((s) => s.contrast);
  const auto = useWs((s) => s.contrastAuto);
  const autoVals = useWs((s) => s.autoContrast);
  const contrast = auto && autoVals ? autoVals : manual;
  const showOverlays = useWs((s) => s.showOverlays);
  const showWeak = useWs((s) => s.showWeak);
  const lo = info.db_min;
  const hi = info.db_max;
  return (
    <div className="p-3.5">
      <div className="mb-2 text-2xs font-semibold tracking-wider text-fg-muted uppercase">Colour map</div>
      <div className="grid grid-cols-2 gap-1.5">
        {COLORMAPS.map((m) => (
          <button
            key={m.id}
            onClick={() => ws.set({ colormap: m.id })}
            className={cn(
              "rounded-md border p-1 text-left transition-colors",
              colormap === m.id ? "border-accent-line bg-accent-soft" : "border-line hover:border-line-strong",
            )}
          >
            <div className="h-3 rounded-[3px]" style={{ background: colormapGradient(m.id) }} />
            <div className={cn("mt-1 px-0.5 text-2xs font-medium", colormap === m.id ? "text-accent" : "text-fg-muted")}>{m.label}</div>
          </button>
        ))}
      </div>
      <div className="mt-4 flex flex-col gap-2 border-t border-line pt-3">
        <Switch checked={showOverlays} onChange={(v) => ws.set({ showOverlays: v })} label={<span className="text-xs">Fitted flyover curves</span>} hint="Drawn as rails either side of the sweep (C)" />
        <Switch checked={showWeak} onChange={(v) => ws.set({ showWeak: v })} label={<span className="text-xs">Include weak fits</span>} hint="Fits that score about as well as pure noise" />
      </div>
      <div className="mt-4 mb-1.5 flex items-center justify-between border-t border-line pt-3">
        <span className="text-2xs font-semibold tracking-wider text-fg-muted uppercase">Contrast</span>
        <div className="flex items-center gap-3">
          {!auto && (
            <button className="flex items-center gap-1 text-2xs text-fg-faint hover:text-fg" onClick={() => ws.set({ contrastAuto: true })}>
              <RotateCcw className="size-3" /> Auto
            </button>
          )}
          <Switch checked={auto} onChange={(v) => ws.set({ contrastAuto: v, ...(v ? {} : { contrast: contrast }) })} label={<span className="text-2xs text-fg-muted">Auto</span>} />
        </div>
      </div>
      <div className="mb-2 h-2 rounded-full" style={{ background: colormapGradient(colormap) }} />
      <label className="flex items-center gap-2 text-xs text-fg-muted">
        <span className="w-14">Floor</span>
        <Slider
          ariaLabel="Contrast floor"
          min={lo}
          max={hi}
          step={0.5}
          value={contrast.lo}
          onChange={(v) => ws.set({ contrastAuto: false, contrast: { lo: Math.min(v, contrast.hi - 1), hi: contrast.hi } })}
        />
        <span className="w-[52px] shrink-0 text-right whitespace-nowrap text-fg tnum">{contrast.lo.toFixed(1)} dB</span>
      </label>
      <label className="mt-1 flex items-center gap-2 text-xs text-fg-muted">
        <span className="w-14">Ceiling</span>
        <Slider
          ariaLabel="Contrast ceiling"
          min={lo}
          max={hi}
          step={0.5}
          value={contrast.hi}
          onChange={(v) => ws.set({ contrastAuto: false, contrast: { lo: contrast.lo, hi: Math.max(v, contrast.lo + 1) } })}
        />
        <span className="w-[52px] shrink-0 text-right whitespace-nowrap text-fg tnum">{contrast.hi.toFixed(1)} dB</span>
      </label>
      <p className="mt-3 text-2xs leading-snug text-fg-faint">
        Levels are whitened dB above each frequency's median, so steady hum is suppressed and passing tones stand out.
        {auto && " Auto picks the floor just above the visible noise."}
      </p>
    </div>
  );
}

// ---- box fit -----------------------------------------------------------------------------

function BoxFitCard() {
  const c = useCaseData();
  const box = useWs((s) => s.boxDraft);
  const [harmonics, setHarmonics] = useState(1);
  const [busy, setBusy] = useState(false);
  const startJob = useStartJob();
  if (!box) return null;
  const fit = async (b: BoxDraft) => {
    setBusy(true);
    try {
      await startJob(
        `${casePath(c.id)}/aircraft/fit`,
        { t_start: b.t0, t_end: b.t1, f_lo: b.f0, f_hi: b.f1, harmonics },
        {
          success: () => "Added a manual flyover",
          onFinish: () => ws.set({ pendingFit: null }),
          onDone: (job) => {
            const id = job.result?.event_id;
            if (id) ws.set({ selectedEventId: id });
          },
        },
      );
      ws.set({ pendingFit: b, boxDraft: null, tool: "pan" });
    } catch (e) {
      toast({ tone: "error", title: "Could not fit the flyover", body: errorMessage(e) });
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="absolute top-2 left-1/2 z-20 flex -translate-x-1/2 items-center gap-3 rounded-xl border border-line-strong bg-surface/95 py-1.5 pr-1.5 pl-3 shadow-pop backdrop-blur animate-pop-in">
      <div className="text-xs">
        <div className="font-medium text-fg">Fit a flyover in this box</div>
        <div className="text-2xs text-fg-muted tnum">
          {fmtRec(box.t0, 1)} – {fmtRec(box.t1, 1)} · {box.f0.toFixed(0)}–{box.f1.toFixed(0)} Hz
        </div>
      </div>
      <div className="flex items-center gap-1.5 text-2xs text-fg-muted">
        Harmonics
        <Segmented
          size="xs"
          value={harmonics}
          onChange={setHarmonics}
          options={[1, 2, 3].map((h) => ({ value: h, label: String(h) }))}
        />
      </div>
      <Button size="sm" variant="primary" loading={busy} onClick={() => fit(box)}>
        Fit
      </Button>
      <Button size="sm" variant="ghost" onClick={() => ws.set({ boxDraft: null })}>
        Cancel
      </Button>
    </div>
  );
}

function SpectrogramHint() {
  return (
    <div className="pointer-events-none absolute right-5 bottom-9 z-10 max-w-[300px] rounded-lg border border-white/10 bg-black/70 px-3 py-2 text-2xs leading-relaxed text-white/75 backdrop-blur-sm">
      Prop flyovers show as tones sweeping <b className="text-white">down</b> in pitch. Run <b className="text-white">Detect flyovers</b>, or press{" "}
      <Kbd>B</Kbd> and drag a box around a sweep.
    </div>
  );
}

// ---- thunder / ENF overlays -------------------------------------------------------------------

function ThunderOverlay({
  unavailable,
  loading,
  bands,
  addMode,
}: {
  unavailable: boolean;
  loading: boolean;
  bands: { name: string; lo_hz: number; hi_hz: number }[];
  addMode: "add-onset" | "add-flash" | null;
}) {
  return (
    <>
      {bands.length > 0 && (
        <div className="pointer-events-none absolute top-2 right-3 z-10 flex flex-wrap items-center justify-end gap-x-3 gap-y-1 rounded-md border border-white/10 bg-black/65 px-2.5 py-1 text-2xs text-white/75 backdrop-blur-sm">
          {bands.map((b, i) => (
            <span key={b.name} className="flex items-center gap-1.5">
              <span className="h-0.5 w-3 rounded" style={{ background: BAND_COLORS[i % BAND_COLORS.length] }} />
              {b.name} <span className="text-white/45 tnum">{b.lo_hz}–{b.hi_hz} Hz</span>
            </span>
          ))}
          <span className="flex items-center gap-1.5">
            <span className="h-2 w-3 rounded-sm bg-[rgba(45,212,191,0.45)]" />
            onset strength
          </span>
          <span className="flex items-center gap-1.5">
            <span className="h-2.5 w-0.5" style={{ background: STATUS_COLOR.accepted }} />
            onsets
          </span>
        </div>
      )}
      {addMode && (
        <div className="pointer-events-none absolute bottom-10 left-1/2 z-10 -translate-x-1/2 rounded-full border border-white/10 bg-black/75 px-3 py-1 text-2xs text-white/85 backdrop-blur-sm">
          Click to add a {addMode === "add-onset" ? "thunder onset" : "lightning flash"} · <Kbd>Esc</Kbd> to finish
        </div>
      )}
      {unavailable && (
        <div className="absolute inset-0 z-10 flex items-center justify-center bg-black/40">
          <Unavailable className="max-w-[360px] bg-surface" what="The thunder envelope" />
        </div>
      )}
      {loading && !unavailable && (
        <div className="absolute inset-0 z-10 flex items-center justify-center">
          <Spinner className="size-5 text-fg-faint" />
        </div>
      )}
    </>
  );
}

function EnfOverlay({
  unavailable,
  loading,
  hasAnalysis,
  noHum,
  overlay,
}: {
  unavailable: boolean;
  loading: boolean;
  hasAnalysis: boolean;
  noHum: boolean;
  overlay: boolean;
}) {
  const c = useCaseData();
  const startJob = useStartJob();
  const [busy, setBusy] = useState(false);
  const extract = async () => {
    setBusy(true);
    try {
      await startJob(`${casePath(c.id)}/enf/extract`, { nominal: "auto" });
    } catch (e) {
      toast({ tone: "error", title: "Could not start the extraction", body: errorMessage(e) });
    } finally {
      setBusy(false);
    }
  };
  if (unavailable)
    return (
      <div className="absolute inset-0 z-10 flex items-center justify-center bg-black/40">
        <Unavailable className="max-w-[360px] bg-surface" what="Mains hum (ENF) analysis" />
      </div>
    );
  if (loading)
    return (
      <div className="absolute inset-0 z-10 flex items-center justify-center">
        <Spinner className="size-5 text-fg-faint" />
      </div>
    );
  if (!hasAnalysis || noHum)
    return (
      <div className="absolute inset-0 z-10 flex items-center justify-center">
        <div className="rounded-xl border border-line bg-surface/95 shadow-pop">
          <EmptyState
            compact
            icon={<Activity />}
            title={noHum ? "No mains hum found" : "No mains hum trace yet"}
            action={
              !noHum && (
                <Button variant="primary" size="sm" loading={busy} onClick={extract}>
                  Extract mains hum
                </Button>
              )
            }
          >
            {noHum
              ? "The recording doesn't contain a usable 50/60 Hz hum. Try forcing 50 or 60 Hz in the panel."
              : "Extract the 50/60 Hz hum to see how the grid frequency drifted during the recording."}
          </EmptyState>
        </div>
      </div>
    );
  return (
    <div className="pointer-events-none absolute top-2 right-3 z-10 flex items-center gap-3 rounded-md border border-white/10 bg-black/65 px-2.5 py-1 text-2xs text-white/75 backdrop-blur-sm">
      <span className="flex items-center gap-1.5">
        <span className="h-0.5 w-3 rounded bg-[#c084fc]" /> {overlay ? "Recording" : "ENF at the fundamental"}
      </span>
      {overlay && (
        <span className="flex items-center gap-1.5">
          <span className="h-1 w-3 rounded bg-[#fbbf24]/60" /> Reference (aligned)
        </span>
      )}
    </div>
  );
}

// ---- overview strip ------------------------------------------------------------------------

function Overview({ duration, qcCaseId }: { duration: number; qcCaseId: string }) {
  const view = useWs((s) => s.view);
  const pb = useWs((s) => s.playback);
  const tab = useWs((s) => s.tab);
  const mode = modeForTab(tab);
  const events = useAircraftEvents(qcCaseId);
  const lightning = useLightning(qcCaseId, mode === "thunder");
  const [drag, setDrag] = useState<{ x: number; t0: number; w: number } | null>(null);
  const D = Math.max(1e-6, duration);
  const left = (view.t0 / D) * 100;
  const width = Math.max(0.4, ((view.t1 - view.t0) / D) * 100);

  const marks: { t: number; color: string; key: string }[] = [];
  if (mode === "spectrogram") for (const e of events.data ?? []) marks.push({ t: e.t_cpa, color: STATUS_COLOR[e.status], key: e.id });
  if (mode === "thunder") {
    for (const o of lightning.data?.onsets ?? []) marks.push({ t: o.t, color: STATUS_COLOR[o.status], key: o.id });
    for (const f of lightning.data?.flashes ?? []) marks.push({ t: f.t, color: "#facc15", key: f.id });
  }

  const onDown = (e: React.PointerEvent<HTMLDivElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const t = ((e.clientX - r.left) / r.width) * D;
    const span = view.t1 - view.t0;
    let t0 = view.t0;
    if (t < view.t0 || t > view.t1) {
      t0 = clamp(t - span / 2, 0, D - span);
      ws.set({ view: { t0, t1: t0 + span } });
    }
    e.currentTarget.setPointerCapture(e.pointerId);
    setDrag({ x: e.clientX, t0, w: r.width });
  };
  const onMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!drag) return;
    const span = view.t1 - view.t0;
    const t0 = clamp(drag.t0 + ((e.clientX - drag.x) / drag.w) * D, 0, D - span);
    ws.set({ view: { t0, t1: t0 + span } });
  };

  return (
    <div className="flex h-[22px] shrink-0 items-center gap-2 border-t border-line px-2 pl-[50px]">
      <div
        className="relative h-3 flex-1 cursor-pointer rounded-[3px] bg-surface-2 ring-1 ring-line ring-inset"
        onPointerDown={onDown}
        onPointerMove={onMove}
        onPointerUp={() => setDrag(null)}
        data-tip="Overview of the whole recording: drag to move the view"
      >
        {marks.map((m) => (
          <span key={m.key} className="absolute top-0.5 bottom-0.5 w-[2px] rounded-full opacity-80" style={{ left: `${(m.t / D) * 100}%`, background: m.color }} />
        ))}
        <div
          className="absolute -top-px -bottom-px rounded-[3px] border border-accent-line bg-accent/8"
          style={{ left: `${left}%`, width: `${width}%` }}
        />
        {pb.t != null && pb.state !== "idle" && <span className="absolute -top-0.5 -bottom-0.5 w-px bg-white" style={{ left: `${(pb.t / D) * 100}%` }} />}
      </div>
      <span className="w-[54px] text-right text-2xs text-fg-faint tnum">{fmtRec(duration)}</span>
    </div>
  );
}
