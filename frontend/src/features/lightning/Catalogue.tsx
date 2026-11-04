import { useQueryClient } from "@tanstack/react-query";
import { CloudDownload, Crosshair, FileUp, MapPin, Plus, Trash2 } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { api, errorMessage } from "../../api/client";
import { isActive, useStartJob } from "../../api/jobs";
import { casePath, qk, useCaseJobs, useLightning, useStrikes } from "../../api/queries";
import type { Case, Strike } from "../../api/types";
import { Button, IconButton } from "../../components/ui/Button";
import { Checkbox, Field, Input, NumberInput, Segmented, Select } from "../../components/ui/form";
import { Badge, Hint, KV, ProgressBar, Section } from "../../components/ui/misc";
import { confirmDialog } from "../../components/ui/overlay";
import { toast } from "../../components/ui/toast";
import { cn } from "../../lib/cn";
import { fmtAgo, fmtDist, fmtDuration, fmtInt } from "../../lib/format";
import { fmtClock, fmtDateIso, fmtUtc, fromUtcInput, toUtcInput, utcDayRange } from "../../lib/tz";
import { focusMap, useWs, ws } from "../../state/workspace";
import { caseDuration, useCaseData } from "../workspace/caseContext";
import { EventGlyph } from "./EventGlyph";
import { MANUAL_KINDS, USGS_TYPES, eventIcon } from "./kinds";

type Src = "glm" | "usgs" | "csv";

const LIMIT_S: Record<"glm" | "usgs", number> = { glm: 6 * 3600, usgs: 31 * 86400 };

/** Default UTC window for a catalogue query, around the recording or its date. */
function defaultWindow(c: Case, src: "glm" | "usgs"): [number, number] | null {
  const D = caseDuration(c);
  if (c.start_utc != null) {
    if (src === "glm") {
      const a = c.start_utc - 3600;
      return [Math.floor(a), Math.ceil(Math.min(c.start_utc + D + 3600, a + LIMIT_S.glm))];
    }
    return [Math.floor(c.start_utc - 12 * 3600), Math.ceil(c.start_utc + D + 12 * 3600)];
  }
  const r = c.date ? utcDayRange(c.date) : null;
  if (!r) return null;
  return src === "glm" ? [r[0] + 12 * 3600, r[0] + 18 * 3600] : [r[0] - 86400, r[1] + 86400];
}

