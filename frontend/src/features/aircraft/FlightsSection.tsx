import { CloudDownload, Helicopter, Plane, PlaneTakeoff, Search } from "lucide-react";
import { useMemo, useState } from "react";
import { errorMessage } from "../../api/client";
import { isActive, useStartJob } from "../../api/jobs";
import { casePath, useCaseJobs, useFlightSet } from "../../api/queries";
import { Button } from "../../components/ui/Button";
import { Checkbox, Input } from "../../components/ui/form";
import { Badge, ErrorNote, Hint, ProgressBar, Section, Skeleton } from "../../components/ui/misc";
import { toast } from "../../components/ui/toast";
import { cn } from "../../lib/cn";
import { fmtAgo } from "../../lib/format";
import { fmtClock } from "../../lib/tz";
import { setTab, useWs, ws } from "../../state/workspace";
import { useCaseData } from "../workspace/caseContext";
import { isHelicopter } from "../map/geometry";

export function CategoryIcon({ category, className }: { category: string | null | undefined; className?: string }) {
  const heli = isHelicopter(category);
  const Icon = heli ? Helicopter : Plane;
  return (
    <span data-tip={category ?? "Unknown category"} className="inline-flex shrink-0">
      <Icon className={cn("size-3.5", heli ? "text-[#c026d3] dark:text-[#f0abfc]" : "text-src-aircraft", className)} />
    </span>
  );
}

export function FlightsSection() {
  const c = useCaseData();
  const fs = useFlightSet(c.id);
  const jobs = useCaseJobs(c.id);
  const fetchJob = jobs.find((j) => isActive(j) && j.kind.startsWith("aircraft") && j.kind.includes("flight"));
  const startJob = useStartJob();
  const [keepJets, setKeepJets] = useState(false);
  const [starting, setStarting] = useState(false);
  const [filter, setFilter] = useState("");
  const [showAll, setShowAll] = useState(false);
  const flightIdx = useWs((s) => s.flightIdx);
  const ready = !!c.date && !!c.region;
  const set = fs.data;

  const flights = useMemo(() => {
    const q = filter.trim().toLowerCase();
    const l = (set?.flights ?? []).slice().sort((a, b) => a.t_start_utc - b.t_start_utc);
    if (!q) return l;
    return l.filter((f) => [f.reg, f.icao, f.type, f.desc].some((x) => (x ?? "").toLowerCase().includes(q)));
  }, [set, filter]);

  const fetchDay = async () => {
    setStarting(true);
    try {
      await startJob(`${casePath(c.id)}/aircraft/flights/fetch`, { keep_jets: keepJets }, { success: (j) => `${j.result?.n_flights ?? 0} prop flights for ${c.date}` });
    } catch (e) {
      toast({ tone: "error", title: "Could not start the download", body: errorMessage(e) });
    } finally {
      setStarting(false);
    }
  };

  const stale = set?.status === "ready" && (set.date !== c.date || JSON.stringify(set.bbox) === "null");

  return (
    <Section
      title={
        <span className="flex items-center gap-2">
          Flights
          {set?.status === "ready" && (
            <span className="font-normal tracking-normal text-fg-faint normal-case tnum">
              {set.n_flights} flights
              {set.flights.some((f) => isHelicopter(f.category)) && ` · ${set.flights.filter((f) => isHelicopter(f.category)).length} helicopters`}
            </span>
          )}
        </span>
      }
      icon={<PlaneTakeoff />}
    >
      {fs.isLoading ? (
        <Skeleton className="h-12" />
      ) : fs.error ? (
        <ErrorNote error={fs.error} what="Flight data" />
      ) : !ready ? (
        <Hint>
          Set the{" "}
          <button className="font-medium text-accent hover:underline" onClick={() => setTab("setup")}>
            date and region
          </button>{" "}
          to fetch flights.
        </Hint>
      ) : fetchJob ? (
        <div className="rounded-lg border border-accent-line/50 bg-accent-soft px-3 py-2.5">
          <div className="flex items-center justify-between text-xs">
            <span className="font-medium text-accent">Fetching ADS-B history for {c.date}…</span>
            <span className="text-accent tnum">{Math.round(fetchJob.progress * 100)}%</span>
          </div>
          <ProgressBar className="mt-1.5" value={fetchJob.progress} indeterminate={fetchJob.status === "queued" || fetchJob.progress < 0.01} />
          <div className="mt-1.5 truncate text-2xs text-fg-muted">{fetchJob.message}</div>
          <div className="mt-1 text-2xs text-fg-faint">This streams about 4 GB and can take several minutes. You can keep working.</div>
        </div>
      ) : (
        <div className="flex flex-col gap-2.5">
          {set?.status === "ready" ? (
            <div className="flex items-center gap-2 text-xs text-fg-muted">
              <Badge tone="ok" dot>
                Ready
              </Badge>
              <span className="tnum">
                {set.date} · fetched {fmtAgo(set.fetched_at)}
              </span>
            </div>
          ) : (
            <p className="text-xs leading-relaxed text-fg-muted">
              Download the day's ADS-B tracks for the region from adsb.lol. It streams about 4 GB, takes several minutes, and is cached for later searches.
            </p>
          )}
          {stale && <div className="text-2xs text-warn">The flights were fetched for {set?.date}; the case date is now {c.date}. Fetch again.</div>}
          <div className="flex items-center gap-3">
            <Button size="sm" variant={set?.status === "ready" ? "secondary" : "primary"} icon={<CloudDownload />} loading={starting} onClick={fetchDay}>
              {set?.status === "ready" ? "Fetch again" : `Fetch flights for ${c.date}`}
            </Button>
            <Checkbox checked={keepJets} onChange={setKeepJets} label="Keep jets" />
          </div>
        </div>
      )}

      {set?.status === "ready" && set.flights.length > 0 && (
        <div className="mt-3">
          <Input prefixIcon={<Search />} placeholder="Filter by registration, ICAO or type" value={filter} onChange={(e) => setFilter(e.target.value)} className="mb-1.5" />
          <div className="-mx-1.5 flex max-h-[260px] flex-col overflow-y-auto">
            {(showAll ? flights : flights.slice(0, 40)).map((f) => (
              <button
                key={f.idx}
                onClick={() => ws.set({ flightIdx: flightIdx === f.idx ? null : f.idx })}
                className={cn(
                  "grid grid-cols-[1fr_auto] items-center gap-2 rounded-md px-1.5 py-1 text-left text-xs",
                  flightIdx === f.idx ? "bg-accent-soft ring-1 ring-accent-line ring-inset" : "hover:bg-surface-2",
                )}
              >
                <span className="flex min-w-0 items-center gap-1.5 truncate">
                  <CategoryIcon category={f.category} />
                  <span className="font-medium text-fg">{f.reg || f.icao.toUpperCase()}</span>{" "}
                  <span className="text-fg-muted">{f.type ?? "?"}</span> <span className="text-fg-faint">{f.desc ?? ""}</span>
                </span>
                <span className="text-2xs text-fg-faint tnum">
                  {fmtClock(f.t_start_utc, "UTC", false)}–{fmtClock(f.t_end_utc, "UTC", false)}
                </span>
              </button>
            ))}
          </div>
          {flights.length > 40 && !showAll && (
            <button className="mt-1 text-2xs font-medium text-fg-muted hover:text-fg" onClick={() => setShowAll(true)}>
              Show all {flights.length}
            </button>
          )}
          <div className="mt-1.5 text-[10px] text-fg-faint">{set.attribution || "Flight data: adsb.lol (ODbL)"} · times UTC</div>
        </div>
      )}
    </Section>
  );
}
