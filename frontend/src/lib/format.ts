// Number, time and size formatting. Recording time is seconds from the start of the
// recording; UTC time is unix seconds (see docs/API.md).

const pad2 = (n: number) => String(n).padStart(2, "0");

/** Recording time as m:ss, h:mm:ss, optionally with fractional seconds. */
export function fmtRec(t: number, decimals = 0): string {
  if (!Number.isFinite(t)) return "–";
  const neg = t < 0;
  let s = Math.abs(t);
  const scale = 10 ** decimals;
  s = Math.round(s * scale) / scale;
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s - h * 3600 - m * 60;
  const secStr = decimals > 0 ? sec.toFixed(decimals).padStart(3 + decimals, "0") : pad2(Math.floor(sec));
  const body = h > 0 ? `${h}:${pad2(m)}:${secStr}` : `${m}:${secStr}`;
  return neg ? `−${body}` : body;
}

/** Human duration: "14 min 3 s", "2 h 05 min", "45 s". */
export function fmtDuration(s: number | null | undefined): string {
  if (s == null || !Number.isFinite(s)) return "–";
  if (s < 60) return `${s < 10 ? s.toFixed(1) : Math.round(s)} s`;
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = Math.round(s % 60);
  if (h > 0) return `${h} h ${pad2(m)} min`;
  return sec ? `${m} min ${sec} s` : `${m} min`;
}

/** Compact duration for estimates: "~40 s", "~3 min", "~1.5 h". */
export function fmtEstimate(s: number): string {
  if (!Number.isFinite(s)) return "–";
  if (s < 1) return "< 1 s";
  if (s < 90) return `~${Math.round(s)} s`;
  if (s < 5400) return `~${Math.round(s / 60)} min`;
  return `~${(s / 3600).toFixed(1)} h`;
}

export function fmtBytes(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return "–";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < u.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v < 10 && i > 0 ? v.toFixed(1) : Math.round(v)} ${u[i]}`;
}

export function fmtNum(v: number | null | undefined, digits = 1): string {
  if (v == null || !Number.isFinite(v)) return "–";
  return v.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function fmtInt(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "–";
  return Math.round(v).toLocaleString("en-US");
}

export function fmtPct(v: number | null | undefined, digits = 0): string {
  if (v == null || !Number.isFinite(v)) return "–";
  return `${(v * 100).toFixed(digits)}%`;
}

/** Distance in metres: "430 m", "2.4 km", "38 km". */
export function fmtDist(m: number | null | undefined): string {
  if (m == null || !Number.isFinite(m)) return "–";
  if (m < 1000) return `${Math.round(m)} m`;
  if (m < 10000) return `${(m / 1000).toFixed(1)} km`;
  return `${Math.round(m / 1000)} km`;
}

export function fmtHz(f: number | null | undefined, digits = 1): string {
  if (f == null || !Number.isFinite(f)) return "–";
  return `${f.toFixed(digits)} Hz`;
}

export function fmtSpeed(mps: number | null | undefined): string {
  if (mps == null || !Number.isFinite(mps)) return "–";
  return `${Math.round(mps)} m/s · ${Math.round(mps * 1.943844)} kt`;
}

export function fmtCoord(lat: number, lon: number, digits = 5): string {
  return `${lat.toFixed(digits)}, ${lon.toFixed(digits)}`;
}

export function fmtLatLonPretty(lat: number, lon: number, digits = 4): string {
  const ns = lat >= 0 ? "N" : "S";
  const ew = lon >= 0 ? "E" : "W";
  return `${Math.abs(lat).toFixed(digits)}° ${ns}, ${Math.abs(lon).toFixed(digits)}° ${ew}`;
}

/** Signed seconds: "+3.2 s", "−0.4 s". */
export function fmtSigned(v: number, digits = 1, unit = "s"): string {
  const s = Math.abs(v).toFixed(digits);
  return `${v < 0 ? "−" : "+"}${s} ${unit}`;
}

/** "3 min ago", "yesterday", "12 Mar". */
export function fmtAgo(unix: number | null | undefined): string {
  if (!unix) return "–";
  const d = Date.now() / 1000 - unix;
  if (d < 45) return "just now";
  if (d < 3600) return `${Math.round(d / 60)} min ago`;
  if (d < 86400) return `${Math.round(d / 3600)} h ago`;
  if (d < 2 * 86400) return "yesterday";
  if (d < 7 * 86400) return `${Math.round(d / 86400)} days ago`;
  return new Date(unix * 1000).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

export function titleCase(s: string): string {
  return s.replace(/[_.-]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export function clamp(v: number, lo: number, hi: number): number {
  return v < lo ? lo : v > hi ? hi : v;
}
