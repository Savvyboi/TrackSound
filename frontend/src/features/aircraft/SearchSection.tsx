import { Crosshair, Gauge, Play, Sparkles, Target, TriangleAlert } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { api, errorMessage, isUnavailable } from "../../api/client";
import { isActive, useStartJob } from "../../api/jobs";
import { casePath, useAircraftEvents, useCaseJobs, useFlightSet } from "../../api/queries";
import type { AircraftSearchRequest, SearchEstimate } from "../../api/types";
import { Button } from "../../components/ui/Button";
import { Field, Input, NumberInput, Segmented, Switch } from "../../components/ui/form";
import { Hint, ProgressBar, Section, Spinner } from "../../components/ui/misc";
import { toast } from "../../components/ui/toast";
import { cn } from "../../lib/cn";
import { fmtCoord, fmtDuration, fmtEstimate, fmtInt, fmtRec } from "../../lib/format";
import { useDebounced } from "../../lib/hooks";
import { fromUtcInput, toUtcInput } from "../../lib/tz";
import { focusMap, setTab, useWs, ws } from "../../state/workspace";
import { useCaseData } from "../workspace/caseContext";
import { eventNumbers, strongBetween, suggestPair } from "./actions";

const MODES = [
  { value: "blind" as const, label: "Blind", tip: "Two flyovers, unknown clock: their time gap must match two real flights" },
  { value: "point" as const, label: "Known point", tip: "Score one location (check a claim) with two flyovers" },
  { value: "single" as const, label: "Single + time", tip: "One flyover whose absolute time is known" },
];

