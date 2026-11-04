import { useQueryClient } from "@tanstack/react-query";
import {
  Check,
  Clock3,
  CloudLightning,
  Info,
  ListOrdered,
  LocateFixed,
  Play,
  Plus,
  Radio,
  Trash2,
  X,
  Zap,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api, errorMessage } from "../../api/client";
import { isActive, useStartJob } from "../../api/jobs";
import { casePath, qk, useCaseJobs, useLightning, usePatchCase, useSolve, useStrikes } from "../../api/queries";
import type { EventStatus, Flash, LightningSolveSummary, ThunderOnset } from "../../api/types";
import { Button, IconButton } from "../../components/ui/Button";
import { Field, NumberInput, Slider, Switch } from "../../components/ui/form";
import { Badge, EmptyState, ErrorNote, Hint, KV, ProgressBar, Section, Skeleton } from "../../components/ui/misc";
import { confirmDialog } from "../../components/ui/overlay";
import { toast } from "../../components/ui/toast";
import { cn } from "../../lib/cn";
import { fmtAgo, fmtDist, fmtLatLonPretty, fmtNum, fmtRec, fmtSigned } from "../../lib/format";
import { circleRing } from "../../lib/geo";
import { fmtClock, fmtDateTime, fmtUtc } from "../../lib/tz";
import { focusMap, focusTime, useWs, ws } from "../../state/workspace";
import { STATUS_COLOR } from "../timeline/TimelineCanvas";
import { caseTz, recordingReady, useCaseData } from "../workspace/caseContext";
import { SearchAreaBanner, useSearchArea } from "../workspace/SearchArea";
import { useLightningActions } from "./actions";
import { CatalogueSection, KnownEventSection } from "./Catalogue";

export function LightningTab() {
  const c = useCaseData();
  const st = useLightning(c.id, recordingReady(c));
  if (st.error) {
    return (
      <div className="p-4">
        <ErrorNote error={st.error} what="The lightning source" />
      </div>
    );
  }
  return (
    <div>
      <div className="border-b border-line px-4 pt-3.5 pb-3">
        <div className="text-sm font-semibold text-fg">Lightning & events</div>
        <p className="mt-0.5 text-xs leading-relaxed text-fg-muted">
          Thunder, blasts, explosions and other sharp sounds whose time and place are on record. Match their onsets to find where and when.
        </p>
      </div>
      <DetectSection />
      <OnsetsSection />
      {(c.recording?.has_video || (st.data?.flashes.length ?? 0) > 0) && <FlashesSection />}
      <CatalogueSection />
      <KnownEventSection />
      <SolveSection />
      <SolveResults />
    </div>
  );
}

function useLightningJob(word: string) {
  const c = useCaseData();
  const jobs = useCaseJobs(c.id);
  return jobs.find((j) => isActive(j) && j.kind.startsWith("lightning") && j.kind.includes(word));
}

function JobBox({ job, title, note }: { job: { progress: number; message: string; status: string }; title: string; note?: string }) {
  return (
    <div className="rounded-lg border border-accent-line/50 bg-accent-soft px-3 py-2.5">
      <div className="flex items-center justify-between text-xs">
        <span className="font-medium text-accent">{title}</span>
        <span className="text-accent tnum">{Math.round(job.progress * 100)}%</span>
      </div>
      <ProgressBar className="mt-1.5" value={job.progress} indeterminate={job.status === "queued"} />
      <div className="mt-1.5 truncate text-2xs text-fg-muted">{job.message}</div>
      {note && <div className="mt-1 text-2xs text-fg-faint">{note}</div>}
    </div>
  );
}

// ---- detection -----------------------------------------------------------------------------

