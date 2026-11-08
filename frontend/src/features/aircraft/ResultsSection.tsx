import { useQueryClient } from "@tanstack/react-query";
import { ChevronRight, CircleAlert, Clock3, Copy, Crosshair, Info, ListOrdered, LocateFixed, MapPinned, ScanSearch, Trash2 } from "lucide-react";
import { useState } from "react";
import { api, errorMessage } from "../../api/client";
import { useStartJob } from "../../api/jobs";
import { casePath, qk, useAircraftEvents, useFlightSet, usePatchCase, useSearch, useSearches } from "../../api/queries";
import type { AircraftCandidate, AircraftSearch, BBox } from "../../api/types";
import { Button, IconButton } from "../../components/ui/Button";
import { Checkbox, NumberInput } from "../../components/ui/form";
import { Badge, EmptyState, ErrorNote, KV, Section, Skeleton, Spinner } from "../../components/ui/misc";
import { confirmDialog, Popover, usePopover } from "../../components/ui/overlay";
import { toast } from "../../components/ui/toast";
import { cn } from "../../lib/cn";
import { fmtAgo, fmtCoord, fmtDist, fmtDuration, fmtInt, fmtLatLonPretty, fmtPct, fmtSigned } from "../../lib/format";
import { bboxAround, boundsOf } from "../../lib/geo";
import { fmtClock, fmtDateTime } from "../../lib/tz";
import { focusMap, useWs, ws } from "../../state/workspace";
import { caseTz, useCaseData } from "../workspace/caseContext";
import { eventNumbers } from "./actions";
import { CategoryIcon } from "./FlightsSection";

const MODE_LABEL: Record<AircraftSearch["mode"], string> = { blind: "Blind", point: "Known point", single: "Single", refine: "Refine" };

function marginTone(m: number | null): "ok" | "warn" | "danger" | "neutral" {
  if (m == null) return "neutral";
  if (m >= 0.15) return "ok";
  if (m >= 0.05) return "warn";
  return "danger";
}

export function ResultsSection() {
  const c = useCaseData();
  const searches = useSearches(c.id);
  const searchId = useWs((s) => s.searchId);
  const list = searches.data ?? [];

  const select = (s: AircraftSearch) => {
    if (searchId === s.id) {
      ws.set({ searchId: null, candidateRank: null });
      return;
    }
    ws.set({ searchId: s.id, candidateRank: s.best ? 1 : null });
    const pts = s.candidates.length ? s.candidates : [];
    const b = s.bbox ?? boundsOf(pts);
    if (b) focusMap({ bbox: b });
  };

  return (
    <Section
      title={
        <span className="flex items-center gap-2">
          Results {list.length > 0 && <span className="font-normal tracking-normal text-fg-faint normal-case tnum">{list.length}</span>}
        </span>
      }
      icon={<ListOrdered />}
    >
      {searches.isLoading ? (
        <Skeleton className="h-14" />
      ) : searches.error ? (
        <ErrorNote error={searches.error} what="Aircraft searches" />
      ) : list.length === 0 ? (
        <EmptyState compact icon={<ScanSearch />} title="No searches yet">
          Run a search above. Results appear here with ranked candidate locations, the flights each one heard, and the implied start time.
        </EmptyState>
      ) : (
        <div className="flex flex-col gap-2">
          {list.map((s) => (
            <SearchCard key={s.id} s={s} open={searchId === s.id} onToggle={() => select(s)} />
          ))}
        </div>
      )}
    </Section>
  );
}

