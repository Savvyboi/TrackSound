import { createContext, useContext } from "react";
import type { Case } from "../../api/types";
import { speedOfSound } from "../../lib/physics";
import { browserTimeZone } from "../../lib/tz";

export const CaseContext = createContext<Case | null>(null);

export function useCaseData(): Case {
  const c = useContext(CaseContext);
  if (!c) throw new Error("useCaseData outside a case workspace");
  return c;
}

/** The case's display time zone (falls back to the browser's). */
export function caseTz(c: Case): string {
  return c.timezone || browserTimeZone();
}

export function caseC(c: Case): number {
  return speedOfSound(c.temperature_c);
}

export function recordingReady(c: Case): boolean {
  return c.recording?.status === "ready" && !!c.recording.spectrogram;
}

export function caseDuration(c: Case): number {
  return c.recording?.duration_s ?? 0;
}