function DetectSection() {
  const c = useCaseData();
  const startJob = useStartJob();
  const [sens, setSens] = useState(0.5);
  const [gap, setGap] = useState(4);
  const [fsens, setFsens] = useState(0.5);
  const thunderJob = useLightningJob("thunder");
  const flashJob = useLightningJob("flash");
  const go = async (path: string, body: unknown, what: string) => {
    try {
      await startJob(`${casePath(c.id)}/lightning/${path}`, body, { success: (j) => `${j.result?.count ?? 0} ${what}` });
    } catch (e) {
      toast({ tone: "error", title: "Could not start detection", body: errorMessage(e) });
    }
  };
  return (
    <Section title="Detect" icon={<CloudLightning />}>
      <div className="flex flex-col gap-3">
        {thunderJob ? (
          <JobBox job={thunderJob} title="Detecting thunder…" />
        ) : (
          <div className="flex flex-col gap-2">
            <div className="flex items-center gap-3">
              <span className="w-20 text-xs text-fg-muted">Sensitivity</span>
              <Slider value={sens} min={0} max={1} step={0.05} onChange={setSens} ariaLabel="Thunder detection sensitivity" />
              <span className="w-8 text-right text-xs text-fg tnum">{sens.toFixed(2)}</span>
            </div>
            <div className="flex items-center gap-3">
              <span className="w-20 text-xs text-fg-muted">Min. gap</span>
              <NumberInput value={gap} onCommit={(v) => v != null && setGap(v)} min={0.5} max={120} step={1} suffix="s" className="w-24" allowEmpty={false} />
              <div className="flex-1" />
              <Button size="sm" variant="primary" icon={<Radio />} onClick={() => go("detect-thunder", { sensitivity: sens, min_gap_s: gap }, "thunder onsets")}>
                Detect thunder
              </Button>
            </div>
          </div>
        )}
        {c.recording?.has_video &&
          (flashJob ? (
            <JobBox job={flashJob} title="Detecting flashes in the video…" />
          ) : (
            <div className="flex items-center gap-3 border-t border-line pt-3">
              <span className="w-20 text-xs text-fg-muted">Flashes</span>
              <Slider value={fsens} min={0} max={1} step={0.05} onChange={setFsens} ariaLabel="Flash detection sensitivity" />
              <span className="w-8 text-right text-xs text-fg tnum">{fsens.toFixed(2)}</span>
              <Button size="sm" variant="secondary" icon={<Zap />} onClick={() => go("detect-flashes", { sensitivity: fsens }, "flashes")}>
                Detect
              </Button>
            </div>
          ))}
        <Hint>Detection replaces earlier detected candidates and keeps ones you accepted or added by hand.</Hint>
      </div>
    </Section>
  );
}

// ---- onsets / flashes ----------------------------------------------------------------------

function TimeEdit({ t, onCommit }: { t: number; onCommit: (t: number) => void }) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState("");
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (editing) ref.current?.select();
  }, [editing]);
  const parse = (s: string): number | null => {
    const m = /^\s*(?:(\d+):)?(\d+):(\d+(?:\.\d*)?)\s*$/.exec(s);
    if (m) return Number(m[1] ?? 0) * 3600 + Number(m[2]) * 60 + Number(m[3]);
    const n = Number(s);
    return Number.isFinite(n) ? n : null;
  };
  if (!editing)
    return (
      <button
        className="rounded px-1 text-xs font-semibold text-fg hover:bg-surface-4 tnum"
        onClick={(e) => {
          e.stopPropagation();
          setText(fmtRec(t, 2));
          setEditing(true);
        }}
        data-tip="Click to edit the time (m:ss.ss or seconds); or drag the marker on the timeline"
      >
        {fmtRec(t, 2)}
      </button>
    );
  return (
    <input
      ref={ref}
      value={text}
      onClick={(e) => e.stopPropagation()}
      onChange={(e) => setText(e.target.value)}
      onBlur={() => {
        setEditing(false);
        const v = parse(text);
        if (v != null && Math.abs(v - t) > 1e-3) onCommit(v);
      }}
      onKeyDown={(e) => {
        if (e.key === "Enter") (e.target as HTMLInputElement).blur();
        if (e.key === "Escape") setEditing(false);
      }}
      className="h-5 w-20 rounded border border-accent-line bg-surface-2 px-1 text-xs text-fg outline-none tnum"
      aria-label="Time"
    />
  );
}

