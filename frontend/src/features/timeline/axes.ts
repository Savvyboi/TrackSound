import { fmtRec } from "../../lib/format";
import { fmtClock, tzAbbr } from "../../lib/tz";

export type Geom = {
  W: number; // container css px
  H: number;
  x0: number; // plot area (css px)
  x1: number;
  y0: number;
  y1: number;
};

export const GUTTER_LEFT = 50;
export const GUTTER_RIGHT = 10;
export const GUTTER_TOP = 6;
export const AXIS_H = 22;
export const CLOCK_H = 15;

const TIME_STEPS = [0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200];

export function timeStep(span: number, widthPx: number, minPx = 84): number {
  for (const s of TIME_STEPS) if ((s / span) * widthPx >= minPx) return s;
  return TIME_STEPS[TIME_STEPS.length - 1];
}

export function niceStep(range: number, px: number, minPx: number): number {
  const raw = (range * minPx) / Math.max(1, px);
  const p = 10 ** Math.floor(Math.log10(raw));
  for (const m of [1, 2, 2.5, 5, 10]) if (m * p >= raw) return m * p;
  return 10 * p;
}

export function decimalsFor(step: number): number {
  if (step >= 1) return 0;
  return Math.min(3, Math.ceil(-Math.log10(step) - 1e-9));
}

export type Theme = {
  axis: string;
  grid: string;
  text: string;
  faint: string;
  gutter: string;
  line: string;
};

export function readTheme(el: Element): Theme {
  const cs = getComputedStyle(el);
  const v = (n: string, d: string) => cs.getPropertyValue(n).trim() || d;
  return {
    axis: v("--plot-axis", "#8b95a6"),
    grid: v("--plot-grid", "rgba(255,255,255,0.07)"),
    text: v("--fg-muted", "#9ba4b4"),
    faint: v("--fg-faint", "#687284"),
    gutter: v("--surface", "#0e1116"),
    line: v("--line", "#1f252f"),
  };
}

export const FONT = '500 10.5px "Inter Variable", Inter, ui-sans-serif, system-ui, sans-serif';
export const FONT_SMALL = '500 10px "Inter Variable", Inter, ui-sans-serif, system-ui, sans-serif';

/** Bottom time axis: recording time (m:ss), plus clock time when the start is known. */
export function drawTimeAxis(
  ctx: CanvasRenderingContext2D,
  g: Geom,
  t0: number,
  t1: number,
  th: Theme,
  clock: { startUtc: number; tz: string } | null,
  gridTo: number,
) {
  const w = g.x1 - g.x0;
  const span = t1 - t0;
  const step = timeStep(span, w);
  const dec = decimalsFor(step);
  const first = Math.ceil(t0 / step) * step;
  ctx.font = FONT;
  ctx.textBaseline = "top";
  ctx.textAlign = "center";
  for (let t = first; t <= t1 + 1e-9; t += step) {
    const x = Math.round(g.x0 + ((t - t0) / span) * w) + 0.5;
    // grid
    ctx.strokeStyle = th.grid;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(x, gridTo);
    ctx.lineTo(x, g.y1);
    ctx.stroke();
    // tick
    ctx.strokeStyle = th.axis;
    ctx.beginPath();
    ctx.moveTo(x, g.y1);
    ctx.lineTo(x, g.y1 + 4);
    ctx.stroke();
    ctx.fillStyle = th.text;
    const label = fmtRec(t, dec);
    const half = ctx.measureText(label).width / 2;
    ctx.fillText(label, Math.min(Math.max(x, g.x0 - 6 + half), g.W - 2 - half), g.y1 + 6);
    if (clock) {
      ctx.fillStyle = th.faint;
      ctx.font = FONT_SMALL;
      const cl = fmtClock(clock.startUtc + t, clock.tz, step < 60 || (clock.startUtc + t) % 60 !== 0);
      const ch = ctx.measureText(cl).width / 2;
      ctx.fillText(cl, Math.min(Math.max(x, g.x0 - 6 + ch), g.W - 2 - ch), g.y1 + 6 + CLOCK_H);
      ctx.font = FONT;
    }
  }
  // axis line
  ctx.strokeStyle = th.line;
  ctx.beginPath();
  ctx.moveTo(g.x0, g.y1 + 0.5);
  ctx.lineTo(g.x1, g.y1 + 0.5);
  ctx.stroke();
  if (clock) {
    ctx.textAlign = "right";
    ctx.fillStyle = th.faint;
    ctx.font = FONT_SMALL;
    ctx.fillText(tzAbbr(clock.startUtc + t0, clock.tz), g.x0 - 8, g.y1 + 6 + CLOCK_H);
  }
}

/** Left value axis (Hz, dB, ...). */
export function drawValueAxis(
  ctx: CanvasRenderingContext2D,
  g: Geom,
  lo: number,
  hi: number,
  th: Theme,
  unit: string,
  opts: { grid?: boolean; minPx?: number; format?: (v: number, step: number) => string } = {},
) {
  const h = g.y1 - g.y0;
  const step = niceStep(hi - lo, h, opts.minPx ?? 34);
  const first = Math.ceil(lo / step) * step;
  ctx.font = FONT;
  ctx.textAlign = "right";
  ctx.textBaseline = "middle";
  const dec = decimalsFor(step);
  for (let v = first; v <= hi + 1e-9; v += step) {
    const y = Math.round(g.y1 - ((v - lo) / (hi - lo)) * h) + 0.5;
    if (y < g.y0 + 14) continue; // keep clear of the unit label
    if (opts.grid !== false) {
      ctx.strokeStyle = th.grid;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(g.x0, y);
      ctx.lineTo(g.x1, y);
      ctx.stroke();
    }
    ctx.strokeStyle = th.axis;
    ctx.beginPath();
    ctx.moveTo(g.x0 - 4, y);
    ctx.lineTo(g.x0, y);
    ctx.stroke();
    ctx.fillStyle = th.text;
    ctx.fillText(opts.format ? opts.format(v, step) : v.toFixed(dec), g.x0 - 7, y);
  }
  ctx.strokeStyle = th.line;
  ctx.beginPath();
  ctx.moveTo(g.x0 - 0.5, g.y0);
  ctx.lineTo(g.x0 - 0.5, g.y1);
  ctx.stroke();
  // unit label
  ctx.save();
  ctx.fillStyle = th.faint;
  ctx.font = FONT_SMALL;
  ctx.textAlign = "left";
  ctx.textBaseline = "top";
  ctx.fillText(unit, 6, g.y0 + 1);
  ctx.restore();
}
