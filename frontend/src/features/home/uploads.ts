import type { QueryClient } from "@tanstack/react-query";
import { uploadWithProgress, errorMessage } from "../../api/client";
import { trackJob } from "../../api/jobs";
import { casePath, setCaseData } from "../../api/queries";
import type { Case, Job } from "../../api/types";
import { toast } from "../../components/ui/toast";
import { createStore } from "../../lib/store";

export type Upload = {
  caseId: string;
  fileName: string;
  size: number;
  phase: "uploading" | "processing" | "error" | "cancelled";
  loaded: number;
  total: number;
  jobId?: string;
  error?: string;
  startedAt: number;
};

export const uploadsStore = createStore<{ byCase: Record<string, Upload> }>({ byCase: {} });
const aborts = new Map<string, () => void>();

function patch(caseId: string, p: Partial<Upload>) {
  uploadsStore.set((s) => {
    const cur = s.byCase[caseId];
    if (!cur) return {};
    return { byCase: { ...s.byCase, [caseId]: { ...cur, ...p } } };
  });
}

export function clearUpload(caseId: string) {
  uploadsStore.set((s) => {
    const next = { ...s.byCase };
    delete next[caseId];
    return { byCase: next };
  });
}

export function cancelUpload(caseId: string) {
  aborts.get(caseId)?.();
}

export const MEDIA_ACCEPT = "audio/*,video/*,.wav,.mp3,.flac,.ogg,.opus,.m4a,.aac,.wma,.mp4,.mov,.mkv,.webm,.avi,.ts,.mts";

/** Upload a recording into a case, then follow its processing job. */
export async function startUpload(qc: QueryClient, caseId: string, file: File): Promise<Job | null> {
  uploadsStore.set((s) => ({
    byCase: {
      ...s.byCase,
      [caseId]: { caseId, fileName: file.name, size: file.size, phase: "uploading", loaded: 0, total: file.size, startedAt: Date.now() },
    },
  }));
  const form = new FormData();
  form.append("file", file, file.name);
  const h = uploadWithProgress<{ case: Case; job: Job }>(`${casePath(caseId)}/recording`, form, (loaded, total) =>
    patch(caseId, { loaded, total }),
  );
  aborts.set(caseId, h.abort);
  try {
    const res = await h.promise;
    setCaseData(qc, res.case);
    patch(caseId, { phase: "processing", jobId: res.job.id, loaded: file.size });
    trackJob(qc, res.job, {
      success: () => `${file.name} is ready`,
      onDone: () => clearUpload(caseId),
    });
    return res.job;
  } catch (e) {
    if ((e as Error)?.name === "AbortError") {
      patch(caseId, { phase: "cancelled" });
      setTimeout(() => clearUpload(caseId), 1500);
    } else {
      patch(caseId, { phase: "error", error: errorMessage(e) });
      toast({ tone: "error", title: "Upload failed", body: errorMessage(e) });
    }
    return null;
  } finally {
    aborts.delete(caseId);
  }
}

export function caseNameFromFile(name: string): string {
  const base = name.replace(/\.[^.]+$/, "").replace(/[_]+/g, " ").trim();
  return base || "Untitled case";
}
