import { Check, Radar, SlidersHorizontal, Trash2, X } from "lucide-react";
import { useState } from "react";
import { errorMessage } from "../../api/client";
import { isActive, useStartJob } from "../../api/jobs";
import { casePath, useAircraftEvents, useCaseJobs } from "../../api/queries";
import type { AircraftEvent } from "../../api/types";
import { Button, IconButton } from "../../components/ui/Button";
import { Checkbox, NumberInput, Segmented } from "../../components/ui/form";
import { Badge, EmptyState, ErrorNote, ProgressBar, Section, Skeleton } from "../../components/ui/misc";
import { toast } from "../../components/ui/toast";
import { cn } from "../../lib/cn";
import { fmtDist, fmtRec } from "../../lib/format";
import { useWs, ws, zoomToRange } from "../../state/workspace";
import { STATUS_COLOR } from "../timeline/TimelineCanvas";
import { recordingReady, useCaseData } from "../workspace/caseContext";
import { eventNumbers, eventQuality, useAircraftActions } from "./actions";

type PresetId = "prop" | "heli" | "custom";
const PRESETS: { id: PresetId; label: string; tip: string; band?: [number, number]; harmonics: number }[] = [
  { id: "prop", label: "Propeller", tip: "Blade-pass tone 60–300 Hz, fundamental only", band: [60, 300], harmonics: 1 },
  { id: "heli", label: "Helicopter rotor", tip: "Main rotor 10–60 Hz with 3 harmonics", band: [10, 60], harmonics: 3 },
  { id: "custom", label: "Custom", tip: "Choose the band and harmonics", harmonics: 1 },
];

export function toggleChecked(id: string) {
  const cur = ws.get().checkedEventIds;
  if (cur.includes(id)) ws.set({ checkedEventIds: cur.filter((x) => x !== id) });
  else ws.set({ checkedEventIds: [...cur, id].slice(-2) });
}

export function selectEvent(ev: AircraftEvent) {
  ws.set({ selectedEventId: ev.id });
  zoomToRange(ev.t_start, ev.t_end, 0.15);
}

