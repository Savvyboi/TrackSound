import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef } from "react";
import { toast } from "../components/ui/toast";
import { titleCase } from "../lib/format";
import { api, errorMessage } from "./client";
import { qk, useJobsQuery } from "./queries";
import type { Job } from "./types";

export const isActive = (j: Job) => j.status === "queued" || j.status === "running";

/** Human label for a job kind such as "aircraft.detect" or "lightning.detect_thunder". */
export function jobLabel(kind: string): string {
  const k = kind.toLowerCase();
  const has = (...w: string[]) => w.every((x) => k.includes(x));
  if (has("recording")) return "Processing recording";
  if (has("aircraft", "detect")) return "Detecting flyovers";
  if (has("aircraft", "fit")) return "Fitting flyover";
  if (has("aircraft", "flight")) return "Fetching ADS-B flights";
  if (has("aircraft", "refine")) return "Refining search";
  if (has("aircraft", "search")) return "Aircraft search";
  if (has("thunder")) return "Detecting thunder";
  if (has("flash")) return "Detecting flashes";
  if (has("lightning", "strike")) return "Fetching lightning strikes";
  if (has("lightning", "solve")) return "Lightning solve";
  if (has("enf", "extract")) return "Extracting mains hum";
  if (has("enf", "match")) return "Matching mains hum";
  return titleCase(kind);
}

/** Which source a job belongs to (used to refetch the right data when it finishes). */
export function jobSource(kind: string): "recording" | "aircraft" | "lightning" | "enf" | "other" {
  const p = kind.toLowerCase().split(/[._-]/)[0];
  if (p === "recording" || p === "aircraft" || p === "lightning" || p === "enf") return p;
  return "other";
}

type DoneHandler = (job: Job) => void;
const handlers = new Map<string, TrackOptions>();
const started = new Set<string>();

export type TrackOptions = {
  onDone?: DoneHandler;
  /** Called for any terminal state (done, error, cancelled). */
  onFinish?: DoneHandler;
  /** Don't toast on success (still toasts errors). */
  quiet?: boolean;
  /** Custom success message. */
  success?: (job: Job) => string | undefined;
};

/** Register a job we just started: show it immediately and react when it finishes. */
export function trackJob(qc: QueryClient, job: Job, opts: TrackOptions = {}) {
  started.add(job.id);
  handlers.set(job.id, opts);
  qc.setQueryData<Job[]>(qk.jobs, (list) => [job, ...(list ?? []).filter((j) => j.id !== job.id)]);
  void qc.invalidateQueries({ queryKey: qk.jobs });
}

export function invalidateForJob(qc: QueryClient, job: Job) {
  const cid = job.case_id;
  if (!cid) return;
  const src = jobSource(job.kind);
  if (src === "recording") {
    void qc.invalidateQueries({ queryKey: qk.case(cid) });
    void qc.invalidateQueries({ queryKey: qk.cases });
    // a new recording changes everything derived from it
    void qc.invalidateQueries({ queryKey: qk.aircraft(cid) });
    void qc.invalidateQueries({ queryKey: qk.lightning(cid) });
    void qc.invalidateQueries({ queryKey: qk.enf(cid) });
  } else if (src === "aircraft") void qc.invalidateQueries({ queryKey: qk.aircraft(cid) });
  else if (src === "lightning") void qc.invalidateQueries({ queryKey: qk.lightning(cid) });
  else if (src === "enf") void qc.invalidateQueries({ queryKey: qk.enf(cid) });
  else void qc.invalidateQueries({ queryKey: qk.case(cid) });
}

function summarise(job: Job): string | undefined {
  const r = job.result;
  if (r && typeof r === "object") {
    if (typeof r.count === "number") return `${r.count} found`;
    if (typeof r.n_flights === "number") return `${r.n_flights} prop flights`;
    if (r.nominal_hz !== undefined) return r.nominal_hz ? `Mains hum at ${r.nominal_hz} Hz` : "No mains hum found";
  }
  if (job.message && !["Done", "Running", "Queued"].includes(job.message)) return job.message;
  return undefined;
}

/** Polls the job list, toasts when jobs finish and refetches whatever they changed. */
export function JobsWatcher() {
  const qc = useQueryClient();
  const { data } = useJobsQuery();
  const seen = useRef(new Map<string, Job["status"]>());

  useEffect(() => {
    if (!data) return;
    for (const job of data) {
      const prev = seen.current.get(job.id);
      seen.current.set(job.id, job.status);
      const wasActive = prev === "queued" || prev === "running" || (prev === undefined && started.has(job.id));
      if (isActive(job) || !wasActive || prev === job.status) continue;
      // transition into a terminal state
      started.delete(job.id);
      const h = handlers.get(job.id);
      handlers.delete(job.id);
      invalidateForJob(qc, job);
      try {
        h?.onFinish?.(job);
      } catch (e) {
        console.error(e);
      }
      const label = jobLabel(job.kind);
      if (job.status === "done") {
        if (!h?.quiet) toast({ tone: "success", title: `${label} finished`, body: h?.success?.(job) ?? summarise(job) });
        try {
          h?.onDone?.(job);
        } catch (e) {
          console.error(e);
        }
      } else if (job.status === "error") {
        toast({ tone: "error", title: `${label} failed`, body: job.error || job.message || undefined });
      } else if (job.status === "cancelled") {
        toast({ tone: "info", title: `${label} cancelled` });
      }
    }
  }, [data, qc]);

  return null;
}

/** Start a long operation: POST, track the returned Job, toast errors. */
export function useStartJob() {
  const qc = useQueryClient();
  return useCallback(
    async (path: string, body: unknown, opts: TrackOptions = {}): Promise<Job | null> => {
      try {
        const job = await api.post<Job>(path, body);
        trackJob(qc, job, opts);
        return job;
      } catch (e) {
        throw e instanceof Error ? e : new Error(errorMessage(e));
      }
    },
    [qc],
  );
}

export async function cancelJob(qc: QueryClient, id: string) {
  try {
    const j = await api.post<Job>(`/api/jobs/${encodeURIComponent(id)}/cancel`);
    qc.setQueryData<Job[]>(qk.jobs, (list) => (list ?? []).map((x) => (x.id === j.id ? j : x)));
  } catch (e) {
    toast({ tone: "error", title: "Could not cancel the job", body: errorMessage(e) });
  }
}
