import { Clock3, Info, MapPinned, RadioTower, ScanLine } from "lucide-react";
import { useState } from "react";
import { api, errorMessage } from "../../api/client";
import { isActive, useStartJob } from "../../api/jobs";
import { casePath, setCaseData, useAlerts, useCaseJobs } from "../../api/queries";
import type { Case, SameHeader, SameLocation } from "../../api/types";
import { Button } from "../../components/ui/Button";
import { Badge, EmptyState, ErrorNote, Hint, KV, ProgressBar, Section, Skeleton } from "../../components/ui/misc";
import { toast } from "../../components/ui/toast";
import { cn } from "../../lib/cn";
import { fmtAgo, fmtPct, fmtRec } from "../../lib/format";
import { boundsOf, round6 } from "../../lib/geo";
import { fmtDateTime, fmtUtc } from "../../lib/tz";
import { focusMap, focusTime, useWs, ws } from "../../state/workspace";
import { adoptStartTime } from "../workspace/adoptStart";
import { caseTz, recordingReady, useCaseData } from "../workspace/caseContext";
import { SearchAreaBanner } from "../workspace/SearchArea";
import { useQueryClient } from "@tanstack/react-query";

export function AlertsTab() {
  const c = useCaseData();
  const alerts = useAlerts(c.id, recordingReady(c));
  const startJob = useStartJob();
  const job = useCaseJobs(c.id).find((j) => isActive(j) && j.kind.startsWith("alert"));
  const [starting, setStarting] = useState(false);
  const scan = async () => {
    setStarting(true);
    try {
      await startJob(`${casePath(c.id)}/alerts/scan`, {}, { success: (j) => `${j.result?.count ?? 0} alert headers decoded` });
    } catch (e) {
      toast({ tone: "error", title: "Could not start the scan", body: errorMessage(e) });
    } finally {
      setStarting(false);
    }
  };
  const st = alerts.data;
  return (
    <div>
      <Section title="Emergency alerts (EAS / SAME)" icon={<RadioTower />}>
        <p className="-mt-1 mb-3 text-xs leading-relaxed text-fg-muted">
          US radio and TV stations and NOAA Weather Radio send alerts with a digital header naming the event, the affected counties and the issue time. A
          decoded header gives a county-level region and a clock anchor.
        </p>
        <SearchAreaBanner className="mb-3" />
        {alerts.error ? (
          <ErrorNote error={alerts.error} what="Alert decoding" />
        ) : job ? (
          <div className="rounded-lg border border-accent-line/50 bg-accent-soft px-3 py-2.5">
            <div className="flex items-center justify-between text-xs">
              <span className="font-medium text-accent">Scanning for alert bursts…</span>
              <span className="text-accent tnum">{Math.round(job.progress * 100)}%</span>
            </div>
            <ProgressBar className="mt-1.5" value={job.progress} indeterminate={job.status === "queued"} />
            <div className="mt-1.5 truncate text-2xs text-fg-muted">{job.message}</div>
          </div>
        ) : (
          <div className="flex items-center gap-2">
            <Button size="sm" variant={st?.scanned_at ? "secondary" : "primary"} icon={<ScanLine />} loading={starting} onClick={scan} disabled={!recordingReady(c)}>
              {st?.scanned_at ? "Scan again" : "Scan the recording"}
            </Button>
            {st?.scanned_at && <span className="text-2xs text-fg-faint">scanned {fmtAgo(st.scanned_at)}</span>}
          </div>
        )}
        {st?.notes && st.notes.length > 0 && (
          <ul className="mt-3 flex flex-col gap-1">
            {st.notes.map((n, i) => (
              <li key={i} className="flex items-start gap-1.5 text-2xs leading-snug text-fg-muted">
                <Info className="mt-px size-3 shrink-0 text-info" />
                {n}
              </li>
            ))}
          </ul>
        )}
      </Section>

      {!alerts.error && (
        <Section
          title={
            <span className="flex items-center gap-2">
              Decoded headers {st && st.headers.length > 0 && <span className="font-normal tracking-normal text-fg-faint normal-case tnum">{st.headers.length}</span>}
            </span>
          }
        >
          {alerts.isLoading ? (
            <Skeleton className="h-16" />
          ) : !st || st.headers.length === 0 ? (
            <EmptyState compact icon={<RadioTower />} title={st?.scanned_at ? "No alert headers found" : "Not scanned yet"}>
              {st?.scanned_at
                ? "No SAME data bursts were decoded. They sound like short, harsh modem screeches before an alert."
                : "Scan the recording to look for the data bursts that start broadcast emergency alerts."}
            </EmptyState>
          ) : (
            <div className="flex flex-col gap-2">
              {st.headers.map((h) => (
                <HeaderCard key={h.id} h={h} />
              ))}
              {st.eom.length > 0 && (
                <div className="text-2xs text-fg-faint tnum">End-of-message bursts at {st.eom.map((e) => fmtRec(e.t, 1)).join(", ")}</div>
              )}
            </div>
          )}
        </Section>
      )}
    </div>
  );
}

