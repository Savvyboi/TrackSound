import { useQueryClient } from "@tanstack/react-query";
import { useMemo } from "react";
import { api, errorMessage } from "../../api/client";
import { casePath, qk } from "../../api/queries";
import type { AircraftEvent, EventQuality, EventStatus } from "../../api/types";
import { toast } from "../../components/ui/toast";
import { ws } from "../../state/workspace";

export function useAircraftActions(caseId: string) {
  const qc = useQueryClient();
  return useMemo(() => {
    const key = qk.events(caseId);
    const update = (id: string, patch: Partial<AircraftEvent>) =>
      qc.setQueryData<AircraftEvent[]>(key, (l) => (l ?? []).map((e) => (e.id === id ? { ...e, ...patch } : e)));
    return {
      async setStatus(id: string, status: EventStatus) {
        const prev = qc.getQueryData<AircraftEvent[]>(key);
        update(id, { status });
        try {
          const ev = await api.patch<AircraftEvent>(`${casePath(caseId)}/aircraft/events/${encodeURIComponent(id)}`, { status });
          update(id, ev);
        } catch (e) {
          qc.setQueryData(key, prev);
          toast({ tone: "error", title: "Could not update the flyover", body: errorMessage(e) });
        }
      },
      async setHarmonics(id: string, harmonics: number) {
        const prev = qc.getQueryData<AircraftEvent[]>(key);
        update(id, { harmonics });
        try {
          const ev = await api.patch<AircraftEvent>(`${casePath(caseId)}/aircraft/events/${encodeURIComponent(id)}`, { harmonics });
          update(id, ev);
        } catch (e) {
          qc.setQueryData(key, prev);
          toast({ tone: "error", title: "Could not update the flyover", body: errorMessage(e) });
        }
      },
      async remove(id: string) {
        const prev = qc.getQueryData<AircraftEvent[]>(key);
        qc.setQueryData<AircraftEvent[]>(key, (l) => (l ?? []).filter((e) => e.id !== id));
        const s = ws.get();
        ws.set({
          selectedEventId: s.selectedEventId === id ? null : s.selectedEventId,
          checkedEventIds: s.checkedEventIds.filter((x) => x !== id),
        });
        try {
          await api.del(`${casePath(caseId)}/aircraft/events/${encodeURIComponent(id)}`);
        } catch (e) {
          qc.setQueryData(key, prev);
          toast({ tone: "error", title: "Could not delete the flyover", body: errorMessage(e) });
        }
      },
    };
  }, [qc, caseId]);
}

/** Display numbers for events: 1, 2, 3 ... in time order. */
export function eventNumbers(events: AircraftEvent[] | undefined): Map<string, number> {
  const m = new Map<string, number>();
  (events ?? [])
    .slice()
    .sort((a, b) => a.t_cpa - b.t_cpa)
    .forEach((e, i) => m.set(e.id, i + 1));
  return m;
}

/** Event quality from the server, or the same heuristic from the score (noise ~1, real 3+). */
export function eventQuality(ev: AircraftEvent): EventQuality {
  if (ev.quality) return ev.quality;
  return ev.score >= 6 ? "strong" : ev.score >= 3 ? "possible" : "weak";
}

const QRANK: Record<EventQuality, number> = { strong: 2, possible: 1, weak: 0 };

/** The two strongest usable events at least `minGap` seconds apart (the default search pair). */
export function suggestPair(events: AircraftEvent[] | undefined, minGap = 300): [AircraftEvent, AircraftEvent] | null {
  const pool = (events ?? [])
    .filter((e) => e.status !== "rejected" && eventQuality(e) !== "weak")
    .sort((a, b) => Number(b.status === "accepted") - Number(a.status === "accepted") || QRANK[eventQuality(b)] - QRANK[eventQuality(a)] || b.score - a.score);
  let best: [AircraftEvent, AircraftEvent] | null = null;
  let bestScore = -Infinity;
  for (let i = 0; i < pool.length; i++) {
    for (let j = i + 1; j < pool.length; j++) {
      if (Math.abs(pool[i].t_cpa - pool[j].t_cpa) < minGap) continue;
      const s = Math.min(pool[i].score, pool[j].score) + 0.01 * (pool[i].score + pool[j].score);
      if (s > bestScore) {
        bestScore = s;
        best = pool[i].t_cpa < pool[j].t_cpa ? [pool[i], pool[j]] : [pool[j], pool[i]];
      }
    }
  }
  return best;
}
