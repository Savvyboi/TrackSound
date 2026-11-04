import { Activity, ArrowLeftRight, Clock3, Download, LocateFixed, Plane, RadioTower, ShieldAlert, Zap } from "lucide-react";
import { useState, type ReactNode } from "react";
import { api, errorMessage } from "../../api/client";
import { useQueryClient } from "@tanstack/react-query";
import { useAlerts, useEnf, useEnfMatch, useEnfReferences, useLightning, useSearches, useSolve } from "../../api/queries";
import type { StartSource } from "../../api/types";
import { SourceBadge } from "../../components/SourceBadge";
import { Button, IconButton } from "../../components/ui/Button";
import { ErrorNote, Hint, KV, Section } from "../../components/ui/misc";
import { toast } from "../../components/ui/toast";
import { cn } from "../../lib/cn";
import { fmtDist, fmtDuration, fmtLatLonPretty, fmtPct, fmtSigned } from "../../lib/format";
import { circleRing, haversine } from "../../lib/geo";
import { fmtDateTime, fmtUtc } from "../../lib/tz";
import { focusMap, setTab, ws } from "../../state/workspace";
import { adoptStartTime } from "../workspace/adoptStart";
import { caseTz, useCaseData } from "../workspace/caseContext";

type Estimate = {
  source: Exclude<StartSource, "user">;
  label: string;
  icon: ReactNode;
  color: string;
  loc: { lat: number; lon: number; radius_m: number | null } | null;
  start_utc: number | null;
  quality: ReactNode;
  where?: string;
  open: () => void;
};