export function CatalogueSection() {
  const c = useCaseData();
  const qc = useQueryClient();
  const st = useLightning(c.id);
  const set = st.data?.strikes ?? null;
  const strikes = useStrikes(c.id, !!set?.count);
  const startJob = useStartJob();
  const job = useCaseJobs(c.id).find((j) => isActive(j) && j.kind.startsWith("lightning") && j.kind.includes("strike"));
  const [src, setSrc] = useState<Src>("glm");
  const [fetching, setFetching] = useState<"glm" | "usgs" | null>(null);
  const [types, setTypes] = useState<string[]>(() => USGS_TYPES.filter((t) => t.on).map((t) => t.type));
  const win = useMemo(() => (src === "csv" ? null : defaultWindow(c, src)), [c, src]);
  const [t0, setT0] = useState("");
  const [t1, setT1] = useState("");
  useEffect(() => {
    setT0(win ? toUtcInput(win[0]) : "");
    setT1(win ? toUtcInput(win[1]) : "");
  }, [win]);
  const a = t0 ? fromUtcInput(t0) : null;
  const b = t1 ? fromUtcInput(t1) : null;
  const span = a != null && b != null ? b - a : null;
  const limit = src === "csv" ? Infinity : LIMIT_S[src];
  const invalid = span != null && (span <= 0 || span > limit);
  const file = useRef<HTMLInputElement>(null);
  const [importing, setImporting] = useState(false);

  const fetchCatalogue = async () => {
    if (a == null || b == null || src === "csv") return;
    const body = src === "glm" ? { source: "glm", satellite: "auto", t0_utc: a, t1_utc: b } : { source: "usgs", event_types: types, t0_utc: a, t1_utc: b };
    try {
      setFetching(src);
      await startJob(`${casePath(c.id)}/lightning/strikes/fetch`, body, {
        success: (j) => `${fmtInt(j.result?.count ?? 0)} ${src === "glm" ? "lightning flashes" : "USGS events"}`,
        onFinish: () => setFetching(null),
      });
    } catch (e) {
      setFetching(null);
      toast({ tone: "error", title: "Could not start the download", body: errorMessage(e) });
    }
  };
  const importCsv = async (f: File) => {
    setImporting(true);
    try {
      const fd = new FormData();
      fd.append("file", f, f.name);
      const r = await api.form<{ count: number }>(`${casePath(c.id)}/lightning/strikes/import`, fd);
      toast({ tone: "success", title: "Events imported", body: `${fmtInt(r.count)} from ${f.name}` });
      void qc.invalidateQueries({ queryKey: qk.lightning(c.id) });
    } catch (e) {
      toast({ tone: "error", title: "Import failed", body: errorMessage(e) });
    } finally {
      setImporting(false);
    }
  };
  const clear = async () => {
    const ok = await confirmDialog({
      title: "Remove all strikes and events?",
      body: "Catalogue and hand-entered events are removed. Solves keep their results.",
      confirmLabel: "Remove all",
      danger: true,
    });
    if (!ok) return;
    try {
      await api.del(`${casePath(c.id)}/lightning/strikes`);
      void qc.invalidateQueries({ queryKey: qk.lightning(c.id) });
    } catch (e) {
      toast({ tone: "error", title: "Could not remove the events", body: errorMessage(e) });
    }
  };

  const multiDay = set ? set.t1_utc - set.t0_utc > 86400 : false;

  return (
    <Section
      title={
        <span className="flex items-center gap-2">
          Strikes and events {set && <span className="font-normal tracking-normal text-fg-faint normal-case tnum">{fmtInt(set.count)}</span>}
        </span>
      }
      icon={<MapPin />}
    >
      {set ? (
        <div className="mb-3 rounded-lg border border-line bg-surface-2 px-3 py-2">
          <div className="flex items-center justify-between gap-2">
            <div className="flex min-w-0 flex-wrap items-center gap-1">
              {set.source.split("+").map((s) => (
                <Badge key={s} tone={s === "manual" ? "accent" : s.startsWith("glm") || s === "csv" ? "lightning" : "info"}>
                  {s}
                </Badge>
              ))}
            </div>
            <IconButton size="xs" label="Remove all strikes and events" onClick={clear} className="hover:!text-danger">
              <Trash2 />
            </IconButton>
          </div>
          <KV label="Window">
            {multiDay
              ? `${fmtUtc(set.t0_utc, false)} → ${fmtUtc(set.t1_utc, false)}`
              : `${fmtDateIso(set.t0_utc, "UTC")} ${fmtClock(set.t0_utc, "UTC", false)}–${fmtClock(set.t1_utc, "UTC", false)} UTC`}
          </KV>
          <KV label="Events">{fmtInt(set.count)}</KV>
          <KV label="Fetched">{fmtAgo(set.fetched_at)}</KV>
        </div>
      ) : (
        <p className="mb-3 text-xs leading-relaxed text-fg-muted">
          Sharp sounds whose time and place are on record: lightning from GOES satellites (Americas), blasts, explosions and sonic booms from USGS, a CSV
          from any network, or events you add by hand below.
        </p>
      )}

      {job ? (
        <div className="mb-3 rounded-lg border border-accent-line/50 bg-accent-soft px-3 py-2.5">
          <div className="flex items-center justify-between text-xs">
            <span className="font-medium text-accent">{fetching === "usgs" ? "Querying USGS ComCat…" : fetching === "glm" ? "Fetching GOES GLM flashes…" : "Fetching events…"}</span>
            <span className="text-accent tnum">{Math.round(job.progress * 100)}%</span>
          </div>
          <ProgressBar className="mt-1.5" value={job.progress} indeterminate={job.status === "queued" || job.progress < 0.01} />
          <div className="mt-1.5 truncate text-2xs text-fg-muted">{job.message}</div>
        </div>
      ) : (
        <div className="mb-3 flex flex-col gap-2.5">
          <Segmented<Src>
            className="flex w-full"
            value={src}
            onChange={setSrc}
            ariaLabel="Catalogue source"
            options={[
              { value: "glm", label: "GOES GLM lightning", tip: "Lightning flashes seen by GOES satellites (Americas only); at most 6 h" },
              { value: "usgs", label: "USGS events", tip: "Quarry blasts, explosions, sonic booms, earthquakes (mostly US); at most 31 days" },
              { value: "csv", label: "Import CSV", tip: "Any catalogue as CSV" },
            ]}
          />
          {src === "csv" ? (
            <>
              <Hint>
                Columns <code className="font-mono text-2xs">time</code> (ISO-8601 or unix s), <code className="font-mono text-2xs">lat</code>,{" "}
                <code className="font-mono text-2xs">lon</code>; optional <code className="font-mono text-2xs">sigma_m</code>, <code className="font-mono text-2xs">energy</code>,{" "}
                <code className="font-mono text-2xs">kind</code>. A USGS ComCat CSV export works too. Replaces earlier catalogue events; keeps the ones you added.
              </Hint>
              <Button size="sm" variant="secondary" icon={<FileUp />} loading={importing} className="self-start" onClick={() => file.current?.click()}>
                Choose CSV…
              </Button>
              <input
                ref={file}
                type="file"
                accept=".csv,text/csv"
                className="hidden"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) void importCsv(f);
                  e.target.value = "";
                }}
              />
            </>
          ) : (
            <>
              <div className="grid grid-cols-[44px_1fr] items-center gap-x-2 gap-y-1.5">
                <span className="text-xs text-fg-muted">From</span>
                <Input type="datetime-local" step={60} value={t0} onChange={(e) => setT0(e.target.value)} aria-label="Catalogue window from (UTC)" suffix="UTC" />
                <span className="text-xs text-fg-muted">To</span>
                <Input type="datetime-local" step={60} value={t1} onChange={(e) => setT1(e.target.value)} aria-label="Catalogue window to (UTC)" suffix="UTC" />
              </div>
              {src === "usgs" && (
                <div className="grid grid-cols-2 gap-x-3 gap-y-1.5 rounded-md border border-line bg-surface-2/60 px-2.5 py-2">
                  {USGS_TYPES.map((t) => (
                    <Checkbox
                      key={t.type}
                      checked={types.includes(t.type)}
                      onChange={(v) => setTypes((cur) => (v ? [...cur, t.type] : cur.filter((x) => x !== t.type)))}
                      label={
                        <span className="flex items-center gap-1.5 capitalize">
                          <EventGlyph kind={t.type} className="size-3" />
                          {t.type}
                        </span>
                      }
                    />
                  ))}
                </div>
              )}
              <div className={cn("text-2xs", invalid ? "text-danger" : "text-fg-faint")}>
                {span == null
                  ? c.start_utc == null && !c.date
                    ? "Set the date or start time in Setup to prefill the window."
                    : ""
                  : invalid
                    ? span <= 0
                      ? "The end must be after the start."
                      : `At most ${src === "glm" ? "6 hours" : "31 days"} (this is ${fmtDuration(span)}).`
                    : src === "glm"
                      ? `${fmtDuration(span)} window · GOES GLM covers the Americas only`
                      : `${fmtDuration(span)} window · USGS ComCat, mostly US events`}
              </div>
              <Button
                size="sm"
                variant={set ? "secondary" : "primary"}
                icon={<CloudDownload />}
                className="self-start"
                disabled={a == null || b == null || invalid || (src === "usgs" && types.length === 0)}
                onClick={fetchCatalogue}
              >
                {src === "glm" ? "Fetch GOES GLM" : "Fetch USGS events"}
              </Button>
            </>
          )}
        </div>
      )}

      {strikes.data && strikes.data.strikes.length > 0 && <StrikeList strikes={strikes.data.strikes} />}
    </Section>
  );
}

