import type { QueryClient } from "@tanstack/react-query";
import { qk } from "../../api/queries";
import type { AircraftEvent, LightningState } from "../../api/types";
import { modeForTab, ws } from "../../state/workspace";
import { MAX_PLAY_S, pausePlayback, playRange, resumePlayback, stopPlayback } from "./playback";

export type PlayTarget = { t0: number; t1: number; label: string };

/** What the play button plays: the selected flyover / box / onset, else the visible range. */
export function playTarget(qc: QueryClient, caseId: string): PlayTarget {
  const s = ws.get();
  const mode = modeForTab(s.tab);
  if (mode === "spectrogram" && s.selectedEventId) {
    const ev = qc.getQueryData<AircraftEvent[]>(qk.events(caseId))?.find((e) => e.id === s.selectedEventId);
    if (ev) return { t0: ev.t_start, t1: Math.min(ev.t_end, ev.t_start + MAX_PLAY_S), label: "Selected flyover" };
  }
  if (mode === "spectrogram" && s.boxDraft) return { t0: s.boxDraft.t0, t1: s.boxDraft.t1, label: "Box" };
  if (mode === "thunder" && (s.onsetId || s.flashId)) {
    const st = qc.getQueryData<LightningState>(qk.lightningState(caseId));
    const m = st?.onsets.find((o) => o.id === s.onsetId) ?? st?.flashes.find((f) => f.id === s.flashId);
    if (m) return { t0: Math.max(0, m.t - 4), t1: m.t + 26, label: s.onsetId ? "Selected onset" : "Selected flash" };
  }
  const v = s.view;
  if (v.t1 - v.t0 > MAX_PLAY_S) return { t0: v.t0, t1: v.t0 + MAX_PLAY_S, label: "First 10 min of view" };
  return { t0: v.t0, t1: v.t1, label: "Visible range" };
}

export function togglePlay(qc: QueryClient, caseId: string) {
  const pb = ws.get().playback;
  if (pb.state === "playing") return pausePlayback();
  if (pb.state === "paused") return resumePlayback();
  if (pb.state === "loading") return stopPlayback();
  const t = playTarget(qc, caseId);
  void playRange(caseId, t.t0, t.t1, ws.get().speed);
}
