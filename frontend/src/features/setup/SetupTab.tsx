import { useQueryClient } from "@tanstack/react-query";
import {
  CalendarDays,
  Clock3,
  CloudSun,
  FileAudio,
  Globe2,
  MapPinned,
  RefreshCw,
  ScanLine,
  SquareDashed,
  Thermometer,
  TriangleAlert,
  Upload,
  X,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { errorMessage } from "../../api/client";
import { usePatchCase, useWeather } from "../../api/queries";
import type { BBox, Case, CasePatch, Weather } from "../../api/types";
import { SourceBadge } from "../../components/SourceBadge";
import { Button } from "../../components/ui/Button";
import { ComboBox, Field, Input, NumberInput, Textarea } from "../../components/ui/form";
import { Badge, Hint, KV, Section } from "../../components/ui/misc";
import { confirmDialog } from "../../components/ui/overlay";
import { toast } from "../../components/ui/toast";
import { cn } from "../../lib/cn";
import { fmtBytes, fmtDuration } from "../../lib/format";
import { bboxSizeKm, round6, validBBox } from "../../lib/geo";
import { speedOfSound } from "../../lib/physics";
import { allTimeZones, browserTimeZone, fmtClock, fmtDateIso, fromLocalInput, toLocalInput, tzAbbr, tzOffsetS } from "../../lib/tz";
import { focusMap, useWs, ws } from "../../state/workspace";
import { MEDIA_ACCEPT, startUpload } from "../home/uploads";
import { getMapBounds } from "../map/registry";
import { caseTz, useCaseData } from "../workspace/caseContext";

export function SetupTab() {
  const c = useCaseData();
  const patch = usePatchCase(c.id);
  const save = (p: CasePatch, what = "the case") =>
    patch.mutate(p, { onError: (e) => toast({ tone: "error", title: `Could not save ${what}`, body: errorMessage(e) }) });

  return (
    <div>
      <Checklist c={c} />
      <Section title="Date and time zone" icon={<CalendarDays />}>
        <DateField c={c} save={save} />
        <TimezoneField c={c} save={save} />
      </Section>
      <Section title="Search region" icon={<MapPinned />}>
        <RegionField c={c} save={save} />
      </Section>
      <Section title="Recording start time" icon={<Clock3 />}>
        <StartField c={c} save={save} />
      </Section>
      <Section title="Air temperature" icon={<Thermometer />}>
        <TemperatureField c={c} save={save} />
      </Section>
      <Section title="Notes">
        <NotesField c={c} save={save} />
      </Section>
      <Section title="Recording" icon={<FileAudio />}>
        <RecordingInfo c={c} />
      </Section>
    </div>
  );
}

function Checklist({ c }: { c: Case }) {
  const items = [
    { ok: c.recording?.status === "ready", label: "Recording processed" },
    { ok: !!c.date, label: "Date set (needed for flight data)" },
    { ok: !!c.region, label: "Region set (where to search)" },
  ];
  const done = items.filter((i) => i.ok).length;
  if (done === items.length) return null;
  return (
    <div className="mx-4 mt-4 rounded-lg border border-line bg-surface-2 px-3 py-2.5">
      <div className="flex items-center justify-between text-xs">
        <span className="font-medium text-fg">Get started</span>
        <span className="text-fg-faint tnum">
          {done} of {items.length}
        </span>
      </div>
      <ul className="mt-2 flex flex-col gap-1.5">
        {items.map((i) => (
          <li key={i.label} className="flex items-center gap-2 text-xs">
            <span className={cn("flex size-4 items-center justify-center rounded-full border text-[9px]", i.ok ? "border-ok bg-ok/15 text-ok" : "border-line-strong text-transparent")}>✓</span>
            <span className={i.ok ? "text-fg-muted line-through decoration-fg-faint/60" : "text-fg"}>{i.label}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

type SaveFn = (p: CasePatch, what?: string) => void;

function DateField({ c, save }: { c: Case; save: SaveFn }) {
  const startDay = c.start_utc != null ? fmtDateIso(c.start_utc, "UTC") : null;
  return (
    <Field
      label="Recording date (UTC day)"
      htmlFor="case-date"
      className="mb-3.5"
      hint="The UTC calendar day the recording was made. Flight data is fetched for this day. Near midnight, the local date can differ from the UTC date."
    >
      <Input
        id="case-date"
        type="date"
        value={c.date ?? ""}
        onChange={(e) => {
          const v = e.target.value;
          if (v === "" || /^\d{4}-\d{2}-\d{2}$/.test(v)) save({ date: v || null }, "the date");
        }}
      />
      {startDay && c.date && startDay !== c.date && (
        <div className="flex items-center gap-2 rounded-md border border-warn/25 bg-warn/8 px-2.5 py-1.5 text-2xs text-warn">
          <TriangleAlert className="size-3.5 shrink-0" />
          <span className="flex-1">The start time falls on {startDay} (UTC).</span>
          <button className="font-semibold hover:underline" onClick={() => save({ date: startDay }, "the date")}>
            Use {startDay}
          </button>
        </div>
      )}
      {startDay && !c.date && (
        <button className="self-start text-2xs font-medium text-accent hover:underline" onClick={() => save({ date: startDay }, "the date")}>
          Use the start time's day ({startDay})
        </button>
      )}
    </Field>
  );
}

function fmtOffset(s: number) {
  const sign = s < 0 ? "−" : "+";
  const a = Math.abs(s);
  return `UTC${sign}${String(Math.floor(a / 3600)).padStart(2, "0")}:${String(Math.floor((a % 3600) / 60)).padStart(2, "0")}`;
}

function TimezoneField({ c, save }: { c: Case; save: SaveFn }) {
  const zones = allTimeZones();
  const browser = browserTimeZone();
  const at = c.start_utc ?? Date.now() / 1000;
  return (
    <Field
      label="Time zone"
      hint={c.timezone ? "Used to show local clock times. Results are stored in UTC." : `Not set: showing times in your browser's zone (${browser}).`}
      aside={<span className="text-2xs text-fg-faint tnum">{fmtOffset(tzOffsetS(at, c.timezone || browser))}</span>}
    >
      <ComboBox
        value={c.timezone}
        options={zones}
        placeholder={`${browser} (browser)`}
        onChange={(v) => save({ timezone: v }, "the time zone")}
        renderOption={(z) => z.replace(/_/g, " ")}
      />
      {!c.timezone && (
        <button className="self-start text-2xs font-medium text-accent hover:underline" onClick={() => save({ timezone: browser }, "the time zone")}>
          Use {browser}
        </button>
      )}
    </Field>
  );
}

function RegionField({ c, save }: { c: Case; save: SaveFn }) {
  const mapTool = useWs((s) => s.mapTool);
  const r = c.region;
  const [draft, setDraft] = useState<Partial<BBox>>(r ?? {});
  useEffect(() => setDraft(c.region ?? {}), [c.region]);
  const set = (k: keyof BBox, v: number | null) => {
    const next = { ...draft, [k]: v ?? undefined };
    setDraft(next);
    if (validBBox(next)) save({ region: { lat_min: round6(next.lat_min), lat_max: round6(next.lat_max), lon_min: round6(next.lon_min), lon_max: round6(next.lon_max) } }, "the region");
  };
  const complete = ["lat_min", "lat_max", "lon_min", "lon_max"].every((k) => typeof draft[k as keyof BBox] === "number");
  const invalid = complete && !validBBox(draft);
  const size = r ? bboxSizeKm(r) : null;
  const big = size && size.w * size.h > 150 * 150;
  return (
    <div className="flex flex-col gap-3">
      <p className="-mt-1 text-xs leading-relaxed text-fg-muted">Where the recording could have been made. Searches score every point inside it, so a tighter box is faster.</p>
      <div className="grid grid-cols-2 gap-2">
        <Field label="North (lat max)">
          <NumberInput value={draft.lat_max} onCommit={(v) => set("lat_max", v)} min={-90} max={90} step={0.01} digits={6} placeholder="41.10" />
        </Field>
        <Field label="South (lat min)">
          <NumberInput value={draft.lat_min} onCommit={(v) => set("lat_min", v)} min={-90} max={90} step={0.01} digits={6} placeholder="40.65" />
        </Field>
        <Field label="West (lon min)">
          <NumberInput value={draft.lon_min} onCommit={(v) => set("lon_min", v)} min={-540} max={540} step={0.01} digits={6} placeholder="-73.90" />
        </Field>
        <Field label="East (lon max)">
          <NumberInput value={draft.lon_max} onCommit={(v) => set("lon_max", v)} min={-540} max={540} step={0.01} digits={6} placeholder="-73.20" />
        </Field>
      </div>
      {invalid && <div className="text-2xs text-danger">South must be below north, and west below east.</div>}
      {size && (
        <div className={cn("flex items-center gap-2 text-2xs", big ? "text-warn" : "text-fg-muted")}>
          {big && <TriangleAlert className="size-3.5" />}
          <span className="tnum">
            {size.w.toFixed(1)} × {size.h.toFixed(1)} km ({Math.round(size.w * size.h).toLocaleString()} km²)
          </span>
          {big && <span>· large regions make blind searches slow</span>}
        </div>
      )}
      <div className="flex flex-wrap gap-1.5">
        <Button
          size="sm"
          variant="secondary"
          active={mapTool === "region"}
          icon={<SquareDashed />}
          onClick={() => ws.set({ mapTool: mapTool === "region" ? "none" : "region" })}
        >
          {mapTool === "region" ? "Drawing… (Esc)" : "Draw on map"}
        </Button>
        <Button
          size="sm"
          variant="secondary"
          icon={<ScanLine />}
          onClick={() => {
            const b = getMapBounds();
            if (b && validBBox(b)) save({ region: b }, "the region");
          }}
          tip="Set the region to the area the map is showing"
        >
          Use map view
        </Button>
        {r && (
          <>
            <Button size="sm" variant="ghost" icon={<Globe2 />} onClick={() => focusMap({ bbox: r })}>
              Zoom to
            </Button>
            <Button size="sm" variant="ghost" icon={<X />} onClick={() => save({ region: null }, "the region")}>
              Clear
            </Button>
          </>
        )}
      </div>
    </div>
  );
}

function StartField({ c, save }: { c: Case; save: SaveFn }) {
  const tz = caseTz(c);
  const [text, setText] = useState(c.start_utc != null ? toLocalInput(c.start_utc, tz) : "");
  const [editing, setEditing] = useState(false);
  useEffect(() => {
    if (!editing) setText(c.start_utc != null ? toLocalInput(c.start_utc, tz) : "");
  }, [c.start_utc, tz, editing]);
  const commit = () => {
    setEditing(false);
    if (!text) return;
    const t = fromLocalInput(text, tz);
    if (t == null) return;
    if (c.start_utc != null && Math.abs(t - c.start_utc) < 0.5) return;
    save({ start_utc: t, start_utc_source: "user" }, "the start time");
  };
  const unknown = c.start_utc == null;
  return (
    <div className="flex flex-col gap-2.5">
      <p className="-mt-1 text-xs leading-relaxed text-fg-muted">
        When the recording started, if you know it. Otherwise leave it unknown: aircraft, lightning and mains-hum matches can estimate it, and you can adopt their estimate.
      </p>
      <div className="flex items-center gap-2">
        <Input
          type="datetime-local"
          step={1}
          value={text}
          onFocus={() => setEditing(true)}
          onChange={(e) => setText(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
          className={cn("flex-1", unknown && !text && "text-fg-faint")}
          aria-label={`Start time in ${tz}`}
        />
        <span className="w-10 shrink-0 text-2xs text-fg-faint">{tzAbbr(c.start_utc ?? Date.now() / 1000, tz)}</span>
      </div>
      {unknown ? (
        <div className="flex items-center gap-2 text-2xs text-fg-faint">
          <Badge>Unknown</Badge> Type a date and time above to set it.
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-2 text-2xs text-fg-muted">
          <SourceBadge source={c.start_utc_source} />
          <span className="tnum">{fmtClock(c.start_utc!, "UTC")} UTC</span>
          <span className="flex-1" />
          <button className="font-medium text-fg-muted hover:text-danger" onClick={() => save({ start_utc: null }, "the start time")}>
            Mark as unknown
          </button>
        </div>
      )}
    </div>
  );
}

function TemperatureField({ c, save }: { c: Case; save: SaveFn }) {
  const [lookup, setLookup] = useState(false);
  const weather = useWeather(c, lookup);
  const cNow = speedOfSound(c.temperature_c);
  const canLookup = !!c.date && !!c.region;
  return (
    <div className="flex flex-col gap-2.5">
      <div className="flex items-end gap-2">
        <Field label="Temperature" className="flex-1" hint={`Sets the speed of sound: ${cNow.toFixed(1)} m/s. Leave blank for 15 °C.`}>
          <NumberInput value={c.temperature_c} onCommit={(v) => save({ temperature_c: v }, "the temperature")} placeholder="15" suffix="°C" min={-60} max={60} step={0.5} />
        </Field>
        <Button
          size="md"
          variant="secondary"
          icon={weather.isFetching ? undefined : <CloudSun />}
          loading={weather.isFetching}
          disabled={!canLookup}
          className="mb-[22px]"
          tip={canLookup ? "Look up the air temperature for the case date and region (Open-Meteo archive)" : "Set the date and region first"}
          onClick={() => {
            if (lookup) void weather.refetch();
            else setLookup(true);
          }}
        >
          Look up
        </Button>
      </div>
      {lookup && weather.error && (
        <div className="flex items-start gap-2 text-2xs text-fg-muted">
          <TriangleAlert className="mt-px size-3.5 shrink-0 text-fg-faint" />
          <span className="flex-1">
            {(weather.error as { status?: number }).status === 502
              ? "The weather archive isn't reachable right now. Enter the temperature by hand, or try again later."
              : (weather.error as { unavailable?: boolean }).unavailable
                ? "Weather lookup isn't available on this server."
                : errorMessage(weather.error)}
          </span>
          <button className="text-fg-faint hover:text-fg" onClick={() => void weather.refetch()} aria-label="Retry">
            <RefreshCw className="size-3" />
          </button>
        </div>
      )}
      {lookup && weather.data && <WeatherResult c={c} w={weather.data} onUse={(v) => save({ temperature_c: v }, "the temperature")} />}
    </div>
  );
}

function WeatherResult({ c, w, onUse }: { c: Case; w: Weather; onUse: (v: number) => void }) {
  const pts = w.hourly.filter((h) => h.temperature_c != null) as { t_utc: number; temperature_c: number }[];
  const lo = Math.min(...pts.map((p) => p.temperature_c));
  const hi = Math.max(...pts.map((p) => p.temperature_c));
  const t0 = pts[0]?.t_utc ?? 0;
  const t1 = pts[pts.length - 1]?.t_utc ?? 1;
  const W = 300;
  const H = 34;
  const x = (t: number) => ((t - t0) / Math.max(1, t1 - t0)) * W;
  const y = (v: number) => H - 3 - ((v - lo) / Math.max(0.1, hi - lo)) * (H - 6);
  const path = pts.map((p, i) => `${i ? "L" : "M"}${x(p.t_utc).toFixed(1)},${y(p.temperature_c).toFixed(1)}`).join(" ");
  const start = c.start_utc;
  const dur = c.recording?.duration_s ?? 0;
  const already = w.suggested_c != null && c.temperature_c != null && Math.abs(c.temperature_c - w.suggested_c) < 0.05;
  return (
    <div className="rounded-lg border border-line bg-surface-2 p-2.5">
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0 text-xs">
          {w.suggested_c != null ? (
            <>
              <span className="text-fg-muted">Suggested </span>
              <span className="font-semibold text-fg tnum">{w.suggested_c.toFixed(1)} °C</span>
              <span className="text-fg-muted"> ({w.basis})</span>
            </>
          ) : (
            <span className="text-fg-muted">No temperature available for this day.</span>
          )}
        </div>
        {w.suggested_c != null && (
          <Button size="xs" variant={already ? "ghost" : "primary"} disabled={already} onClick={() => onUse(Math.round(w.suggested_c! * 10) / 10)}>
            {already ? "In use" : "Use"}
          </Button>
        )}
      </div>
      {pts.length > 1 && (
        <svg viewBox={`0 0 ${W} ${H}`} className="mt-2 h-[34px] w-full overflow-visible" preserveAspectRatio="none" aria-label="Hourly temperature">
          {start != null && dur > 0 && (
            <rect x={x(start)} y={0} width={Math.max(1.5, x(start + dur) - x(start))} height={H} fill="var(--accent)" opacity={0.18} />
          )}
          <path d={path} fill="none" stroke="var(--src-lightning)" strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
        </svg>
      )}
      {pts.length > 1 && (
        <div className="mt-1 flex justify-between text-[10px] text-fg-faint tnum">
          <span>00:00 UTC</span>
          <span>
            {lo.toFixed(1)}–{hi.toFixed(1)} °C{w.sound_speed_mps ? ` · c ≈ ${w.sound_speed_mps.toFixed(1)} m/s` : ""}
          </span>
          <span>24:00</span>
        </div>
      )}
      {w.attribution && <div className="mt-1.5 text-[10px] text-fg-faint">{w.attribution}</div>}
    </div>
  );
}

function NotesField({ c, save }: { c: Case; save: SaveFn }) {
  const [v, setV] = useState(c.notes ?? "");
  const [focus, setFocus] = useState(false);
  useEffect(() => {
    if (!focus) setV(c.notes ?? "");
  }, [c.notes, focus]);
  return (
    <Textarea
      value={v}
      placeholder="Anything worth remembering about this recording: source, claims to check, what you've tried…"
      rows={3}
      onFocus={() => setFocus(true)}
      onChange={(e) => setV(e.target.value)}
      onBlur={() => {
        setFocus(false);
        if (v !== (c.notes ?? "")) save({ notes: v }, "the notes");
      }}
    />
  );
}

function RecordingInfo({ c }: { c: Case }) {
  const qc = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const rec = c.recording;
  const replace = async (f: File) => {
    if (rec) {
      const ok = await confirmDialog({
        title: "Replace the recording?",
        body: "The new file is decoded and a new spectrogram is built. Results computed from the old recording may no longer line up.",
        confirmLabel: "Replace",
      });
      if (!ok) return;
    }
    void startUpload(qc, c.id, f);
  };
  return (
    <div className="flex flex-col gap-2">
      {rec ? (
        <div className="rounded-lg border border-line bg-surface-2 px-3 py-2">
          <KV label="File">{rec.filename}</KV>
          <KV label="Size">{fmtBytes(rec.size_bytes)}</KV>
          <KV label="Duration">{fmtDuration(rec.duration_s)}</KV>
          <KV label="Video">{rec.has_video ? `yes${rec.video_fps ? ` · ${rec.video_fps.toFixed(2)} fps` : ""}` : "no"}</KV>
          {rec.spectrogram && (
            <KV label="Spectrogram">
              {rec.spectrogram.n_frames.toLocaleString()} × {rec.spectrogram.n_bins} · {rec.spectrogram.dt} s × {rec.spectrogram.df.toFixed(2)} Hz
            </KV>
          )}
          <KV label="Status">
            <span className={rec.status === "error" ? "text-danger" : rec.status === "processing" ? "text-warn" : "text-ok"}>{rec.status}</span>
          </KV>
        </div>
      ) : (
        <Hint>No recording yet. Drop a file on the timeline panel, or choose one here.</Hint>
      )}
      <Button size="sm" variant="secondary" icon={<Upload />} className="self-start" onClick={() => input.current?.click()} disabled={rec?.status === "processing"}>
        {rec ? "Replace recording…" : "Choose a recording…"}
      </Button>
      <input
        ref={input}
        type="file"
        accept={MEDIA_ACCEPT}
        className="hidden"
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) void replace(f);
          e.target.value = "";
        }}
      />
    </div>
  );
}