function StrikeList({ strikes }: { strikes: Strike[] }) {
  const c = useCaseData();
  const qc = useQueryClient();
  const [all, setAll] = useState(false);
  const sorted = useMemo(() => strikes.slice().sort((x, y) => x.t_utc - y.t_utc), [strikes]);
  const t0 = sorted[0]?.t_utc ?? 0;
  const multiDay = (sorted[sorted.length - 1]?.t_utc ?? 0) - t0 > 20 * 3600;
  const shown = all ? sorted : sorted.slice(0, 8);
  const remove = async (s: Strike) => {
    try {
      await api.del(`${casePath(c.id)}/lightning/strikes/${encodeURIComponent(s.id)}`);
      void qc.invalidateQueries({ queryKey: qk.lightning(c.id) });
    } catch (e) {
      toast({ tone: "error", title: "Could not delete the event", body: errorMessage(e) });
    }
  };
  return (
    <div className="-mx-1.5">
      <div className="px-1.5 pb-1 text-[10px] font-semibold tracking-wider text-fg-faint uppercase">Events (UTC)</div>
      {shown.map((s) => {
        const deletable = s.source === "manual" || s.source.startsWith("usgs");
        const lightning = eventIcon(s.kind) === "lightning";
        return (
          <div
            key={s.id}
            role="button"
            tabIndex={0}
            onClick={() => focusMap({ center: { lat: s.lat, lon: s.lon }, zoom: 12 })}
            onKeyDown={(e) => e.key === "Enter" && focusMap({ center: { lat: s.lat, lon: s.lon }, zoom: 12 })}
            className="group grid cursor-pointer grid-cols-[18px_1fr_auto] items-center gap-2 rounded-md px-1.5 py-1 hover:bg-surface-2"
            data-tip="Show on the map"
          >
            <EventGlyph kind={s.kind} />
            <div className="min-w-0">
              <div className="flex items-baseline gap-1.5 text-xs tnum">
                <span className="font-medium text-fg">
                  {multiDay ? `${fmtDateIso(s.t_utc, "UTC").slice(5)} ` : ""}
                  {fmtClock(s.t_utc, "UTC")}
                </span>
                <span className={cn("truncate", lightning ? "text-fg-muted" : "text-fg capitalize")}>{lightning ? `lightning${s.kind ? ` (${s.kind})` : ""}` : s.kind}</span>
              </div>
              <div className="truncate text-2xs text-fg-faint tnum">
                {s.source}
                {s.source.startsWith("usgs") && s.energy != null ? ` · M ${s.energy.toFixed(1)}` : ""}
                {s.depth_m != null ? ` · depth ${fmtDist(s.depth_m)}` : ""} · ±{fmtDist(s.sigma_m)}
                {s.ref ? ` · ${s.ref}` : ""}
              </div>
            </div>
            {deletable ? (
              <IconButton
                size="xs"
                label="Delete this event"
                className="opacity-50 group-hover:opacity-100 hover:!text-danger"
                onClick={(e) => {
                  e.stopPropagation();
                  void remove(s);
                }}
              >
                <Trash2 />
              </IconButton>
            ) : (
              <span />
            )}
          </div>
        );
      })}
      {sorted.length > 8 && (
        <button className="mx-1.5 mt-1 text-2xs font-medium text-fg-muted hover:text-fg" onClick={() => setAll((v) => !v)}>
          {all ? "Show fewer" : `Show all ${fmtInt(sorted.length)}`}
        </button>
      )}
    </div>
  );
}

