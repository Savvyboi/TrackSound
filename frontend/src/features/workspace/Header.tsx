import { CalendarDays, ChevronLeft, Clock3, FileAudio, MapPinned, Moon, Pencil, Sun, Thermometer, Video } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { usePatchCase } from "../../api/queries";
import { LogoMark } from "../../components/Brand";
import { SourceBadge } from "../../components/SourceBadge";
import { IconButton } from "../../components/ui/Button";
import { toast } from "../../components/ui/toast";
import { errorMessage } from "../../api/client";
import { cn } from "../../lib/cn";
import { fmtBytes, fmtRec } from "../../lib/format";
import { fmtBBoxShort } from "../../lib/geo";
import { toggleTheme, useTheme } from "../../lib/theme";
import { fmtClock, fmtDatePretty, tzAbbr } from "../../lib/tz";
import { setTab } from "../../state/workspace";
import { caseC, caseTz, useCaseData } from "./caseContext";
import { JobsIndicator } from "./JobsIndicator";

export function Header() {
  const c = useCaseData();
  const theme = useTheme();
  const tz = caseTz(c);
  const rec = c.recording;
  return (
    <header className="flex h-12 shrink-0 items-center gap-3 border-b border-line bg-surface px-3">
      <Link to="/" className="flex items-center gap-1 rounded-md py-1 pr-1.5 pl-0.5 text-fg-muted hover:bg-surface-3 hover:text-fg" data-tip="All cases">
        <ChevronLeft className="size-4" />
        <LogoMark className="size-6" />
      </Link>
      <CaseName />
      <Sep />
      {rec ? (
        <div className="flex min-w-0 items-center gap-2 text-xs text-fg-muted" data-tip={`${rec.filename} · ${fmtBytes(rec.size_bytes)}`}>
          {rec.has_video ? <Video className="size-3.5 shrink-0" /> : <FileAudio className="size-3.5 shrink-0" />}
          <span className="max-w-[200px] truncate text-fg">{rec.filename}</span>
          {rec.duration_s != null && <span className="tnum">{fmtRec(rec.duration_s)}</span>}
        </div>
      ) : (
        <span className="text-xs text-fg-faint">No recording</span>
      )}
      <Sep />
      <CaseClock />
      <Sep className="hidden xl:block" />
      <HeaderItem icon={<CalendarDays />} label="Date (UTC)" onClick={() => setTab("setup")} empty={!c.date} className="hidden xl:flex">
        {c.date ?? "Set date"}
      </HeaderItem>
      <HeaderItem icon={<MapPinned />} label="Search region" onClick={() => setTab("setup")} empty={!c.region} className="hidden 2xl:flex">
        {c.region ? fmtBBoxShort(c.region) : "Set region"}
      </HeaderItem>
      <HeaderItem
        icon={<Thermometer />}
        label={`Air temperature: speed of sound ${caseC(c).toFixed(1)} m/s`}
        onClick={() => setTab("setup")}
        className="hidden 2xl:flex"
      >
        {c.temperature_c != null ? `${c.temperature_c} °C` : <span className="text-fg-faint">15 °C default</span>}
      </HeaderItem>
      <div className="flex-1" />
      <JobsIndicator caseId={c.id} />
      <IconButton label={theme === "dark" ? "Light theme" : "Dark theme"} onClick={toggleTheme}>
        {theme === "dark" ? <Sun /> : <Moon />}
      </IconButton>
      <span className="sr-only">{tz}</span>
    </header>
  );
}

function Sep({ className }: { className?: string }) {
  return <div className={cn("h-5 w-px shrink-0 bg-line", className)} />;
}

function HeaderItem({
  icon,
  label,
  children,
  onClick,
  empty,
  className,
}: {
  icon: React.ReactNode;
  label: string;
  children: React.ReactNode;
  onClick?: () => void;
  empty?: boolean;
  className?: string;
}) {
  return (
    <button
      onClick={onClick}
      data-tip={label}
      className={cn(
        "flex min-w-0 items-center gap-1.5 rounded-md px-1.5 py-1 text-xs whitespace-nowrap transition-colors hover:bg-surface-3 tnum [&_svg]:size-3.5 [&_svg]:shrink-0",
        empty ? "text-fg-faint" : "text-fg",
        className,
      )}
    >
      <span className="text-fg-faint">{icon}</span>
      <span className="truncate">{children}</span>
    </button>
  );
}

function CaseName() {
  const c = useCaseData();
  const patch = usePatchCase(c.id);
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(c.name);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (!editing) setValue(c.name);
  }, [c.name, editing]);
  useEffect(() => {
    if (editing) input.current?.select();
  }, [editing]);
  const commit = () => {
    setEditing(false);
    const v = value.trim();
    if (v && v !== c.name)
      patch.mutate({ name: v }, { onError: (e) => toast({ tone: "error", title: "Could not rename the case", body: errorMessage(e) }) });
    else setValue(c.name);
  };
  if (editing)
    return (
      <input
        ref={input}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") commit();
          if (e.key === "Escape") {
            setValue(c.name);
            setEditing(false);
          }
        }}
        aria-label="Case name"
        className="h-8 w-[260px] rounded-md border border-accent-line bg-surface-2 px-2 text-sm font-semibold text-fg outline-none focus:ring-2 focus:ring-accent/15"
      />
    );
  return (
    <button
      onClick={() => setEditing(true)}
      className="group flex min-w-0 items-center gap-1.5 rounded-md px-1.5 py-1 text-sm font-semibold text-fg hover:bg-surface-3"
      data-tip="Rename case"
    >
      <span className="max-w-[280px] truncate">{c.name}</span>
      <Pencil className="size-3 shrink-0 text-fg-faint opacity-0 transition-opacity group-hover:opacity-100" />
    </button>
  );
}

/** Recording start time, if known, and which source set it. */
function CaseClock() {
  const c = useCaseData();
  const tz = caseTz(c);
  if (c.start_utc == null)
    return (
      <button
        onClick={() => setTab("setup")}
        className="flex items-center gap-2 rounded-md px-1.5 py-1 text-xs text-fg-faint hover:bg-surface-3 hover:text-fg-muted"
        data-tip="The recording's start time is unknown. Enter it in Setup, or estimate it from aircraft, lightning or mains hum."
      >
        <Clock3 className="size-3.5" />
        Start time unknown
      </button>
    );
  return (
    <button
      onClick={() => setTab("setup")}
      className="flex min-w-0 items-center gap-2 rounded-md px-1.5 py-1 hover:bg-surface-3"
      data-tip={`Recording started ${fmtDatePretty(c.start_utc, tz)} ${fmtClock(c.start_utc, tz)} ${tzAbbr(c.start_utc, tz)} (${fmtClock(c.start_utc, "UTC")} UTC)`}
    >
      <Clock3 className="size-3.5 shrink-0 text-accent" />
      <span className="text-sm font-semibold text-fg tnum">{fmtClock(c.start_utc, tz)}</span>
      <span className="text-xs text-fg-muted tnum">
        {tzAbbr(c.start_utc, tz)} · {fmtDatePretty(c.start_utc, tz)}
      </span>
      <SourceBadge source={c.start_utc_source} />
    </button>
  );
}