function SearchCard({ s, open, onToggle }: { s: AircraftSearch; open: boolean; onToggle: () => void }) {
  const c = useCaseData();
  const qc = useQueryClient();
  const del = async () => {
    const ok = await confirmDialog({ title: "Delete this search?", body: "Its candidates and heat map are removed.", confirmLabel: "Delete", danger: true });
    if (!ok) return;
    try {
      await api.del(`${casePath(c.id)}/aircraft/searches/${encodeURIComponent(s.id)}`);
      if (ws.get().searchId === s.id) ws.set({ searchId: null, candidateRank: null });
      void qc.invalidateQueries({ queryKey: qk.searches(c.id) });
    } catch (e) {
      toast({ tone: "error", title: "Could not delete the search", body: errorMessage(e) });
    }
  };
  const failed = s.status === "error";
  return (
    <div className={cn("overflow-hidden rounded-lg border transition-colors", open ? "border-accent-line/60 bg-surface-2/60" : "border-line bg-surface-2/30 hover:border-line-strong")}>
      <div role="button" tabIndex={0} onClick={onToggle} onKeyDown={(e) => e.key === "Enter" && onToggle()} className="group flex cursor-pointer items-start gap-2 px-2.5 py-2">
        <ChevronRight className={cn("mt-0.5 size-3.5 shrink-0 text-fg-faint transition-transform", open && "rotate-90 text-accent")} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <Badge tone={s.mode === "refine" ? "info" : "aircraft"}>{MODE_LABEL[s.mode]}</Badge>
            <span className="text-2xs text-fg-faint">{fmtAgo(s.created_at)}</span>
            {!failed && s.stats && (
              <span className="truncate text-2xs text-fg-faint tnum">
                · {fmtInt(s.stats.n_points)} pts · {fmtDuration(s.stats.elapsed_s)}
              </span>
            )}
          </div>
          {failed ? (
            <div className="mt-1 flex items-start gap-1.5 text-2xs text-danger">
              <CircleAlert className="mt-px size-3 shrink-0" />
              <span className="line-clamp-2">{s.error || "The search failed"}</span>
            </div>
          ) : s.best ? (
            <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs tnum">
              <span className="text-fg">
                score <b>{s.best.score.toFixed(2)}</b>
              </span>
              <Badge tone={marginTone(s.margin)} title="Margin over the best other flight pair">
                margin {s.margin == null ? "n/a" : fmtPct(s.margin)}
              </Badge>
              <span className="text-2xs text-fg-faint">{s.stats?.n_candidates ?? s.candidates.length} candidates</span>
            </div>
          ) : (
            <div className="mt-1 text-2xs text-fg-muted">No candidate matched.</div>
          )}
        </div>
        <IconButton
          size="xs"
          label="Delete search"
          className="opacity-0 group-hover:opacity-70 hover:!text-danger hover:!opacity-100"
          onClick={(e) => {
            e.stopPropagation();
            void del();
          }}
        >
          <Trash2 />
        </IconButton>
      </div>
      {s.notes && s.notes.length > 0 && (
        <ul className="mx-2.5 mb-2 flex flex-col gap-1 rounded-md border border-warn/25 bg-warn/8 px-2.5 py-1.5">
          {s.notes.map((n, i) => (
            <li key={i} className="flex items-start gap-1.5 text-2xs leading-snug text-fg">
              <Info className="mt-px size-3 shrink-0 text-warn" />
              <span>{n}</span>
            </li>
          ))}
        </ul>
      )}
      {open && <SearchDetail summary={s} />}
    </div>
  );
}

function SearchDetail({ summary }: { summary: AircraftSearch }) {
  const c = useCaseData();
  const full = useSearch(c.id, summary.id);
  const rank = useWs((s) => s.candidateRank);
  const s = full.data ?? summary;
  const cands = s.candidates;
  const cand = cands.find((k) => k.rank === rank) ?? null;
  return (
    <div className="border-t border-line px-2.5 pt-2 pb-2.5">
      {full.data?.notes && full.data.notes.length > (summary.notes?.length ?? 0) && (
        <ul className="mb-2 flex flex-col gap-1">
          {full.data.notes.slice(summary.notes?.length ?? 0).map((n, i) => (
            <li key={i} className="flex items-start gap-1.5 text-2xs leading-snug text-fg-muted">
              <Info className="mt-px size-3 shrink-0 text-info" />
              {n}
            </li>
          ))}
        </ul>
      )}
      {full.isLoading && !cands.length ? (
        <Skeleton className="h-10" />
      ) : cands.length === 0 ? (
        <div className="text-2xs text-fg-muted">No candidates.</div>
      ) : (
        <>
          <div className="mb-1 grid grid-cols-[22px_1fr_52px_62px] gap-2 px-1 text-[10px] font-semibold tracking-wider text-fg-faint uppercase">
            <span>#</span>
            <span>Location</span>
            <span className="text-right">Score</span>
            <span className="text-right">Start</span>
          </div>
          <div className="flex flex-col">
            {cands.slice(0, full.data ? 12 : 5).map((k) => (
              <button
                key={k.rank}
                onClick={() => {
                  ws.set({ candidateRank: k.rank === rank ? null : k.rank });
                  if (k.rank !== rank) focusMap({ center: { lat: k.lat, lon: k.lon }, zoom: 12.5 });
                }}
                className={cn(
                  "grid grid-cols-[22px_1fr_52px_62px] items-center gap-2 rounded-md px-1 py-1 text-left text-xs tnum",
                  k.rank === rank ? "bg-accent-soft ring-1 ring-accent-line ring-inset" : "hover:bg-surface-3",
                )}
              >
                <span className={cn("flex size-5 items-center justify-center rounded-full text-[10px] font-bold", k.rank === 1 ? "bg-[#67e8f9] text-[#06080c]" : "bg-surface-4 text-fg-muted")}>
                  {k.rank}
                </span>
                <span className="truncate text-fg">{fmtCoord(k.lat, k.lon, 4)}</span>
                <span className="text-right text-fg-muted">{k.score.toFixed(2)}</span>
                <span className="text-right text-fg-muted">{fmtClock(k.start_utc, caseTz(c), false)}</span>
              </button>
            ))}
          </div>
          {full.isFetching && !full.data && (
            <div className="mt-1 flex items-center gap-1.5 text-2xs text-fg-faint">
              <Spinner className="size-3" /> Loading all candidates and the heat map…
            </div>
          )}
        </>
      )}
      {cand && <CandidateDetail s={s} k={cand} />}
    </div>
  );
}