export function EventsSection() {
  const c = useCaseData();
  const events = useAircraftEvents(c.id, recordingReady(c));
  const jobs = useCaseJobs(c.id);
  const detectJob = jobs.find((j) => isActive(j) && j.kind.includes("detect") && j.kind.startsWith("aircraft"));
  const selected = useWs((s) => s.selectedEventId);
  const checked = useWs((s) => s.checkedEventIds);
  const actions = useAircraftActions(c.id);
  const startJob = useStartJob();
  const [showOpts, setShowOpts] = useState(false);
  const [preset, setPreset] = useState<PresetId>("prop");
  const [band, setBand] = useState<[number, number]>([60, 300]);
  const [harmonics, setHarmonics] = useState(1);
  const [top, setTop] = useState(12);
  const specLo = c.recording?.spectrogram?.f0 ?? 40;
  const minBand = Math.max(10, Math.ceil(specLo));
  const applyPreset = (p: PresetId) => {
    setPreset(p);
    const def = PRESETS.find((x) => x.id === p);
    if (def?.band) {
      setBand(def.band);
      setHarmonics(def.harmonics);
    }
    if (p === "custom") setShowOpts(true);
  };
  const [starting, setStarting] = useState(false);
  const showWeak = useWs((s) => s.showWeak);
  const setShowWeak = (f: (v: boolean) => boolean) => ws.set({ showWeak: f(ws.get().showWeak) });
  const all = (events.data ?? []).slice().sort((a, b) => a.t_cpa - b.t_cpa);
  const weak = all.filter((e) => eventQuality(e) === "weak" && e.status !== "accepted" && e.origin !== "manual");
  const collapseWeak = weak.length > 2 && !showWeak;
  const list = collapseWeak ? all.filter((e) => !weak.includes(e) || e.id === selected || checked.includes(e.id)) : all;
  const numbers = eventNumbers(events.data);
  const nAccepted = all.filter((e) => e.status === "accepted").length;

  const detect = async () => {
    setStarting(true);
    try {
      await startJob(`${casePath(c.id)}/aircraft/detect`, { band, harmonics, top }, { success: (j) => `${j.result?.count ?? 0} candidate flyovers` });
    } catch (e) {
      toast({ tone: "error", title: "Could not start detection", body: errorMessage(e) });
    } finally {
      setStarting(false);
    }
  };

  return (
    <Section
      title={
        <span className="flex items-center gap-2">
          Flyovers
          {all.length > 0 && (
            <span className="font-normal tracking-normal text-fg-faint normal-case tnum">
              {all.length} · {nAccepted} accepted
            </span>
          )}
        </span>
      }
      icon={<Radar />}
      actions={
        <IconButton size="xs" label="Detection settings" active={showOpts} onClick={() => setShowOpts((v) => !v)}>
          <SlidersHorizontal />
        </IconButton>
      }
    >
      <Segmented
        className="mb-2.5 flex w-full"
        size="sm"
        value={preset}
        onChange={applyPreset}
        ariaLabel="Detection preset"
        options={PRESETS.map((p) => ({
          value: p.id,
          label: p.label,
          tip: p.id === "heli" && specLo > 15 ? "This recording's spectrogram starts at 40 Hz: upload it again to analyse helicopter rotors" : p.tip,
          disabled: p.id === "heli" && specLo > 15,
        }))}
      />
      {showOpts && (
        <div className="mb-3 grid grid-cols-[1fr_1fr_64px_64px] items-end gap-2 rounded-lg border border-line bg-surface-2 p-2.5">
          <label className="flex flex-col gap-1 text-2xs text-fg-muted">
            Band from
            <NumberInput
              value={band[0]}
              onCommit={(v) => {
                if (v == null) return;
                setBand([v, band[1]]);
                setPreset("custom");
              }}
              min={minBand}
              max={440}
              suffix="Hz"
              allowEmpty={false}
            />
          </label>
          <label className="flex flex-col gap-1 text-2xs text-fg-muted">
            to
            <NumberInput
              value={band[1]}
              onCommit={(v) => {
                if (v == null) return;
                setBand([band[0], v]);
                setPreset("custom");
              }}
              min={minBand + 5}
              max={450}
              suffix="Hz"
              allowEmpty={false}
            />
          </label>
          <label className="flex flex-col gap-1 text-2xs text-fg-muted">
            Harmonics
            <NumberInput
              value={harmonics}
              onCommit={(v) => {
                if (v == null) return;
                setHarmonics(Math.round(v));
                setPreset("custom");
              }}
              min={1}
              max={4}
              step={1}
              allowEmpty={false}
            />
          </label>
          <label className="flex flex-col gap-1 text-2xs text-fg-muted">
            Top N
            <NumberInput value={top} onCommit={(v) => v != null && setTop(Math.round(v))} min={1} max={100} step={1} allowEmpty={false} />
          </label>
          <p className="col-span-4 text-2xs leading-snug text-fg-faint">
            The band is searched for the fundamental tone ({minBand}–450 Hz): propeller blade-pass is about 60–300 Hz, helicopter main rotors about 10–60 Hz with
            several harmonics. A narrower band is faster. Detection keeps accepted and manual flyovers.
          </p>
        </div>
      )}

      {detectJob ? (
        <div className="mb-3 rounded-lg border border-accent-line/50 bg-accent-soft px-3 py-2.5">
          <div className="flex items-center justify-between text-xs">
            <span className="font-medium text-accent">Detecting flyovers…</span>
            <span className="text-accent tnum">{Math.round(detectJob.progress * 100)}%</span>
          </div>
          <ProgressBar className="mt-1.5" value={detectJob.progress} indeterminate={detectJob.status === "queued"} />
          <div className="mt-1.5 truncate text-2xs text-fg-muted">{detectJob.message}</div>
        </div>
      ) : (
        <div className="mb-3 flex items-center gap-2">
          <Button variant={all.length ? "secondary" : "primary"} size="sm" icon={<Radar />} loading={starting} onClick={detect} disabled={!recordingReady(c)}>
            {all.length ? "Detect again" : "Detect flyovers"}
          </Button>
          <span className="text-2xs text-fg-faint tnum">
            {band[0]}–{band[1]} Hz · {harmonics} harm. · top {top}
          </span>
        </div>
      )}

      {events.isLoading ? (
        <div className="flex flex-col gap-2">
          <Skeleton className="h-10" />
          <Skeleton className="h-10" />
        </div>
      ) : events.error ? (
        <ErrorNote error={events.error} what="Flyover detection" />
      ) : all.length === 0 ? (
        <EmptyState compact icon={<Radar />} title="No flyovers yet">
          Run detection, or press <b>B</b> and drag a box around a sweep on the spectrogram to fit one by hand.
        </EmptyState>
      ) : (
        <div className="-mx-1.5 flex flex-col">
          <div className="grid grid-cols-[20px_26px_1fr_auto] gap-2 px-1.5 pb-1 text-[10px] font-semibold tracking-wider text-fg-faint uppercase">
            <span title="Use in search">Use</span>
            <span>#</span>
            <span>CPA · tone · speed · distance</span>
            <span className="pr-1">Status</span>
          </div>
          {list.map((ev) => (
            <EventRow
              key={ev.id}
              ev={ev}
              n={numbers.get(ev.id) ?? 0}
              selected={selected === ev.id}
              checked={checked.includes(ev.id)}
              onAccept={() => actions.setStatus(ev.id, ev.status === "accepted" ? "candidate" : "accepted")}
              onReject={() => actions.setStatus(ev.id, ev.status === "rejected" ? "candidate" : "rejected")}
              onDelete={() => actions.remove(ev.id)}
              onHarmonics={(h) => actions.setHarmonics(ev.id, h)}
            />
          ))}
          {weak.length > 2 && (
            <button className="mx-1.5 mt-1 self-start rounded-md px-1.5 py-1 text-2xs font-medium text-fg-muted hover:bg-surface-2 hover:text-fg" onClick={() => setShowWeak((v) => !v)}>
              {showWeak ? `Hide ${weak.length} weak fits` : `Show ${weak.length} weak fits (likely noise)`}
            </button>
          )}
          <div className="mt-2 px-1.5 text-2xs leading-snug text-fg-faint">
            Tick two flyovers to use in a search. Click a row to inspect it; <b>A</b>/<b>R</b> accepts or rejects the selected one.
          </div>
        </div>
      )}
    </Section>
  );
}