function StatusButtons({ status, onSet, onDelete }: { status: EventStatus; onSet: (s: EventStatus) => void; onDelete: () => void }) {
  return (
    <div className="flex items-center gap-0.5" onClick={(e) => e.stopPropagation()}>
      <IconButton size="xs" label={status === "accepted" ? "Accepted (undo)" : "Accept"} onClick={() => onSet(status === "accepted" ? "candidate" : "accepted")} className={cn(status === "accepted" ? "!bg-ok/15 !text-ok" : "opacity-60 group-hover:opacity-100")}>
        <Check />
      </IconButton>
      <IconButton size="xs" label={status === "rejected" ? "Rejected (undo)" : "Reject"} onClick={() => onSet(status === "rejected" ? "candidate" : "rejected")} className={cn(status === "rejected" ? "!bg-danger/15 !text-danger" : "opacity-60 group-hover:opacity-100")}>
        <X />
      </IconButton>
      <IconButton size="xs" label="Delete" onClick={onDelete} className="opacity-0 group-hover:opacity-60 hover:!text-danger hover:!opacity-100">
        <Trash2 />
      </IconButton>
    </div>
  );
}

function OnsetsSection() {
  const c = useCaseData();
  const st = useLightning(c.id);
  const a = useLightningActions(c.id);
  const sel = useWs((s) => s.onsetId);
  const tool = useWs((s) => s.tool);
  const onsets = st.data?.onsets ?? [];
  const accepted = onsets.filter((o) => o.status === "accepted").length;
  return (
    <Section
      title={
        <span className="flex items-center gap-2">
          Thunder onsets {onsets.length > 0 && <span className="font-normal tracking-normal text-fg-faint normal-case tnum">{onsets.length} · {accepted} accepted</span>}
        </span>
      }
      icon={<Radio />}
      actions={
        <Button size="xs" variant="ghost" active={tool === "add-onset"} icon={<Plus />} onClick={() => ws.set({ tool: tool === "add-onset" ? "pan" : "add-onset" })} tip="Then click on the timeline to add an onset (O)">
          Add
        </Button>
      }
    >
      {st.isLoading ? (
        <Skeleton className="h-10" />
      ) : onsets.length === 0 ? (
        <EmptyState compact icon={<Radio />} title="No thunder onsets">
          Detect thunder, or click <b>Add</b> and then the timeline wherever a rumble starts.
        </EmptyState>
      ) : (
        <div className="-mx-1.5 flex flex-col">
          {onsets.map((o, i) => (
            <MarkerRow
              key={o.id}
              label={`T${i + 1}`}
              item={o}
              selected={sel === o.id}
              detail={
                <>
                  {fmtNum(o.strength, 1)} dB · ±{fmtNum(o.sigma_s, 2)} s{o.duration_s ? ` · ${fmtNum(o.duration_s, 1)} s long` : ""}
                  {o.origin === "manual" && " · manual"}
                </>
              }
              onSelect={() => {
                ws.set({ onsetId: o.id, flashId: null });
                focusTime(o.t, 60);
              }}
              onTime={(t) => a.patchOnset(o.id, { t })}
              onStatus={(s) => a.patchOnset(o.id, { status: s })}
              onDelete={() => a.deleteOnset(o.id)}
            />
          ))}
        </div>
      )}
    </Section>
  );
}

function FlashesSection() {
  const c = useCaseData();
  const st = useLightning(c.id);
  const a = useLightningActions(c.id);
  const sel = useWs((s) => s.flashId);
  const tool = useWs((s) => s.tool);
  const flashes = st.data?.flashes ?? [];
  return (
    <Section
      title={
        <span className="flex items-center gap-2">
          Flashes {flashes.length > 0 && <span className="font-normal tracking-normal text-fg-faint normal-case tnum">{flashes.length}</span>}
        </span>
      }
      icon={<Zap />}
      actions={
        <Button size="xs" variant="ghost" active={tool === "add-flash"} icon={<Plus />} onClick={() => ws.set({ tool: tool === "add-flash" ? "pan" : "add-flash" })} tip="Then click on the timeline where a flash appears">
          Add
        </Button>
      }
    >
      {flashes.length === 0 ? (
        <EmptyState compact icon={<Zap />} title="No flashes">
          Lightning visible in the video ties the recording clock to the strike catalogue. Detect them, or add them by hand.
        </EmptyState>
      ) : (
        <div className="-mx-1.5 flex flex-col">
          {flashes.map((f, i) => (
            <MarkerRow
              key={f.id}
              label={`F${i + 1}`}
              item={f}
              selected={sel === f.id}
              flash
              detail={
                <>
                  brightness +{fmtNum(f.strength, 0)}
                  {f.origin === "manual" && " · manual"}
                </>
              }
              onSelect={() => {
                ws.set({ flashId: f.id, onsetId: null });
                focusTime(f.t, 60);
              }}
              onTime={(t) => a.patchFlash(f.id, { t })}
              onStatus={(s) => a.patchFlash(f.id, { status: s })}
              onDelete={() => a.deleteFlash(f.id)}
            />
          ))}
        </div>
      )}
    </Section>
  );
}