export function SummaryTab() {
  const c = useCaseData();
  const tz = caseTz(c);
  const searches = useSearches(c.id);
  const lightning = useLightning(c.id);
  const enf = useEnf(c.id);
  const refs = useEnfReferences();
  const alerts = useAlerts(c.id);
  const qc = useQueryClient();

  const okSearches = (searches.data ?? []).filter((s) => s.status === "done" && s.best);
  const aSearch = okSearches.find((s) => s.mode === "refine") ?? okSearches[0];
  const solveSummary = (lightning.data?.solves ?? []).find((s) => s.best);
  const solve = useSolve(c.id, solveSummary?.id ?? null);
  const matchSummary = (enf.data?.matches ?? []).find((m) => m.best);
  const match = useEnfMatch(c.id, matchSummary?.id ?? null);

  const ests: Estimate[] = [];
  if (aSearch?.best) {
    const b = aSearch.best;
    const spacing = Number((aSearch.request as { spacing_m?: number }).spacing_m) || (aSearch.mode === "refine" ? 50 : 200);
    ests.push({
      source: "aircraft",
      label: "Aircraft",
      icon: <Plane />,
      color: "var(--src-aircraft)",
      loc: { lat: b.lat, lon: b.lon, radius_m: null },
      start_utc: b.start_utc,
      quality: (
        <>
          {aSearch.mode === "refine" ? "refined" : aSearch.mode} search · {spacing} m grid · margin {aSearch.margin == null ? "n/a" : fmtPct(aSearch.margin)}
        </>
      ),
      open: () => {
        setTab("aircraft");
        ws.set({ searchId: aSearch.id, candidateRank: 1 });
      },
    });
  }
  const sb = solve.data?.best ?? solveSummary?.best;
  if (sb) {
    ests.push({
      source: "lightning",
      label: "Lightning",
      icon: <Zap />,
      color: "var(--src-lightning)",
      loc: { lat: sb.lat, lon: sb.lon, radius_m: sb.uncertainty_m },
      start_utc: sb.start_utc,
      quality: (
        <>
          {sb.n_matched} matched onsets · RMS {sb.rms_s.toFixed(2)} s
        </>
      ),
      open: () => {
        setTab("lightning");
        ws.set({ solveId: solveSummary!.id });
      },
    });
  }
  const mb = match.data?.best ?? matchSummary?.best;
  if (mb && matchSummary) {
    const ref = refs.data?.find((r) => r.id === matchSummary.reference_id);
    ests.push({
      source: "enf",
      label: "Mains hum",
      icon: <Activity />,
      color: "var(--src-enf)",
      loc: null,
      where: ref ? `On the ${ref.grid} grid (${ref.nominal_hz} Hz)` : undefined,
      start_utc: mb.start_utc,
      quality: (
        <>
          r {mb.corr.toFixed(2)} · z {mb.z.toFixed(1)}
        </>
      ),
      open: () => {
        setTab("enf");
        ws.set({ matchId: matchSummary.id });
      },
    });
  }

  const header = (alerts.data?.headers ?? []).slice().sort((a, b) => b.confidence - a.confidence)[0];
  if (header) {
    const pts = header.locations.filter((l) => l.lat != null && l.lon != null);
    const lat = pts.length ? pts.reduce((a, l) => a + l.lat!, 0) / pts.length : null;
    const lon = pts.length ? pts.reduce((a, l) => a + l.lon!, 0) / pts.length : null;
    const names = header.locations
      .slice(0, 3)
      .map((l) => [l.county ?? l.fips, l.state].filter(Boolean).join(", "))
      .join("; ");
    ests.push({
      source: "alerts",
      label: "Emergency alert",
      icon: <RadioTower />,
      color: "var(--src-alerts)",
      loc: lat != null && lon != null ? { lat, lon, radius_m: 30000 } : null,
      where: names ? `County level: ${names}` : undefined,
      start_utc: header.implied_start_utc,
      quality: (
        <>
          {header.event_name} · {header.repeats}/3 bursts · confidence {Math.round(header.confidence * 100)}%
        </>
      ),
      open: () => {
        setTab("alerts");
        ws.set({ alertId: header.id });
      },
    });
  }

  const zoomTo = (e: Estimate) => {
    if (!e.loc) return;
    const r = Math.max(400, (e.loc.radius_m ?? 600) * 1.8);
    const ring = circleRing(e.loc.lat, e.loc.lon, r, 24);
    const lats = ring.map((p) => p[1]);
    const lons = ring.map((p) => p[0]);
    focusMap({ bbox: { lat_min: Math.min(...lats), lat_max: Math.max(...lats), lon_min: Math.min(...lons), lon_max: Math.max(...lons) } });
  };

  const pairs: { a: Estimate; b: Estimate }[] = [];
  for (let i = 0; i < ests.length; i++) for (let j = i + 1; j < ests.length; j++) pairs.push({ a: ests[i], b: ests[j] });

  return (
    <div>
      <Section title="Case clock" icon={<Clock3 />}>
        {c.start_utc != null ? (
          <div>
            <div className="text-base font-semibold text-fg tnum">{fmtDateTime(c.start_utc, tz)}</div>
            <div className="mt-1 flex items-center gap-2 text-2xs text-fg-muted">
              <SourceBadge source={c.start_utc_source} />
              <span className="tnum">{fmtUtc(c.start_utc)}</span>
            </div>
          </div>
        ) : (
          <Hint>The start time is unknown. Adopt an estimate below, or enter it in Setup.</Hint>
        )}
      </Section>

      <Section title="Estimates by source" icon={<LocateFixed />}>
        {searches.error && lightning.error && enf.error ? (
          <ErrorNote error={searches.error} />
        ) : ests.length === 0 ? (
          <Hint>No results yet. Run an aircraft search, a lightning solve or a mains-hum match; their best answers are compared here.</Hint>
        ) : (
          <div className="flex flex-col gap-2">
            {ests.map((e) => (
              <div key={e.source} className="rounded-lg border border-line bg-surface-2/50 p-2.5">
                <div className="flex items-center gap-2">
                  <span className="flex size-6 items-center justify-center rounded-md border border-line bg-surface [&_svg]:size-3.5" style={{ color: e.color }}>
                    {e.icon}
                  </span>
                  <button className="flex-1 text-left text-sm font-semibold text-fg hover:underline" onClick={e.open}>
                    {e.label}
                  </button>
                  {e.loc && (
                    <IconButton size="xs" label="Zoom the map to this estimate" onClick={() => zoomTo(e)}>
                      <LocateFixed />
                    </IconButton>
                  )}
                </div>
                <div className="mt-2">
                  <KV label="Location">
                    {e.loc && e.source !== "alerts" ? (
                      <>
                        {fmtLatLonPretty(e.loc.lat, e.loc.lon, 4)}
                        {e.loc.radius_m != null && <span className="text-fg-muted"> ± {fmtDist(e.loc.radius_m)}</span>}
                      </>
                    ) : (
                      <span className="text-fg-muted">{e.where ?? "no location"}</span>
                    )}
                  </KV>
                  <KV label="Start time">{e.start_utc != null ? fmtDateTime(e.start_utc, tz) : "–"}</KV>
                  <KV label="Evidence">
                    <span className="text-fg-muted">{e.quality}</span>
                  </KV>
                </div>
                {e.start_utc != null && !(c.start_utc != null && c.start_utc_source === e.source && Math.abs(c.start_utc - e.start_utc) < 0.5) && (
                  <button
                    className="mt-1.5 text-2xs font-medium text-accent hover:underline"
                    onClick={() =>
                      adoptStartTime(qc, c.id, e.start_utc!, e.source).catch((err) =>
                        toast({ tone: "error", title: "Could not set the start time", body: errorMessage(err) }),
                      )
                    }
                  >
                    Use as case start time
                  </button>
                )}
              </div>
            ))}
          </div>
        )}
      </Section>

      {pairs.length > 0 && (
        <Section title="Agreement" icon={<ArrowLeftRight />}>
          <div className="flex flex-col gap-1.5">
            {pairs.map(({ a, b }) => {
              const dist = a.loc && b.loc ? haversine(a.loc.lat, a.loc.lon, b.loc.lat, b.loc.lon) : null;
              const dt = a.start_utc != null && b.start_utc != null ? b.start_utc - a.start_utc : null;
              const tol = (a.loc?.radius_m ?? 0) + (b.loc?.radius_m ?? 0) + 500;
              const agree = (dist == null || dist <= tol) && (dt == null || Math.abs(dt) <= 30);
              return (
                <div key={`${a.source}-${b.source}`} className="flex items-center gap-2 rounded-md border border-line px-2.5 py-2 text-xs">
                  <span className="font-medium text-fg">
                    {a.label} ↔ {b.label}
                  </span>
                  <span className="flex-1" />
                  <span className="text-right text-fg-muted tnum">
                    {dist != null && <span className="block">{fmtDist(dist)} apart</span>}
                    {dt != null && <span className="block">start {fmtSigned(dt, 1)}{Math.abs(dt) >= 120 ? ` (${fmtDuration(Math.abs(dt))})` : ""}</span>}
                  </span>
                  <span className={cn("size-2 rounded-full", agree ? "bg-ok" : "bg-warn")} title={agree ? "Consistent" : "Disagree: check both"} />
                </div>
              );
            })}
          </div>
          <Hint className="mt-2">Independent sources that agree make an answer much stronger. When they disagree, check margins, matched events and notes before trusting either.</Hint>
        </Section>
      )}

      <Section title="Export" icon={<Download />}>
        <ExportButton />
        <div className="mt-3 flex items-start gap-2 rounded-lg border border-line bg-surface-2/50 px-3 py-2.5 text-2xs leading-relaxed text-fg-muted">
          <ShieldAlert className="mt-px size-3.5 shrink-0 text-warn" />
          <span>
            These are estimates, not proof. A location can identify someone's home: report the uncertainty and runner-ups with any result, and don't locate people without their consent.
          </span>
        </div>
      </Section>
    </div>
  );
}

function ExportButton() {
  const c = useCaseData();
  const [busy, setBusy] = useState(false);
  const run = async () => {
    setBusy(true);
    try {
      const report = await api.get<unknown>(`/api/cases/${encodeURIComponent(c.id)}/report`);
      const blob = new Blob([JSON.stringify(report, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      const slug = c.name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || c.id;
      a.href = url;
      a.download = `tracksound-${slug}.json`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 2000);
    } catch (e) {
      toast({ tone: "error", title: "Could not export the report", body: errorMessage(e) });
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="flex items-center gap-3">
      <Button variant="primary" icon={<Download />} loading={busy} onClick={run}>
        Export report (JSON)
      </Button>
      <span className="text-2xs text-fg-faint">The case, each source's state and best results.</span>
    </div>
  );
}
