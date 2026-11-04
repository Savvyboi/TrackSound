import { useQueryClient } from "@tanstack/react-query";
import { useMemo } from "react";
import { api, errorMessage } from "../../api/client";
import { casePath, qk } from "../../api/queries";
import type { EventStatus, Flash, LightningState, ThunderOnset } from "../../api/types";
import { toast } from "../../components/ui/toast";
import { ws } from "../../state/workspace";

export function useLightningActions(caseId: string) {
  const qc = useQueryClient();
  return useMemo(() => {
    const key = qk.lightningState(caseId);
    const base = `${casePath(caseId)}/lightning`;
    const snapshot = () => qc.getQueryData<LightningState>(key);
    const restore = (prev: LightningState | undefined) => prev && qc.setQueryData(key, prev);
    const mutate = (fn: (s: LightningState) => LightningState) =>
      qc.setQueryData<LightningState>(key, (s) => (s ? fn(s) : s));
    const fail = (title: string, e: unknown) => toast({ tone: "error", title, body: errorMessage(e) });
    const sortT = <T extends { t: number }>(l: T[]) => l.slice().sort((a, b) => a.t - b.t);

    return {
      async addOnset(t: number) {
        try {
          const o = await api.post<ThunderOnset>(`${base}/onsets`, { t });
          mutate((s) => ({ ...s, onsets: sortT([...s.onsets, o]) }));
          ws.set({ onsetId: o.id });
        } catch (e) {
          fail("Could not add the thunder onset", e);
        }
      },
      async patchOnset(id: string, patch: { t?: number; sigma_s?: number; status?: EventStatus }) {
        const prev = snapshot();
        mutate((s) => ({ ...s, onsets: sortT(s.onsets.map((o) => (o.id === id ? { ...o, ...patch } : o))) }));
        try {
          const o = await api.patch<ThunderOnset>(`${base}/onsets/${encodeURIComponent(id)}`, patch);
          mutate((s) => ({ ...s, onsets: sortT(s.onsets.map((x) => (x.id === id ? o : x))) }));
        } catch (e) {
          restore(prev);
          fail("Could not update the thunder onset", e);
        }
      },
      async deleteOnset(id: string) {
        const prev = snapshot();
        mutate((s) => ({ ...s, onsets: s.onsets.filter((o) => o.id !== id) }));
        if (ws.get().onsetId === id) ws.set({ onsetId: null });
        try {
          await api.del(`${base}/onsets/${encodeURIComponent(id)}`);
        } catch (e) {
          restore(prev);
          fail("Could not delete the thunder onset", e);
        }
      },
      async addFlash(t: number) {
        try {
          const f = await api.post<Flash>(`${base}/flashes`, { t });
          mutate((s) => ({ ...s, flashes: sortT([...s.flashes, f]) }));
          ws.set({ flashId: f.id });
        } catch (e) {
          fail("Could not add the flash", e);
        }
      },
      async patchFlash(id: string, patch: { t?: number; status?: EventStatus }) {
        const prev = snapshot();
        mutate((s) => ({ ...s, flashes: sortT(s.flashes.map((f) => (f.id === id ? { ...f, ...patch } : f))) }));
        try {
          const f = await api.patch<Flash>(`${base}/flashes/${encodeURIComponent(id)}`, patch);
          mutate((s) => ({ ...s, flashes: sortT(s.flashes.map((x) => (x.id === id ? f : x))) }));
        } catch (e) {
          restore(prev);
          fail("Could not update the flash", e);
        }
      },
      async deleteFlash(id: string) {
        const prev = snapshot();
        mutate((s) => ({ ...s, flashes: s.flashes.filter((f) => f.id !== id) }));
        if (ws.get().flashId === id) ws.set({ flashId: null });
        try {
          await api.del(`${base}/flashes/${encodeURIComponent(id)}`);
        } catch (e) {
          restore(prev);
          fail("Could not delete the flash", e);
        }
      },
    };
  }, [qc, caseId]);
}
