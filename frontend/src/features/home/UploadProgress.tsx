import { useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, CircleAlert, FileAudio, X } from "lucide-react";
import { cancelJob } from "../../api/jobs";
import { useJobsQuery } from "../../api/queries";
import type { Case, Job } from "../../api/types";
import { IconButton } from "../../components/ui/Button";
import { ProgressBar } from "../../components/ui/misc";
import { cn } from "../../lib/cn";
import { fmtBytes } from "../../lib/format";
import { cancelUpload, clearUpload, uploadsStore, type Upload } from "./uploads";

export type UploadView = {
  phase: "uploading" | "processing" | "ready" | "error" | "cancelled";
  progress: number;
  label: string;
  detail: string;
  job?: Job;
  upload?: Upload;
};

/** Combined state of a case's upload (bytes sent) and processing job (spectrogram build). */
export function useUploadView(c: Case | undefined): UploadView | null {
  const upload = uploadsStore.use((s) => (c ? s.byCase[c.id] : undefined));
  const { data: jobs } = useJobsQuery();
  if (!c) return null;
  if (upload && upload.phase === "uploading") {
    const p = upload.total ? upload.loaded / upload.total : 0;
    return {
      phase: "uploading",
      progress: p,
      label: "Uploading",
      detail: `${fmtBytes(upload.loaded)} of ${fmtBytes(upload.total)}`,
      upload,
    };
  }
  if (upload && (upload.phase === "error" || upload.phase === "cancelled")) {
    return { phase: upload.phase, progress: 0, label: upload.phase === "error" ? "Upload failed" : "Upload cancelled", detail: upload.error ?? "", upload };
  }
  const job =
    (upload?.jobId && jobs?.find((j) => j.id === upload.jobId)) ||
    jobs?.find((j) => j.case_id === c.id && j.kind.startsWith("recording") && (j.status === "running" || j.status === "queued"));
  if (c.recording?.status === "processing" || (job && (job.status === "running" || job.status === "queued"))) {
    return {
      phase: "processing",
      progress: job?.progress ?? 0,
      label: "Processing",
      detail: job?.message && job.message !== "Running" ? job.message : "Decoding audio and building the spectrogram",
      job: job || undefined,
      upload,
    };
  }
  if (c.recording?.status === "error") {
    return { phase: "error", progress: 0, label: "Processing failed", detail: c.recording.error ?? "Unknown error" };
  }
  return null;
}

export function UploadProgressCard({ c, view, className }: { c: Case; view: UploadView; className?: string }) {
  const qc = useQueryClient();
  const name = view.upload?.fileName ?? c.recording?.filename ?? "Recording";
  const size = view.upload?.size ?? c.recording?.size_bytes;
  const pct = Math.round(view.progress * 100);
  const failed = view.phase === "error" || view.phase === "cancelled";
  return (
    <div className={cn("flex items-center gap-3 rounded-xl border border-line bg-surface px-3.5 py-3", className)}>
      <div
        className={cn(
          "flex size-9 shrink-0 items-center justify-center rounded-lg border",
          failed ? "border-danger/30 bg-danger/10 text-danger" : "border-line-strong bg-surface-2 text-fg-muted",
        )}
      >
        {failed ? <CircleAlert className="size-4" /> : view.phase === "ready" ? <CheckCircle2 className="size-4 text-ok" /> : <FileAudio className="size-4" />}
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline justify-between gap-3">
          <div className="min-w-0 truncate text-sm font-medium text-fg">
            {name}
            {size != null && <span className="ml-2 text-xs font-normal text-fg-faint tnum">{fmtBytes(size)}</span>}
          </div>
          <div className={cn("shrink-0 text-xs tnum", failed ? "text-danger" : "text-fg-muted")}>
            {view.label}
            {!failed && <span className="ml-1.5 text-fg">{pct}%</span>}
          </div>
        </div>
        {!failed && <ProgressBar className="mt-2" value={view.progress} indeterminate={view.phase === "processing" && view.progress < 0.02} />}
        <div className={cn("mt-1.5 text-xs", failed ? "line-clamp-3 text-danger" : "truncate text-fg-faint")} title={failed ? view.detail : undefined}>
          {view.phase === "uploading" ? `Step 1 of 2 · ${view.detail}` : view.phase === "processing" ? `Step 2 of 2 · ${view.detail}` : view.detail}
        </div>
      </div>
      {view.phase === "uploading" && (
        <IconButton label="Cancel upload" onClick={() => cancelUpload(c.id)}>
          <X />
        </IconButton>
      )}
      {view.phase === "processing" && view.job && (
        <IconButton label="Cancel processing" onClick={() => cancelJob(qc, view.job!.id)}>
          <X />
        </IconButton>
      )}
      {failed && view.upload && (
        <IconButton label="Dismiss" onClick={() => clearUpload(c.id)}>
          <X />
        </IconButton>
      )}
    </div>
  );
}