function HeaderCard({ h }: { h: SameHeader }) {
  const c = useCaseData();
  const qc = useQueryClient();
  const tz = caseTz(c);
  const sel = useWs((s) => s.alertId) === h.id;
  const pts = h.locations.filter((l) => l.lat != null && l.lon != null).map((l) => ({ lat: l.lat!, lon: l.lon! }));
  const same = c.start_utc != null && Math.abs(c.start_utc - h.implied_start_utc) < 0.5;

  const useStart = async () => {
    try {
      await adoptStartTime(qc, c.id, h.implied_start_utc, "alerts");
      toast({ tone: "success", title: "Start time set from the alert header", body: `${fmtDateTime(h.implied_start_utc, tz)} (assumes the burst aired at its issue time)` });
    } catch (e) {
      toast({ tone: "error", title: "Could not set the start time", body: errorMessage(e) });
    }
  };
  const setRegion = async () => {
    const b = boundsOf(pts);
    if (!b) return;
    const pad = 0.35; // counties are large: pad the centroids by ~35 km
    const region = { lat_min: round6(b.lat_min - pad), lat_max: round6(b.lat_max + pad), lon_min: round6(b.lon_min - pad), lon_max: round6(b.lon_max + pad) };
    try {
      const updated = await api.patch<Case>(casePath(c.id), { region });
      setCaseData(qc, updated);
      focusMap({ bbox: region });
      toast({ tone: "success", title: "Region set around the alert's counties" });
    } catch (e) {
      toast({ tone: "error", title: "Could not set the region", body: errorMessage(e) });
    }
  };

  const toggle = () => {
    ws.set({ alertId: sel ? null : h.id });
    if (!sel) {
      focusTime(h.t, 90);
      const b = boundsOf(pts);
      if (b) focusMap({ bbox: { lat_min: b.lat_min - 0.3, lat_max: b.lat_max + 0.3, lon_min: b.lon_min - 0.3, lon_max: b.lon_max + 0.3 } });
    }
  };

  return (
    <div className={cn("rounded-lg border transition-colors", sel ? "border-accent-line/60 bg-accent-soft/50" : "border-line bg-surface-2/40 hover:border-line-strong")}>
      {/* the summary is the clickable part; the actions below are separate controls */}
      <div
        role="button"
        tabIndex={0}
        aria-pressed={sel}
        aria-label={`${h.event_name} at ${fmtRec(h.t, 1)}`}
        onClick={toggle}
        onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), toggle())}
        className="cursor-pointer p-2.5 text-left"
      >
        <div className="flex items-start gap-2">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1.5">
              <Badge tone="alerts">{h.event}</Badge>
              <span className="truncate text-sm font-semibold text-fg">{h.event_name}</span>
            </div>
            <div className="mt-0.5 truncate text-2xs text-fg-muted">
              {h.originator_name} · {h.station}
            </div>
          </div>
          <span className="text-xs font-semibold text-fg tnum">{fmtRec(h.t, 1)}</span>
        </div>
        <div className="mt-2">
          <KV label="Issued">{fmtUtc(h.issued_utc, false)}</KV>
          <KV label="Implied start">{fmtDateTime(h.implied_start_utc, tz)}</KV>
          <KV label="Bursts decoded">
            {h.repeats} of 3 · confidence {fmtPct(h.confidence)}
          </KV>
          <KV label="Valid for">{h.purge_minutes} min</KV>
        </div>
        {h.locations.length > 0 && (
          <div className="mt-1.5 flex flex-wrap gap-1">
            {h.locations.slice(0, 12).map((l) => (
              <span key={l.code} className="rounded border border-line bg-surface px-1.5 py-0.5 text-2xs text-fg-muted" title={`FIPS ${l.fips} · code ${l.code}`}>
                {locationLabel(l)}
              </span>
            ))}
            {h.locations.length > 12 && <span className="text-2xs text-fg-faint">+{h.locations.length - 12} more</span>}
          </div>
        )}
        <div className="mt-1.5 truncate font-mono text-[10px] text-fg-faint" title={h.raw}>
          {h.raw}
        </div>
      </div>
      {sel && (
        <div className="flex flex-col gap-2 px-2.5 pb-2.5">
          <div className="flex flex-wrap gap-1.5">
            <Button size="sm" variant={same ? "ghost" : "secondary"} icon={<Clock3 />} disabled={same} onClick={useStart}>
              {same ? "Is the case start time" : "Use as case start time"}
            </Button>
            {pts.length > 0 && (
              <Button size="sm" variant="secondary" icon={<MapPinned />} onClick={setRegion}>
                Set region around the counties
              </Button>
            )}
          </div>
          <Hint>Relayed alerts can air minutes after their issue time, so treat the implied start as approximate.</Hint>
        </div>
      )}
    </div>
  );
}

/** "Jackson County, MO", with the portion when the alert covers only part of it ("… (northwest part)"). */
export function locationLabel(l: SameLocation): string {
  const name = [l.county ?? l.fips, l.state].filter(Boolean).join(", ");
  return l.part && l.part !== "all" ? `${name} (${l.part} part)` : name;
}