function MarkerRow({
  label,
  item,
  selected,
  detail,
  flash,
  onSelect,
  onTime,
  onStatus,
  onDelete,
}: {
  label: string;
  item: ThunderOnset | Flash;
  selected: boolean;
  detail: React.ReactNode;
  flash?: boolean;
  onSelect: () => void;
  onTime: (t: number) => void;
  onStatus: (s: EventStatus) => void;
  onDelete: () => void;
}) {
  const color = flash ? "#facc15" : STATUS_COLOR[item.status];
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onSelect}
      onKeyDown={(e) => e.key === "Enter" && onSelect()}
      className={cn(
        "group grid cursor-pointer grid-cols-[30px_1fr_auto] items-center gap-2 rounded-md px-1.5 py-1.5",
        selected ? "bg-accent-soft ring-1 ring-accent-line ring-inset" : "hover:bg-surface-2",
        item.status === "rejected" && !selected && "opacity-55",
      )}
    >
      <span
        className="flex h-5 items-center justify-center rounded-[5px] text-[10px] font-bold tnum"
        style={{ background: `color-mix(in oklab, ${color} 16%, transparent)`, color, boxShadow: `inset 0 0 0 1px color-mix(in oklab, ${color} 40%, transparent)` }}
      >
        {label}
      </span>
      <div className="min-w-0">
        <TimeEdit t={item.t} onCommit={onTime} />
        <div className="mt-0.5 truncate px-1 text-2xs text-fg-muted tnum">{detail}</div>
      </div>
      <StatusButtons status={item.status} onSet={onStatus} onDelete={onDelete} />
    </div>
  );
}

// ---- solve ---------------------------------------------------------------------------------

function SolveSection() {
  const c = useCaseData();
  const st = useLightning(c.id);
  const startJob = useStartJob();
  const job = useLightningJob("solve");
  const [useStart, setUseStart] = useState(c.start_utc != null);
  const [tol, setTol] = useState(5);
  const [useFlashes, setUseFlashes] = useState(true);
  const [spacing, setSpacing] = useState(250);
  const [range, setRange] = useState(25);
  useEffect(() => setUseStart(c.start_utc != null), [c.start_utc]);
  const accOnsets = (st.data?.onsets ?? []).filter((o) => o.status !== "rejected").length;
  const accFlashes = (st.data?.flashes ?? []).filter((f) => f.status === "accepted").length;
  const mode = useStart && c.start_utc != null ? "known_start" : useFlashes && accFlashes > 0 ? "flash_sync" : "free";
  const modeText = {
    known_start: "Known start: the clock comes from the case start time.",
    flash_sync: "Flash sync: the clock comes from matching video flashes to strikes.",
    free: "Free: searches over the clock offset too (needs several good onsets).",
  }[mode];
  const { area, tooLarge } = useSearchArea();
  const blockers: string[] = [];
  if (tooLarge) blockers.push(`draw a region inside ${area?.description ?? "the country"}`);
  if (!st.data?.strikes?.count) blockers.push("fetch or import strikes");
  if (accOnsets === 0) blockers.push("detect or add thunder onsets");

  const run = async () => {
    try {
      await startJob(
        `${casePath(c.id)}/lightning/solve`,
        { start_utc: useStart ? c.start_utc : null, start_tol_s: tol, use_flashes: useFlashes, spacing_m: spacing, max_range_km: range },
        {
          success: () => "Lightning solution ready",
          onDone: (j) => {
            const sid = j.result?.solve_id;
            if (sid) ws.set({ solveId: sid });
          },
        },
      );
    } catch (e) {
      toast({ tone: "error", title: "Could not start the solve", body: errorMessage(e) });
    }
  };

  return (
    <Section title="Solve" icon={<Play />}>
      <div className="flex flex-col gap-2.5">
        <SearchAreaBanner />
        <Switch
          checked={useStart && c.start_utc != null}
          disabled={c.start_utc == null}
          onChange={setUseStart}
          label="Use the case start time"
          hint={c.start_utc != null ? fmtDateTime(c.start_utc, caseTz(c)) : "Unknown: the solve will find the clock itself"}
        />
        <Switch checked={useFlashes} onChange={setUseFlashes} label="Use video flashes" hint={accFlashes ? `${accFlashes} accepted` : "None accepted"} />
        <div className="grid grid-cols-3 gap-2">
          <Field label="Start ± tol.">
            <NumberInput value={tol} onCommit={(v) => v != null && setTol(v)} min={0} max={3600} step={1} suffix="s" allowEmpty={false} />
          </Field>
          <Field label="Grid">
            <NumberInput value={spacing} onCommit={(v) => v != null && setSpacing(v)} min={20} max={5000} step={50} suffix="m" allowEmpty={false} />
          </Field>
          <Field label="Max range">
            <NumberInput value={range} onCommit={(v) => v != null && setRange(v)} min={1} max={200} step={5} suffix="km" allowEmpty={false} />
          </Field>
        </div>
        <div className="rounded-md border border-line bg-surface-2 px-2.5 py-2 text-2xs leading-snug text-fg-muted">{modeText}</div>
        {job ? (
          <JobBox job={job} title="Solving…" />
        ) : (
          <>
            {blockers.length > 0 && <Hint>To run: {blockers.join(", ")}.</Hint>}
            <Button variant="primary" icon={<Play />} disabled={blockers.length > 0} onClick={run}>
              Solve location
            </Button>
          </>
        )}
      </div>
    </Section>
  );
}