function CandidateDetail({ s, k }: { s: AircraftSearch; k: AircraftCandidate }) {
  const c = useCaseData();
  const tz = caseTz(c);
  const patch = usePatchCase(c.id);
  const events = useAircraftEvents(c.id);
  const numbers = eventNumbers(events.data);
  const flightSet = useFlightSet(c.id);
  const cats = new Map((flightSet.data?.flights ?? []).map((f) => [f.idx, f.category]));
  const isBest = k.rank === 1;
  const sameStart = c.start_utc != null && Math.abs(c.start_utc - k.start_utc) < 0.5 && c.start_utc_source === "aircraft";

  const useStart = () =>
    patch.mutate(
      { start_utc: k.start_utc, start_utc_source: "aircraft" },
      {
        onSuccess: () => toast({ tone: "success", title: "Start time set from the aircraft match", body: fmtDateTime(k.start_utc, tz) }),
        onError: (e) => toast({ tone: "error", title: "Could not set the start time", body: errorMessage(e) }),
      },
    );
  const setRegion = () => {
    const prev: BBox | null = c.region;
    const region = bboxAround(k.lat, k.lon, 5);
    patch.mutate(
      { region },
      {
        onSuccess: () => {
          focusMap({ bbox: region });
          toast({
            tone: "success",
            title: "Region set to 10 × 10 km around the candidate",
            action: prev ? { label: "Undo", onClick: () => patch.mutate({ region: prev }) } : undefined,
          });
        },
        onError: (e) => toast({ tone: "error", title: "Could not set the region", body: errorMessage(e) }),
      },
    );
  };

  return (
    <div className="mt-2.5 rounded-lg border border-line bg-surface p-2.5 animate-fade-in">
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="text-2xs font-semibold tracking-wider text-fg-faint uppercase">Candidate {k.rank}</div>
          <div className="mt-0.5 text-sm font-semibold text-fg tnum">{fmtLatLonPretty(k.lat, k.lon, 5)}</div>
        </div>
        <div className="flex gap-0.5">
          <IconButton
            size="xs"
            label="Copy coordinates"
            onClick={() => {
              void navigator.clipboard?.writeText(fmtCoord(k.lat, k.lon, 6));
              toast({ tone: "info", title: "Coordinates copied", body: fmtCoord(k.lat, k.lon, 6) });
            }}
          >
            <Copy />
          </IconButton>
          <IconButton size="xs" label="Zoom the map here" onClick={() => focusMap({ center: { lat: k.lat, lon: k.lon }, zoom: 14 })}>
            <LocateFixed />
          </IconButton>
        </div>
      </div>
      <div className="mt-2">
        <KV label="Score">{k.score.toFixed(3)}</KV>
        {isBest && (
          <KV label="Margin over runner-up">
            <span className={cn(marginTone(s.margin) === "ok" ? "text-ok" : marginTone(s.margin) === "warn" ? "text-warn" : marginTone(s.margin) === "danger" ? "text-danger" : "")}>
              {s.margin == null ? "no runner-up" : fmtPct(s.margin, 1)}
            </span>
          </KV>
        )}
        <KV label="Ground elevation">{Math.round(k.elev_m)} m</KV>
        <KV label="Implied start">{fmtDateTime(k.start_utc, tz)}</KV>
        <KV label="">
          <span className="text-fg-faint">
            {fmtClock(k.start_utc, "UTC")} UTC · shift {fmtSigned(k.shift_s)}
          </span>
        </KV>
      </div>
      {k.flights.length > 0 && (
        <div className="mt-2">
          <div className="mb-1 text-2xs font-semibold tracking-wider text-fg-faint uppercase">Flights heard</div>
          <div className="flex flex-col gap-1">
            {k.flights.map((f) => (
              <button
                key={`${f.event_id}-${f.flight_idx}`}
                onClick={() => ws.set({ flightIdx: ws.get().flightIdx === f.flight_idx ? null : f.flight_idx })}
                className="flex items-center gap-2 rounded-md border border-line bg-surface-2 px-2 py-1.5 text-left text-xs hover:border-line-strong"
              >
                <span className="flex h-5 min-w-5 items-center justify-center rounded-[5px] bg-[#0891b2]/12 px-1 text-[10px] font-bold text-[#0e7490] tnum dark:bg-[#67e8f9]/15 dark:text-[#67e8f9]">
                  {numbers.get(f.event_id) ?? "?"}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="inline-flex items-center gap-1.5">
                    <CategoryIcon category={cats.get(f.flight_idx)} />
                    <span className="font-medium text-fg">{f.reg || f.icao.toUpperCase()}</span>
                  </span>{" "}
                  <span className="text-fg-muted">{f.type ?? ""}</span>
                  <span className="block truncate text-2xs text-fg-faint">{f.desc ?? ""}</span>
                </span>
                <span className="text-right text-2xs text-fg-muted tnum">
                  CPA {fmtDist(f.d_m)}
                  <span className="block text-fg-faint">
                    {Math.round(f.v_mps)} m/s · {fmtClock(f.t_heard_utc, "UTC")}
                  </span>
                </span>
              </button>
            ))}
          </div>
        </div>
      )}
      <div className="mt-2.5 flex flex-wrap gap-1.5">
        <Button size="sm" variant={sameStart ? "ghost" : "secondary"} icon={<Clock3 />} onClick={useStart} disabled={sameStart}>
          {sameStart ? "Is the case start time" : "Use as case start time"}
        </Button>
        <Button size="sm" variant="secondary" icon={<MapPinned />} onClick={setRegion}>
          Set region around this
        </Button>
        {s.status === "done" && <RefineButton s={s} k={k} />}
      </div>
    </div>
  );
}