function EventRow({
  ev,
  n,
  selected,
  checked,
  onAccept,
  onReject,
  onDelete,
  onHarmonics,
}: {
  ev: AircraftEvent;
  n: number;
  selected: boolean;
  checked: boolean;
  onAccept: () => void;
  onReject: () => void;
  onDelete: () => void;
  onHarmonics: (h: number) => void;
}) {
  const color = STATUS_COLOR[ev.status];
  const q = eventQuality(ev);
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={() => selectEvent(ev)}
      onKeyDown={(e) => e.key === "Enter" && selectEvent(ev)}
      className={cn(
        "group grid cursor-pointer grid-cols-[20px_26px_1fr_auto] items-center gap-2 rounded-md px-1.5 py-1.5 transition-colors",
        selected ? "bg-accent-soft ring-1 ring-accent-line ring-inset" : "hover:bg-surface-2",
        (ev.status === "rejected" || (q === "weak" && ev.status !== "accepted")) && !selected && "opacity-55",
      )}
    >
      <Checkbox checked={checked} onChange={() => toggleChecked(ev.id)} ariaLabel={`Use flyover ${n} in the search`} />
      <span
        className="flex h-5 items-center justify-center rounded-[5px] text-2xs font-bold tnum"
        style={{ background: `color-mix(in oklab, ${color} 18%, transparent)`, color, boxShadow: `inset 0 0 0 1px color-mix(in oklab, ${color} 40%, transparent)` }}
      >
        {n}
      </span>
      <div className="min-w-0">
        <div className="flex items-baseline gap-2 text-xs tnum">
          <span className="font-semibold text-fg">{fmtRec(ev.t_cpa, 1)}</span>
          <span className="text-fg">{ev.f0.toFixed(1)} Hz</span>
          <QualityBadge q={q} />
          {ev.origin === "manual" && <Badge className="h-4 px-1 text-[10px]">manual</Badge>}
        </div>
        <div className="mt-0.5 flex items-center gap-1.5 text-2xs whitespace-nowrap text-fg-muted tnum">
          <span>{Math.round(ev.v)} m/s</span>
          <span className="text-fg-faint">·</span>
          <span>{fmtDist(ev.d)}</span>
          <span className="text-fg-faint">·</span>
          <span data-tip="Mean whitened dB along the fitted curve">score {ev.score.toFixed(1)}</span>
        </div>
      </div>
      <div className="flex items-center gap-0.5" onClick={(e) => e.stopPropagation()}>
        <IconButton
          size="xs"
          label={ev.status === "accepted" ? "Accepted (click to undo)" : "Accept"}
          onClick={onAccept}
          className={cn(ev.status === "accepted" ? "!bg-ok/15 !text-ok" : "opacity-60 group-hover:opacity-100")}
        >
          <Check />
        </IconButton>
        <IconButton
          size="xs"
          label={ev.status === "rejected" ? "Rejected (click to undo)" : "Reject"}
          onClick={onReject}
          className={cn(ev.status === "rejected" ? "!bg-danger/15 !text-danger" : "opacity-60 group-hover:opacity-100")}
        >
          <X />
        </IconButton>
        <IconButton size="xs" label="Delete" onClick={onDelete} className="opacity-0 group-hover:opacity-60 hover:!text-danger hover:!opacity-100">
          <Trash2 />
        </IconButton>
      </div>
      {selected && (
        <div className="col-span-4 mt-1 ml-[46px] grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 border-t border-accent-line/30 pt-1.5 text-2xs text-fg-muted tnum" onClick={(e) => e.stopPropagation()}>
          <span className="text-fg-faint">Tone</span>
          <span>
            {ev.f_pre.toFixed(1)} → {ev.f_post.toFixed(1)} Hz <span className="text-fg-faint">(approach → recede)</span>
          </span>
          <span className="text-fg-faint">Fit window</span>
          <span>
            {fmtRec(ev.t_start)} – {fmtRec(ev.t_end)} · band {ev.band[0]}–{ev.band[1]} Hz
          </span>
          <span className="text-fg-faint">Harmonics</span>
          <span className="flex items-center gap-1.5">
            <select
              value={ev.harmonics}
              onChange={(e) => onHarmonics(Number(e.target.value))}
              className="h-5 cursor-pointer rounded border border-line-strong bg-surface-2 px-1 text-2xs text-fg outline-none"
              aria-label="Harmonics"
            >
              {[1, 2, 3, 4].map((h) => (
                <option key={h} value={h}>
                  {h}
                </option>
              ))}
            </select>
            <span className="text-fg-faint">{ev.origin === "manual" ? "fitted by hand" : "detected"}</span>
          </span>
        </div>
      )}
    </div>
  );
}

const QUALITY_TIP = {
  strong: "Strong: clearly a real flyover",
  possible: "Possible: check it on the spectrogram before using it",
  weak: "Weak: score close to a fit to pure noise (about 1)",
};

export function QualityBadge({ q }: { q: "strong" | "possible" | "weak" }) {
  return (
    <span data-tip={QUALITY_TIP[q]}>
      <Badge tone={q === "strong" ? "ok" : q === "possible" ? "warn" : "neutral"} className="h-4 px-1 text-[10px]">
        {q}
      </Badge>
    </span>
  );
}
