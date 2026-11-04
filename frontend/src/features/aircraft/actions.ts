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

/**
 * Default search pair: the strongest event and its nearest strong neighbour in time (at least
 * `minGap` s apart, with no other strong event between them). Adjacent pairs keep the
 * "nothing heard in between" assumption true on busy skies, which ranks the right place first.
 */
export function suggestPair(events: AircraftEvent[] | undefined, minGap = 120): [AircraftEvent, AircraftEvent] | null {
  const usable = (events ?? []).filter((e) => e.status !== "rejected");
  let strong = usable.filter((e) => eventQuality(e) === "strong" || e.status === "accepted");
  if (strong.length < 2) strong = usable.filter((e) => eventQuality(e) !== "weak" || e.status === "accepted");
  if (strong.length < 2) return null;
  strong.sort((a, b) => a.t_cpa - b.t_cpa);
  const byScore = strong.slice().sort((a, b) => b.score - a.score);
  for (const anchor of byScore) {
    const i = strong.indexOf(anchor);
    const opts: AircraftEvent[] = [];
    const left = strong[i - 1];
    const right = strong[i + 1];
    if (left && anchor.t_cpa - left.t_cpa >= minGap) opts.push(left);
    if (right && right.t_cpa - anchor.t_cpa >= minGap) opts.push(right);
    if (!opts.length) continue;
    opts.sort((a, b) => Math.abs(a.t_cpa - anchor.t_cpa) - Math.abs(b.t_cpa - anchor.t_cpa));
    const other = opts[0];
    return anchor.t_cpa < other.t_cpa ? [anchor, other] : [other, anchor];
  }
  return null;
}

/** Is there another strong event between these two? (the backend then relaxes its silence filter) */
export function strongBetween(events: AircraftEvent[] | undefined, a: AircraftEvent, b: AircraftEvent): number {
  const lo = Math.min(a.t_cpa, b.t_cpa);
  const hi = Math.max(a.t_cpa, b.t_cpa);
  return (events ?? []).filter((e) => e.id !== a.id && e.id !== b.id && e.status !== "rejected" && eventQuality(e) === "strong" && e.t_cpa > lo && e.t_cpa < hi).length;
}