export function KnownEventSection() {
  const c = useCaseData();
  const qc = useQueryClient();
  const mapTool = useWs((s) => s.mapTool);
  const pick = useWs((s) => s.eventPick);
  const [time, setTime] = useState("");
  const [lat, setLat] = useState<number | null>(null);
  const [lon, setLon] = useState<number | null>(null);
  const [sigma, setSigma] = useState(250);
  const [kind, setKind] = useState<string>("event");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (pick) {
      setLat(Math.round(pick.lat * 1e6) / 1e6);
      setLon(Math.round(pick.lon * 1e6) / 1e6);
    }
  }, [pick]);
  const tUtc = time ? fromUtcInput(time) : null;
  const ok = tUtc != null && lat != null && lon != null && Math.abs(lat) <= 90 && Math.abs(lon) <= 540;

  const add = async () => {
    if (!ok) return;
    setBusy(true);
    try {
      const s = await api.post<Strike>(`${casePath(c.id)}/lightning/strikes`, { t_utc: tUtc, lat, lon, sigma_m: sigma, kind });
      void qc.invalidateQueries({ queryKey: qk.lightning(c.id) });
      ws.set({ eventPick: null });
      toast({ tone: "success", title: "Event added", body: `${s.kind ?? kind} at ${fmtClock(s.t_utc, "UTC")} UTC` });
    } catch (e) {
      toast({ tone: "error", title: "Could not add the event", body: errorMessage(e) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Section title="Add a known event" icon={<Plus />}>
      <p className="-mt-1 mb-3 text-xs leading-relaxed text-fg-muted">
        A reported explosion, fireworks or blast whose time and place you know. It is matched against the onsets like a catalogued strike.
      </p>
      <div className="flex flex-col gap-2.5">
        <Field label="Time (UTC)">
          <Input type="datetime-local" step={1} value={time} onChange={(e) => setTime(e.target.value)} suffix="UTC" aria-label="Event time (UTC)" />
        </Field>
        <div className="flex flex-col gap-1.5">
          <span className="text-xs font-medium text-fg-muted">Location</span>
          <div className="grid grid-cols-[1fr_1fr_auto] gap-2">
            <NumberInput value={lat} onCommit={setLat} min={-90} max={90} step={0.001} digits={6} placeholder="Latitude" ariaLabel="Event latitude" />
            <NumberInput value={lon} onCommit={setLon} min={-540} max={540} step={0.001} digits={6} placeholder="Longitude" ariaLabel="Event longitude" />
            <Button
              size="md"
              variant="secondary"
              active={mapTool === "pick-event"}
              icon={<Crosshair />}
              onClick={() => ws.set({ mapTool: mapTool === "pick-event" ? "none" : "pick-event" })}
              tip="Click the map where the event happened"
            >
              {mapTool === "pick-event" ? "Click map…" : "Pick"}
            </Button>
          </div>
        </div>
        <div className="grid grid-cols-2 gap-2">
          <Field label="Uncertainty">
            <NumberInput value={sigma} onCommit={(v) => v != null && setSigma(v)} min={1} max={100000} step={50} suffix="m" allowEmpty={false} />
          </Field>
          <Field label="Kind">
            <Select value={kind} onChange={(e) => setKind(e.target.value)} className="capitalize">
              {MANUAL_KINDS.map((k) => (
                <option key={k} value={k}>
                  {k[0].toUpperCase() + k.slice(1)}
                </option>
              ))}
            </Select>
          </Field>
        </div>
        <Button variant="secondary" icon={<Plus />} disabled={!ok} loading={busy} onClick={add} className="self-start">
          Add event
        </Button>
      </div>
    </Section>
  );
}