export function SearchSection() {
  const c = useCaseData();
  const events = useAircraftEvents(c.id);
  const flights = useFlightSet(c.id);
  const mode = useWs((s) => s.searchMode);
  const checked = useWs((s) => s.checkedEventIds);
  const pickPoint = useWs((s) => s.pickPoint);
  const mapTool = useWs((s) => s.mapTool);
  const jobs = useCaseJobs(c.id);
  const running = jobs.find((j) => isActive(j) && j.kind.startsWith("aircraft") && (j.kind.includes("search") || j.kind.includes("refine")));
  const startJob = useStartJob();
  const [spacing, setSpacing] = useState(200);
  const [tol, setTol] = useState<number | null>(null);
  const [silence, setSilence] = useState(true);
  const [useDem, setUseDem] = useState(true);
  const [eventUtcText, setEventUtcText] = useState("");
  const [starting, setStarting] = useState(false);

  const list = events.data ?? [];
  const numbers = eventNumbers(list);
  const byId = new Map(list.map((e) => [e.id, e]));
  const chosen = checked.map((id) => byId.get(id)).filter((e): e is NonNullable<typeof e> => !!e);
  const pair = suggestPair(list);
  const needed = mode === "single" ? 1 : 2;
  const evs = chosen.slice(0, needed);
  const single = mode === "single" ? evs[0] : undefined;

  // default event time for single mode: case start + event CPA
  const defaultEventUtc = single && c.start_utc != null ? c.start_utc + single.t_cpa : null;
  useEffect(() => {
    setEventUtcText(defaultEventUtc != null ? toUtcInput(defaultEventUtc) : "");
  }, [defaultEventUtc]);
  const eventUtc = eventUtcText ? fromUtcInput(eventUtcText) : null;

  const request: AircraftSearchRequest | null = useMemo(() => {
    if (evs.length < needed) return null;
    const r: AircraftSearchRequest = { mode, events: evs.map((e) => e.id), spacing_m: spacing, use_dem: useDem };
    if (tol != null) r.tol_s = tol;
    if (mode === "blind") r.silence = silence;
    if (mode === "point") {
      if (!pickPoint) return null;
      r.point = pickPoint;
    }
    if (mode === "single") {
      if (eventUtc == null) return null;
      r.event_utc = eventUtc;
    }
    return r;
  }, [mode, evs.map((e) => e.id).join(","), spacing, useDem, tol, silence, pickPoint, eventUtc, needed]); // eslint-disable-line react-hooks/exhaustive-deps

  const debounced = useDebounced(request, 400);
  const [est, setEst] = useState<{ data?: SearchEstimate; error?: unknown; loading: boolean }>({ loading: false });
  useEffect(() => {
    if (!debounced || !c.region || flights.data?.status !== "ready") {
      setEst({ loading: false });
      return;
    }
    let alive = true;
    setEst((e) => ({ ...e, loading: true }));
    api
      .post<SearchEstimate>(`${casePath(c.id)}/aircraft/search/estimate`, debounced)
      .then((data) => alive && setEst({ data, loading: false }))
      .catch((error) => alive && setEst({ error, loading: false }));
    return () => {
      alive = false;
    };
  }, [debounced, c.id, c.region, flights.data?.status]);

  const run = async () => {
    if (!request) return;
    setStarting(true);
    try {
      await startJob(`${casePath(c.id)}/aircraft/search`, request, {
        success: () => "Results are ready",
        onDone: (job) => {
          const sid = job.result?.search_id;
          if (sid) ws.set({ searchId: sid, candidateRank: 1 });
        },
      });
    } catch (e) {
      toast({ tone: "error", title: "Could not start the search", body: errorMessage(e) });
    } finally {
      setStarting(false);
    }
  };

  const blockers: string[] = [];
  if (!c.region) blockers.push("set a search region");
  if (flights.data?.status !== "ready") blockers.push("fetch the flights");
  if (evs.length < needed) blockers.push(needed === 1 ? "tick one flyover" : `tick two flyovers (${chosen.length} ticked)`);
  if (mode === "point" && !pickPoint) blockers.push("choose the point on the map");
  if (mode === "single" && eventUtc == null) blockers.push("enter the flyover's UTC time");

  return (
    <Section title="Search" icon={<Target />} id="aircraft-search">
      <Segmented className="mb-3 flex w-full" value={mode} onChange={(m) => ws.set({ searchMode: m })} options={MODES} ariaLabel="Search mode" />
      <p className="-mt-1 mb-3 text-xs leading-relaxed text-fg-muted">{MODES.find((m) => m.value === mode)!.tip}.</p>

      {/* events */}
      <div className="mb-3 flex flex-col gap-1.5">
        <div className="text-xs font-medium text-fg-muted">{needed === 1 ? "Flyover" : "Flyovers"}</div>
        <div className="flex flex-wrap gap-1.5">
          {evs.length ? (
            evs.map((e) => (
              <span key={e.id} className="inline-flex items-center gap-1.5 rounded-md border border-accent-line/50 bg-accent-soft px-2 py-1 text-xs text-fg tnum">
                <b className="text-accent">#{numbers.get(e.id)}</b> {fmtRec(e.t_cpa)} · {e.f0.toFixed(0)} Hz
              </span>
            ))
          ) : (
            <span className="text-xs text-fg-faint">Tick flyovers in the list above.</span>
          )}
          {evs.length === 2 && <span className="self-center text-2xs text-fg-faint tnum">{fmtDuration(Math.abs(evs[1].t_cpa - evs[0].t_cpa))} apart</span>}
        </div>
        {needed === 2 && evs.length === 2 && strongBetween(list, evs[0], evs[1]) > 0 && (
          <div className="flex items-start gap-1.5 text-2xs leading-snug text-warn">
            <TriangleAlert className="mt-px size-3 shrink-0" />
            <span>
              {strongBetween(list, evs[0], evs[1])} other strong flyover{strongBetween(list, evs[0], evs[1]) > 1 ? "s" : ""} between these two. Adjacent flyovers usually locate better on a busy sky.
            </span>
          </div>
        )}
        {pair && (chosen.length < needed || (needed === 2 && !pair.every((p) => checked.includes(p.id)))) && (
          <button
            className="flex items-center gap-1.5 self-start rounded-md px-1 py-0.5 text-2xs font-medium text-accent hover:bg-accent-soft"
            onClick={() => ws.set({ checkedEventIds: needed === 1 ? [pair[0].id] : pair.map((p) => p.id) })}
          >
            <Sparkles className="size-3" />
            Suggested: #{numbers.get(pair[0].id)}
            {needed === 2 && ` and #${numbers.get(pair[1].id)}`} (strongest adjacent flyovers, {fmtDuration(Math.abs(pair[1].t_cpa - pair[0].t_cpa))} apart)
          </button>
        )}
      </div>

      {mode === "point" && (
        <div className="mb-3 flex flex-col gap-1.5">
          <div className="text-xs font-medium text-fg-muted">Point to check</div>
          <div className="flex items-center gap-2">
            <div className={cn("flex-1 truncate rounded-md border border-line-strong bg-surface-2 px-2.5 py-1.5 text-xs tnum", !pickPoint && "text-fg-faint")}>
              {pickPoint ? fmtCoord(pickPoint.lat, pickPoint.lon, 5) : "Not chosen"}
            </div>
            <Button size="sm" variant="secondary" active={mapTool === "pick-point"} icon={<Crosshair />} onClick={() => ws.set({ mapTool: mapTool === "pick-point" ? "none" : "pick-point" })}>
              {mapTool === "pick-point" ? "Click map…" : "Pick on map"}
            </Button>
          </div>
          {pickPoint && (
            <button className="self-start text-2xs text-fg-muted hover:text-fg" onClick={() => focusMap({ center: pickPoint, zoom: 13 })}>
              Show on map
            </button>
          )}
        </div>
      )}

      {mode === "single" && (
        <Field
          label="Heard closest approach (UTC)"
          className="mb-3"
          hint={c.start_utc != null ? "Defaults to the case start time plus the flyover's CPA." : "The case start time is unknown: enter when this flyover was heard."}
        >
          <Input type="datetime-local" step={1} value={eventUtcText} onChange={(e) => setEventUtcText(e.target.value)} />
        </Field>
      )}

      <div className="mb-3 grid grid-cols-2 gap-2.5">
        {mode !== "point" && (
          <Field label="Grid spacing" hint="Finer is slower">
            <NumberInput value={spacing} onCommit={(v) => v != null && setSpacing(Math.round(v))} min={20} max={5000} step={50} suffix="m" allowEmpty={false} />
          </Field>
        )}
        <Field label={mode === "single" ? "Time tolerance" : "Pairing tolerance"} hint={`Default ${mode === "single" ? 30 : 6} s`}>
          <NumberInput value={tol} onCommit={setTol} min={0.5} max={600} step={1} suffix="s" placeholder={mode === "single" ? "30" : "6"} />
        </Field>
      </div>
      <div className="mb-3 flex flex-col gap-2.5">
        <Switch checked={useDem} onChange={setUseDem} label="Use terrain elevation" hint="Listener height from terrain tiles (slower first time, more accurate)" />
        {mode === "blind" && (
          <Switch checked={silence} onChange={setSilence} label="Silence filter" hint="Drop points that would have heard another close prop between the two flyovers" />
        )}
      </div>

      {/* estimate + run */}
      {running ? (
        <div className="rounded-lg border border-accent-line/50 bg-accent-soft px-3 py-2.5">
          <div className="flex items-center justify-between text-xs">
            <span className="font-medium text-accent">{running.kind.includes("refine") ? "Refining…" : "Searching…"}</span>
            <span className="text-accent tnum">{Math.round(running.progress * 100)}%</span>
          </div>
          <ProgressBar className="mt-1.5" value={running.progress} indeterminate={running.status === "queued"} />
          <div className="mt-1.5 truncate text-2xs text-fg-muted">{running.message}</div>
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          {request && (
            <div className="flex items-center gap-2 rounded-md border border-line bg-surface-2 px-2.5 py-2 text-xs">
              <Gauge className="size-3.5 shrink-0 text-fg-faint" />
              {est.loading && !est.data ? (
                <span className="flex items-center gap-2 text-fg-faint">
                  <Spinner className="size-3" /> Estimating cost…
                </span>
              ) : est.error ? (
                <span className="text-fg-muted">{isUnavailable(est.error) ? "Cost estimate not available" : errorMessage(est.error)}</span>
              ) : est.data ? (
                <span className="text-fg-muted tnum">
                  <b className="font-semibold text-fg">{fmtEstimate(est.data.est_seconds)}</b> · {fmtInt(est.data.n_points)} grid points × {fmtInt(est.data.n_segments)} flight segments
                </span>
              ) : (
                <span className="text-fg-faint">Cost estimate appears here</span>
              )}
            </div>
          )}
          {est.data && est.data.est_seconds > 900 && (
            <div className="flex items-start gap-2 text-2xs text-warn">
              <TriangleAlert className="mt-px size-3.5 shrink-0" /> This will take a while. A smaller region or coarser grid is faster.
            </div>
          )}
          {blockers.length > 0 && (
            <Hint>
              To run: {blockers.join(", ")}.
              {!c.region && (
                <>
                  {" "}
                  <button className="font-medium text-accent hover:underline" onClick={() => setTab("setup")}>
                    Open Setup
                  </button>
                </>
              )}
            </Hint>
          )}
          <Button variant="primary" icon={<Play />} disabled={!request || blockers.length > 0} loading={starting} onClick={run}>
            Run {MODES.find((m) => m.value === mode)!.label.toLowerCase()} search
          </Button>
        </div>
      )}
    </Section>
  );
}
