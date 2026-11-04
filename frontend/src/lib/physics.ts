import type { AircraftEvent } from "../api/types";

/** Speed of sound (m/s) at air temperature T (°C); null means 15 °C (docs/API.md). */
export function speedOfSound(tempC: number | null | undefined): number {
  const T = tempC ?? 15;
  return 331.3 * Math.sqrt(1 + T / 273.15);
}

export type Curve = { t: Float64Array; f: Float64Array };

const curveCache = new Map<string, Curve>();

/**
 * Ideal Doppler curve of an aircraft event (port of dopplertrack.doppler.ideal_curve):
 * the fundamental (Hz) heard at recording time t_obs for emission times around t_cpa_emit.
 */
export function idealCurve(ev: Pick<AircraftEvent, "f0" | "v" | "d" | "t_cpa_emit">, c: number): Curve {
  const key = `${ev.f0}|${ev.v}|${ev.d}|${ev.t_cpa_emit}|${c}`;
  const hit = curveCache.get(key);
  if (hit) return hit;
  const n = 1201;
  const t = new Float64Array(n);
  const f = new Float64Array(n);
  const { f0, v, d, t_cpa_emit } = ev;
  for (let i = 0; i < n; i++) {
    const dte = -600 + (1200 * i) / (n - 1);
    const te = t_cpa_emit + dte;
    const r = Math.sqrt(d * d + (v * dte) * (v * dte));
    const rr = r > 0 ? (v * v * dte) / r : 0;
    f[i] = (f0 * c) / (c + rr);
    t[i] = te + r / c;
  }
  const out = { t, f };
  if (curveCache.size > 500) curveCache.clear();
  curveCache.set(key, out);
  return out;
}