function RefineButton({ s, k }: { s: AircraftSearch; k: AircraftCandidate }) {
  const c = useCaseData();
  const pop = usePopover();
  const events = useAircraftEvents(c.id);
  const numbers = eventNumbers(events.data);
  const startJob = useStartJob();
  const used = new Set<string>(((s.request as { events?: string[] }).events ?? []).concat(k.flights.map((f) => f.event_id)));
  const extras = (events.data ?? []).filter((e) => e.status === "accepted" && !used.has(e.id));
  const [spacing, setSpacing] = useState(50);
  const [half, setHalf] = useState(600);
  const [pick, setPick] = useState<string[]>(() => extras.map((e) => e.id));
  const [busy, setBusy] = useState(false);
  const run = async () => {
    setBusy(true);
    try {
      await startJob(
        `${casePath(c.id)}/aircraft/searches/${encodeURIComponent(s.id)}/refine`,
        { rank: k.rank, spacing_m: spacing, half_m: half, extra_events: pick },
        {
          success: () => "Refined result is ready",
          onDone: (job) => {
            const sid = job.result?.search_id;
            if (sid) ws.set({ searchId: sid, candidateRank: 1 });
          },
        },
      );
      pop.setOpen(false);
    } catch (e) {
      toast({ tone: "error", title: "Could not start refinement", body: errorMessage(e) });
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <Button ref={pop.anchor} size="sm" variant="secondary" icon={<Crosshair />} active={pop.open} onClick={pop.toggle}>
        Refine…
      </Button>
      <Popover open={pop.open} onClose={() => pop.setOpen(false)} anchor={pop.anchor} align="start" width={300}>
        <div className="p-3">
          <div className="text-sm font-semibold text-fg">Refine around candidate {k.rank}</div>
          <p className="mt-1 text-2xs leading-snug text-fg-muted">A fine grid around this point, scoring every flyover locked to one clock. Extra accepted flyovers sharpen the answer.</p>
          <div className="mt-3 grid grid-cols-2 gap-2">
            <label className="flex flex-col gap-1 text-2xs text-fg-muted">
              Spacing
              <NumberInput value={spacing} onCommit={(v) => v != null && setSpacing(v)} min={5} max={500} step={10} suffix="m" allowEmpty={false} />
            </label>
            <label className="flex flex-col gap-1 text-2xs text-fg-muted">
              Half-width
              <NumberInput value={half} onCommit={(v) => v != null && setHalf(v)} min={50} max={10000} step={100} suffix="m" allowEmpty={false} />
            </label>
          </div>
          <div className="mt-3 text-2xs font-medium text-fg-muted">Extra flyovers</div>
          {extras.length === 0 ? (
            <div className="mt-1 text-2xs text-fg-faint">No other accepted flyovers. Accept more in the list to include them.</div>
          ) : (
            <div className="mt-1.5 flex flex-col gap-1.5">
              {extras.map((e) => (
                <Checkbox
                  key={e.id}
                  checked={pick.includes(e.id)}
                  onChange={(v) => setPick((p) => (v ? [...p, e.id] : p.filter((x) => x !== e.id)))}
                  label={
                    <span className="tnum">
                      #{numbers.get(e.id)} · {e.f0.toFixed(0)} Hz · score {e.score.toFixed(1)}
                    </span>
                  }
                />
              ))}
            </div>
          )}
          <Button className="mt-3 w-full" variant="primary" loading={busy} onClick={run}>
            Run refinement
          </Button>
        </div>
      </Popover>
    </>
  );
}
