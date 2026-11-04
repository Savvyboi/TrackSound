// Time zone helpers built on Intl (no tz database shipped with the app).

const dtfCache = new Map<string, Intl.DateTimeFormat>();

function partsFormatter(tz: string): Intl.DateTimeFormat {
  let f = dtfCache.get(tz);
  if (!f) {
    f = new Intl.DateTimeFormat("en-US", {
      timeZone: tz,
      hourCycle: "h23",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
    dtfCache.set(tz, f);
  }
  return f;
}

export function browserTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

export function isValidTimeZone(tz: string): boolean {
  try {
    new Intl.DateTimeFormat("en-US", { timeZone: tz });
    return true;
  } catch {
    return false;
  }
}

let zones: string[] | null = null;
export function allTimeZones(): string[] {
  if (zones) return zones;
  let list: string[] = [];
  try {
    const sv = (Intl as unknown as { supportedValuesOf?: (k: string) => string[] }).supportedValuesOf;
    if (sv) list = sv("timeZone");
  } catch {
    /* ignore */
  }
  if (!list.length) list = ["UTC", "Europe/London", "Europe/Berlin", "America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles", "Asia/Tokyo", "Australia/Sydney"];
  if (!list.includes("UTC")) list = ["UTC", ...list];
  zones = list;
  return list;
}

export type ZonedParts = { year: number; month: number; day: number; hour: number; minute: number; second: number };

export function zonedParts(unixS: number, tz: string): ZonedParts {
  const parts = partsFormatter(tz).formatToParts(new Date(Math.floor(unixS) * 1000));
  const get = (t: string) => Number(parts.find((p) => p.type === t)?.value ?? 0);
  return { year: get("year"), month: get("month"), day: get("day"), hour: get("hour") % 24, minute: get("minute"), second: get("second") };
}

/** Offset of `tz` from UTC at the given instant, in seconds (east positive). */
export function tzOffsetS(unixS: number, tz: string): number {
  const p = zonedParts(unixS, tz);
  const asUtc = Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second) / 1000;
  return asUtc - Math.floor(unixS);
}

/** Wall-clock time in `tz` → unix seconds. */
export function zonedToUnix(p: ZonedParts, tz: string): number {
  const guess = Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, Math.floor(p.second)) / 1000;
  const off1 = tzOffsetS(guess, tz);
  let t = guess - off1;
  const off2 = tzOffsetS(t, tz);
  if (off2 !== off1) t = guess - off2;
  return t + (p.second - Math.floor(p.second));
}

const pad2 = (n: number) => String(n).padStart(2, "0");

/** "2026-09-08T14:03:20" in tz (for <input type="datetime-local" step="1">). */
export function toLocalInput(unixS: number, tz: string): string {
  const p = zonedParts(unixS, tz);
  return `${p.year}-${pad2(p.month)}-${pad2(p.day)}T${pad2(p.hour)}:${pad2(p.minute)}:${pad2(p.second)}`;
}

export function fromLocalInput(value: string, tz: string): number | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?$/.exec(value);
  if (!m) return null;
  const sec = Number(m[6] ?? 0) + (m[7] ? Number(`0.${m[7]}`) : 0);
  return zonedToUnix(
    { year: +m[1], month: +m[2], day: +m[3], hour: +m[4], minute: +m[5], second: sec },
    tz,
  );
}

const abbrCache = new Map<string, Intl.DateTimeFormat>();
/** Short zone name at an instant: "EDT", "GMT+1", "UTC". */
export function tzAbbr(unixS: number, tz: string): string {
  if (tz === "UTC" || tz === "Etc/UTC") return "UTC";
  let f = abbrCache.get(tz);
  if (!f) {
    f = new Intl.DateTimeFormat("en-US", { timeZone: tz, timeZoneName: "short" });
    abbrCache.set(tz, f);
  }
  const p = f.formatToParts(new Date(unixS * 1000)).find((x) => x.type === "timeZoneName");
  return p?.value ?? tz;
}

/** "14:03:20" (or "14:03") in tz. */
export function fmtClock(unixS: number, tz: string, seconds = true): string {
  const p = zonedParts(unixS, tz);
  return seconds ? `${pad2(p.hour)}:${pad2(p.minute)}:${pad2(p.second)}` : `${pad2(p.hour)}:${pad2(p.minute)}`;
}

/** "2026-09-08" in tz. */
export function fmtDateIso(unixS: number, tz: string): string {
  const p = zonedParts(unixS, tz);
  return `${p.year}-${pad2(p.month)}-${pad2(p.day)}`;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
/** "8 Sep 2026" in tz. */
export function fmtDatePretty(unixS: number, tz: string): string {
  const p = zonedParts(unixS, tz);
  return `${p.day} ${MONTHS[p.month - 1]} ${p.year}`;
}

/** "8 Sep 2026, 14:03:20 EDT". */
export function fmtDateTime(unixS: number | null | undefined, tz: string, opts: { seconds?: boolean } = {}): string {
  if (unixS == null || !Number.isFinite(unixS)) return "–";
  return `${fmtDatePretty(unixS, tz)}, ${fmtClock(unixS, tz, opts.seconds ?? true)} ${tzAbbr(unixS, tz)}`;
}

/** "2026-09-08 18:03:20 UTC". */
export function fmtUtc(unixS: number | null | undefined, seconds = true): string {
  if (unixS == null || !Number.isFinite(unixS)) return "–";
  return `${fmtDateIso(unixS, "UTC")} ${fmtClock(unixS, "UTC", seconds)} UTC`;
}

/** "YYYY-MM-DD" → [start, end) unix seconds of that UTC day. */
export function utcDayRange(date: string): [number, number] | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(date);
  if (!m) return null;
  const t0 = Date.UTC(+m[1], +m[2] - 1, +m[3]) / 1000;
  return [t0, t0 + 86400];
}

/** UTC datetime-local string helpers (for windows entered in UTC). */
export function toUtcInput(unixS: number): string {
  return toLocalInput(unixS, "UTC");
}
export function fromUtcInput(value: string): number | null {
  return fromLocalInput(value, "UTC");
}