function SolveResults() {
  const c = useCaseData();
  const qc = useQueryClient();
  const st = useLightning(c.id);
  const solveId = useWs((s) => s.solveId);
  const solves = st.data?.solves ?? [];
  // default to the newest solve
  useEffect(() => {
    if (!solveId && solves.length) ws.set({ solveId: solves[0].id });
  }, [solveId, solves]);
  const del = async (s: LightningSolveSummary) => {
    const ok = await confirmDialog({ title: "Delete this solve?", confirmLabel: "Delete", danger: true });
    if (!ok) return;
    try {
      await api.del(`${casePath(c.id)}/lightning/solves/${encodeURIComponent(s.id)}`);
      if (ws.get().solveId === s.id) ws.set({ solveId: null });
      void qc.invalidateQueries({ queryKey: qk.lightning(c.id) });
    } catch (e) {
      toast({ tone: "error", title: "Could not delete the solve", body: errorMessage(e) });
    }
  };
  if (!solves.length) return null;
  return (
    <Section title="Results" icon={<ListOrdered />}>
      <div className="mb-2 flex flex-col gap-1">
        {solves.map((s) => (
          <div
            key={s.id}
            role="button"
            tabIndex={0}
            onClick={() => ws.set({ solveId: s.id })}
            className={cn(
              "group flex items-center gap-2 rounded-md border px-2.5 py-1.5 text-xs",
              solveId === s.id ? "border-accent-line/60 bg-accent-soft" : "border-line hover:border-line-strong",
            )}
          >
            <Badge tone="lightning">{s.mode.replace("_", " ")}</Badge>
            <span className="flex-1 truncate text-fg-muted tnum">{s.best ? `${s.best.n_matched} matched · ±${fmtDist(s.best.uncertainty_m)}` : "no solution"}</span>
            <span className="text-2xs text-fg-faint">{fmtAgo(s.created_at)}</span>
            <IconButton
              size="xs"
              label="Delete"
              className="opacity-0 group-hover:opacity-70 hover:!text-danger"
              onClick={(e) => {
                e.stopPropagation();
                void del(s);
              }}
            >
              <Trash2 />
            </IconButton>
          </div>
        ))}
      </div>
      {solveId && <SolveDetail id={solveId} />}
    </Section>
  );
}

