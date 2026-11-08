import { useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, CircleAlert, CircleSlash, Clock, ListChecks, X } from "lucide-react";
import { cancelJob, isActive, jobLabel, resultSummary } from "../../api/jobs";
import { useCaseJobs } from "../../api/queries";
import type { Job } from "../../api/types";
import { IconButton } from "../../components/ui/Button";
import { ProgressBar, Spinner } from "../../components/ui/misc";
import { Popover, usePopover } from "../../components/ui/overlay";
import { cn } from "../../lib/cn";
import { fmtAgo, fmtDuration } from "../../lib/format";
import { useNow } from "../../lib/hooks";

export function JobsIndicator({ caseId }: { caseId: string }) {
  const jobs = useCaseJobs(caseId);
  const active = jobs.filter(isActive);
  const recent = jobs.filter((j) => !isActive(j)).slice(0, 6);
  const pop = usePopover();
  const overall = active.length ? active.reduce((a, j) => a + j.progress, 0) / active.length : 0;

  return (
    <>
      <button
        ref={pop.anchor}
        onClick={pop.toggle}
        data-tip={active.length ? undefined : "Background jobs"}
        className={cn(
          "flex h-8 items-center gap-2 rounded-lg border px-2.5 text-xs font-medium transition-colors",
          active.length ? "border-accent-line bg-accent-soft text-accent" : "border-line bg-surface-2 text-fg-muted hover:text-fg",
          pop.open && "ring-2 ring-accent/20",
        )}
      >
        {active.length ? <Spinner className="size-3.5" /> : <ListChecks className="size-3.5" />}
        {active.length ? (
          <>
            <span className="max-w-[180px] truncate">{active.length === 1 ? jobLabel(active[0].kind) : `${active.length} jobs running`}</span>
            <span className="tnum">{Math.round(overall * 100)}%</span>
            <div className="h-1 w-12 overflow-hidden rounded-full bg-accent/20">
              <div className="h-full rounded-full bg-accent transition-[width] duration-500" style={{ width: `${overall * 100}%` }} />
            </div>
          </>
        ) : (
          <span>Jobs</span>
        )}
      </button>
      <Popover open={pop.open} onClose={() => pop.setOpen(false)} anchor={pop.anchor} width={380}>
        <div className="p-1.5">
          <div className="flex items-center justify-between px-2.5 pt-2 pb-1.5">
            <span className="text-2xs font-semibold tracking-wider text-fg-muted uppercase">Running</span>
            <span className="text-2xs text-fg-faint">{active.length || "none"}</span>
          </div>
          {active.length === 0 && <div className="px-2.5 pb-2 text-xs text-fg-faint">Nothing running. Long operations (detection, downloads, searches) show up here.</div>}
          {active.map((j) => (
            <JobRow key={j.id} job={j} />
          ))}
          {recent.length > 0 && (
            <>
              <div className="mt-1 border-t border-line px-2.5 pt-2.5 pb-1.5 text-2xs font-semibold tracking-wider text-fg-muted uppercase">Recent</div>
              {recent.map((j) => (
                <JobRow key={j.id} job={j} />
              ))}
            </>
          )}
        </div>
      </Popover>
    </>
  );
}

function JobRow({ job }: { job: Job }) {
  const qc = useQueryClient();
  const now = useNow(1000);
  const running = isActive(job);
  const elapsed = job.started_at ? (job.finished_at ?? now / 1000) - job.started_at : 0;
  const icon = running ? (
    job.status === "queued" ? (
      <Clock className="size-3.5 text-fg-faint" />
    ) : (
      <Spinner className="size-3.5 text-accent" />
    )
  ) : job.status === "done" ? (
    <CheckCircle2 className="size-3.5 text-ok" />
  ) : job.status === "error" ? (
    <CircleAlert className="size-3.5 text-danger" />
  ) : (
    <CircleSlash className="size-3.5 text-fg-faint" />
  );
  return (
    <div className="flex items-start gap-2.5 rounded-lg px-2.5 py-2 hover:bg-surface-2">
      <div className="mt-0.5">{icon}</div>
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline justify-between gap-2">
          <span className="truncate text-xs font-medium text-fg">{jobLabel(job.kind)}</span>
          <span className="shrink-0 text-2xs text-fg-faint tnum">
            {running ? (job.status === "queued" ? "queued" : `${Math.round(job.progress * 100)}% · ${fmtDuration(elapsed)}`) : fmtAgo(job.finished_at)}
          </span>
        </div>
        {running && <ProgressBar className="mt-1.5" value={job.progress} indeterminate={job.status === "queued"} />}
        <div className={cn("mt-1 line-clamp-2 text-2xs", job.status === "error" ? "text-danger" : "text-fg-muted")}>
          {job.status === "error"
            ? job.error || job.message
            : job.status === "done"
              ? // the message is the last step ("Saving"): say how it ended instead
                (resultSummary(job) ?? (elapsed > 0 ? `Done in ${fmtDuration(elapsed)}` : "Done"))
              : job.status === "cancelled"
                ? "Cancelled"
                : job.message}
        </div>
      </div>
      {running && (
        <IconButton size="xs" label="Cancel job" onClick={() => cancelJob(qc, job.id)}>
          <X />
        </IconButton>
      )}
    </div>
  );
}