function SolveDetail({ id }: { id: string }) {
  const c = useCaseData();
  const tz = caseTz(c);
  const solve = useSolve(c.id, id);
  const st = useLightning(c.id);
  const strikes = useStrikes(c.id, !!st.data?.strikes?.count);
  const patch = usePatchCase(c.id);
  if (solve.isLoading) return <Skeleton className="h-24" />;
  if (solve.error) return <ErrorNote error={solve.error} />;
  const s = solve.data;
  if (!s) return null;
  const b = s.best;
  const onsetIdx = new Map((st.data?.onsets ?? []).map((o, i) => [o.id, i + 1]));
  const strikeById = new Map((strikes.data?.strikes ?? []).map((x) => [x.id, x]));
  const same = b && c.start_utc != null && Math.abs(c.start_utc - b.start_utc) < 0.5 && c.start_utc_source === "lightning";
  return (
    <div className="rounded-lg border border-line bg-surface p-2.5 animate-fade-in">
      {b ? (
        <>
          <div className="flex items-start justify-between gap-2">
            <div>
              <div className="text-2xs font-semibold tracking-wider text-fg-faint uppercase">Best location</div>
              <div className="mt-0.5 text-sm font-semibold text-fg tnum">{fmtLatLonPretty(b.lat, b.lon, 4)}</div>
              <div className="text-xs text-src-lightning tnum">± {fmtDist(b.uncertainty_m)}</div>
            </div>
            <IconButton
              size="xs"
              label="Zoom the map to the uncertainty circle"
              onClick={() => {
                const ring = circleRing(b.lat, b.lon, Math.max(200, b.uncertainty_m) * 1.6);
                const lats = ring.map((p) => p[1]);
                const lons = ring.map((p) => p[0]);
                focusMap({ bbox: { lat_min: Math.min(...lats), lat_max: Math.max(...lats), lon_min: Math.min(...lons), lon_max: Math.max(...lons) } });
              }}
            >
              <LocateFixed />
            </IconButton>
          </div>
          <div className="mt-2">
            <KV label="Implied start">{fmtDateTime(b.start_utc, tz)}</KV>
            <KV label="">
              <span className="text-fg-faint">{fmtUtc(b.start_utc)}</span>
            </KV>
            <KV label="Matched onsets">{b.n_matched}</KV>
            <KV label="RMS residual">{fmtNum(b.rms_s, 2)} s</KV>
            <KV label="Score">{fmtNum(b.score, 2)}</KV>
          </div>
          {s.matches.length > 0 && (
            <div className="mt-2">
              <div className="mb-1 grid grid-cols-[34px_1fr_60px_58px] gap-2 text-[10px] font-semibold tracking-wider text-fg-faint uppercase">
                <span>Onset</span>
                <span>Strike (UTC)</span>
                <span className="text-right">Residual</span>
                <span className="text-right">Distance</span>
              </div>
              {s.matches.map((m) => {
                const sk = strikeById.get(m.strike_id);
                return (
                  <div key={`${m.onset_id}-${m.strike_id}`} className="grid grid-cols-[34px_1fr_60px_58px] gap-2 py-0.5 text-xs tnum">
                    <span className="font-semibold text-fg">T{onsetIdx.get(m.onset_id) ?? "?"}</span>
                    <span className="truncate text-fg-muted">{sk ? fmtClock(sk.t_utc, "UTC") : m.strike_id}</span>
                    <span className={cn("text-right", Math.abs(m.residual_s) > 2 ? "text-warn" : "text-fg-muted")}>{fmtSigned(m.residual_s, 2)}</span>
                    <span className="text-right text-fg-muted">{fmtDist(m.distance_m)}</span>
                  </div>
                );
              })}
            </div>
          )}
          <Button
            size="sm"
            className="mt-2.5"
            variant={same ? "ghost" : "secondary"}
            icon={<Clock3 />}
            disabled={!!same}
            onClick={() =>
              patch.mutate(
                { start_utc: b.start_utc, start_utc_source: "lightning" },
                {
                  onSuccess: () => toast({ tone: "success", title: "Start time set from lightning", body: fmtDateTime(b.start_utc, tz) }),
                  onError: (e) => toast({ tone: "error", title: "Could not set the start time", body: errorMessage(e) }),
                },
              )
            }
          >
            {same ? "Is the case start time" : "Use as case start time"}
          </Button>
        </>
      ) : (
        <div className="text-xs text-fg-muted">No location fits these onsets and strikes.</div>
      )}
      {s.notes.length > 0 && (
        <ul className="mt-2.5 flex flex-col gap-1 border-t border-line pt-2">
          {s.notes.map((n, i) => (
            <li key={i} className="flex items-start gap-1.5 text-2xs leading-snug text-fg-muted">
              <Info className="mt-px size-3 shrink-0 text-info" />
              {n}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
