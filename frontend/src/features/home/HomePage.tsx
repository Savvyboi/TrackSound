import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  ArrowRight,
  AudioWaveform,
  CircleAlert,
  FilePlus2,
  FolderOpen,
  Moon,
  Plane,
  Search,
  Sun,
  Trash2,
  Zap,
} from "lucide-react";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, errorMessage } from "../../api/client";
import { qk, setCaseData, useCases, useHealth } from "../../api/queries";
import type { Case } from "../../api/types";
import { Brand } from "../../components/Brand";
import { SourceBadge } from "../../components/SourceBadge";
import { Button, IconButton } from "../../components/ui/Button";
import { Input } from "../../components/ui/form";
import { Badge, EmptyState, ErrorNote, ProgressBar, Skeleton } from "../../components/ui/misc";
import { confirmDialog } from "../../components/ui/overlay";
import { toast } from "../../components/ui/toast";
import { cn } from "../../lib/cn";
import { fmtAgo, fmtBytes, fmtDuration } from "../../lib/format";
import { fmtBBoxShort } from "../../lib/geo";
import { toggleTheme, useTheme } from "../../lib/theme";
import { browserTimeZone, fmtClock, fmtDatePretty, fmtDateTime } from "../../lib/tz";
import { DropZone } from "./DropZone";
import { UploadProgressCard, useUploadView } from "./UploadProgress";
import { caseNameFromFile, startUpload, uploadsStore } from "./uploads";

export function HomePage() {
  const qc = useQueryClient();
  const nav = useNavigate();
  const cases = useCases();
  const health = useHealth();
  const theme = useTheme();
  const [filter, setFilter] = useState("");
  const uploading = uploadsStore.use((s) => s.byCase);

  const create = useMutation({
    mutationFn: (name: string) => api.post<Case>("/api/cases", { name }),
    onSuccess: (c) => setCaseData(qc, c),
  });

  const onFile = async (file: File) => {
    try {
      const c = await create.mutateAsync(caseNameFromFile(file.name));
      void startUpload(qc, c.id, file);
    } catch (e) {
      toast({ tone: "error", title: "Could not create the case", body: errorMessage(e) });
    }
  };

  const newEmpty = async () => {
    try {
      const c = await create.mutateAsync("Untitled case");
      nav(`/cases/${c.id}`);
    } catch (e) {
      toast({ tone: "error", title: "Could not create the case", body: errorMessage(e) });
    }
  };

  const list = cases.data ?? [];
  const shown = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return list;
    return list.filter((c) => c.name.toLowerCase().includes(q) || (c.recording?.filename ?? "").toLowerCase().includes(q));
  }, [list, filter]);

  const active = list.filter((c) => uploading[c.id] || c.recording?.status === "processing");

  return (
    <div className="flex h-full flex-col overflow-y-auto">
      <header className="sticky top-0 z-20 border-b border-line bg-bg/85 backdrop-blur-md">
        <div className="mx-auto flex h-14 max-w-[1180px] items-center gap-4 px-6">
          <Brand />
          <div className="flex-1" />
          <ServerStatus ok={!!health.data?.ok} error={health.error} version={health.data?.version} dataDir={health.data?.data_dir} loading={health.isLoading} />
          <IconButton label={theme === "dark" ? "Light theme" : "Dark theme"} onClick={toggleTheme}>
            {theme === "dark" ? <Sun /> : <Moon />}
          </IconButton>
        </div>
      </header>

      <main className="mx-auto w-full max-w-[1180px] flex-1 px-6 pt-10 pb-16">
        <section className="mb-8 grid grid-cols-[1fr_auto] items-end gap-8">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight text-fg">Where and when was it recorded?</h1>
            <p className="mt-2 max-w-[640px] text-base leading-relaxed text-fg-muted">
              TrackSound matches faint sounds in a recording against public records of events at known places and times, and
              estimates where it was made and when it started.
            </p>
          </div>
          <div className="flex flex-col gap-1.5 text-xs text-fg-muted">
            <SourceLine icon={<Plane />} color="var(--src-aircraft)" title="Aircraft" text="Propeller Doppler sweeps vs ADS-B tracks" />
            <SourceLine icon={<Zap />} color="var(--src-lightning)" title="Lightning" text="Thunder delays vs lightning catalogues" />
            <SourceLine icon={<AudioWaveform />} color="var(--src-enf)" title="Mains hum" text="Grid frequency wobble vs ENF records" />
          </div>
        </section>

        <DropZone onFile={onFile} disabled={create.isPending}>
          <div className="mt-1 flex items-center gap-2">
            <Button variant="ghost" size="sm" icon={<FilePlus2 />} onClick={newEmpty} loading={create.isPending && false}>
              Create an empty case instead
            </Button>
          </div>
        </DropZone>

        {active.length > 0 && (
          <div className="mt-4 flex flex-col gap-2">
            {active.map((c) => (
              <ActiveUpload key={c.id} c={c} onOpen={() => nav(`/cases/${c.id}`)} />
            ))}
          </div>
        )}

        <section className="mt-10">
          <div className="mb-3 flex items-center gap-3">
            <h2 className="text-base font-semibold text-fg">Cases</h2>
            {list.length > 0 && <span className="text-sm text-fg-faint tnum">{list.length}</span>}
            <div className="flex-1" />
            {list.length > 4 && (
              <Input
                className="w-[240px]"
                prefixIcon={<Search />}
                placeholder="Filter cases"
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
              />
            )}
          </div>

          {cases.isLoading ? (
            <div className="overflow-hidden rounded-xl border border-line bg-surface">
              {[0, 1, 2].map((i) => (
                <div key={i} className="flex items-center gap-6 border-b border-line px-4 py-4 last:border-0">
                  <Skeleton className="h-4 w-48" />
                  <Skeleton className="h-3 w-16" />
                  <Skeleton className="h-3 w-24" />
                  <Skeleton className="ml-auto h-3 w-20" />
                </div>
              ))}
            </div>
          ) : cases.error ? (
            <ErrorNote error={cases.error} />
          ) : list.length === 0 ? (
            <div className="rounded-xl border border-line bg-surface">
              <EmptyState icon={<FolderOpen />} title="No cases yet">
                Upload a recording above to begin. Each case keeps one recording and everything you work out about it.
              </EmptyState>
            </div>
          ) : (
            <CasesTable cases={shown} />
          )}
        </section>
      </main>

      <footer className="border-t border-line">
        <div className="mx-auto flex max-w-[1180px] flex-wrap items-center gap-x-5 gap-y-1 px-6 py-4 text-xs text-fg-faint">
          <span>Runs locally on this computer. No accounts, nothing uploaded elsewhere.</span>
          {health.data?.data_dir && (
            <span>
              Data folder <code className="font-mono text-2xs text-fg-muted">{health.data.data_dir}</code>
            </span>
          )}
          <span className="flex-1" />
          <a className="hover:text-fg" href="/api/docs" target="_blank" rel="noreferrer">
            API docs
          </a>
          <span>Flight data © adsb.lol (ODbL)</span>
        </div>
      </footer>
    </div>
  );
}

function SourceLine({ icon, color, title, text }: { icon: React.ReactNode; color: string; title: string; text: string }) {
  return (
    <div className="flex items-center gap-2">
      <span className="flex size-5 items-center justify-center rounded-md border border-line bg-surface-2 [&_svg]:size-3" style={{ color }}>
        {icon}
      </span>
      <span className="w-[68px] font-medium text-fg">{title}</span>
      <span>{text}</span>
    </div>
  );
}

function ServerStatus({
  ok,
  error,
  version,
  dataDir,
  loading,
}: {
  ok: boolean;
  error: unknown;
  version?: string;
  dataDir?: string;
  loading: boolean;
}) {
  if (loading) return null;
  return (
    <div
      className="flex items-center gap-2 rounded-full border border-line bg-surface px-2.5 py-1 text-xs text-fg-muted"
      data-tip={ok ? `Connected to the local TrackSound server${dataDir ? ` · data in ${dataDir}` : ""}` : errorMessage(error)}
    >
      <span className={cn("size-1.5 rounded-full", ok ? "bg-ok shadow-[0_0_0_3px_color-mix(in_oklab,var(--ok)_20%,transparent)]" : "bg-danger")} />
      {ok ? `Server v${version}` : "Server offline"}
    </div>
  );
}

function ActiveUpload({ c, onOpen }: { c: Case; onOpen: () => void }) {
  const view = useUploadView(c);
  if (!view) return null;
  return (
    <div className="flex items-center gap-2">
      <UploadProgressCard c={c} view={view} className="flex-1" />
      <Button variant="secondary" size="lg" iconRight={<ArrowRight />} onClick={onOpen} className="h-[66px]">
        Open
      </Button>
    </div>
  );
}

function CasesTable({ cases }: { cases: Case[] }) {
  const nav = useNavigate();
  const qc = useQueryClient();
  const del = async (c: Case) => {
    const ok = await confirmDialog({
      title: `Delete “${c.name}”?`,
      body: "The recording, spectrogram and all results in this case are removed from the data folder. This can't be undone.",
      confirmLabel: "Delete case",
      danger: true,
    });
    if (!ok) return;
    try {
      await api.del(`/api/cases/${encodeURIComponent(c.id)}`);
      qc.setQueryData<Case[]>(qk.cases, (l) => (l ?? []).filter((x) => x.id !== c.id));
      toast({ tone: "success", title: "Case deleted", body: c.name });
    } catch (e) {
      toast({ tone: "error", title: "Could not delete the case", body: errorMessage(e) });
    }
  };

  return (
    <div className="overflow-hidden rounded-xl border border-line bg-surface">
      <div className="grid grid-cols-[minmax(220px,2.2fr)_90px_110px_minmax(180px,1.6fr)_minmax(150px,1.2fr)_100px_40px] items-center gap-4 border-b border-line bg-surface-2/50 px-4 py-2 text-2xs font-semibold tracking-wider text-fg-faint uppercase">
        <span>Case</span>
        <span>Duration</span>
        <span>Date (UTC)</span>
        <span>Start time</span>
        <span>Area</span>
        <span>Updated</span>
        <span />
      </div>
      {cases.map((c) => (
        <CaseRow key={c.id} c={c} onOpen={() => nav(`/cases/${c.id}`)} onDelete={() => del(c)} />
      ))}
    </div>
  );
}

function CaseRow({ c, onOpen, onDelete }: { c: Case; onOpen: () => void; onDelete: () => void }) {
  const view = useUploadView(c);
  const tz = c.timezone || browserTimeZone();
  const rec = c.recording;
  return (
    <div
      role="link"
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={(e) => e.key === "Enter" && onOpen()}
      className="group grid cursor-pointer grid-cols-[minmax(220px,2.2fr)_90px_110px_minmax(180px,1.6fr)_minmax(150px,1.2fr)_100px_40px] items-center gap-4 border-b border-line px-4 py-3 transition-colors last:border-0 hover:bg-surface-2"
    >
      <div className="min-w-0">
        <div className="truncate text-sm font-medium text-fg group-hover:text-accent">{c.name}</div>
        <div className="mt-0.5 flex min-w-0 items-center gap-1.5 text-xs text-fg-faint">
          {rec ? (
            <>
              <span className="truncate">{rec.filename}</span>
              <span className="shrink-0 tnum">· {fmtBytes(rec.size_bytes)}</span>
              {rec.has_video && <Badge className="h-4 px-1 text-[10px]">video</Badge>}
            </>
          ) : (
            <span>No recording yet</span>
          )}
        </div>
      </div>
      <div className="text-sm text-fg-muted tnum">{rec?.duration_s ? fmtDuration(rec.duration_s) : "–"}</div>
      <div className="text-sm text-fg-muted tnum">{c.date ?? <span className="text-fg-faint">not set</span>}</div>
      <div className="min-w-0">
        {view && view.phase !== "ready" ? (
          view.phase === "error" || view.phase === "cancelled" ? (
            <span className="flex items-center gap-1.5 text-xs text-danger">
              <CircleAlert className="size-3.5" /> {view.label}
            </span>
          ) : (
            <div className="flex items-center gap-2">
              <ProgressBar value={view.progress} className="w-20 shrink-0" />
              <span className="text-xs whitespace-nowrap text-fg-muted tnum">
                {view.label} {Math.round(view.progress * 100)}%
              </span>
            </div>
          )
        ) : c.start_utc != null ? (
          <div className="flex min-w-0 items-center gap-2">
            <span className="truncate text-sm text-fg tnum" title={fmtDateTime(c.start_utc, tz)}>
              {fmtDatePretty(c.start_utc, tz)} {fmtClock(c.start_utc, tz, false)}
            </span>
            <SourceBadge source={c.start_utc_source} />
          </div>
        ) : (
          <span className="text-sm text-fg-faint">Unknown</span>
        )}
      </div>
      <div className="truncate text-sm text-fg-muted tnum">
        {c.countries?.length ? (
          <>
            <span className="text-fg">{c.countries.join(", ")}</span>
            {c.region && <span className="text-fg-faint"> · {fmtBBoxShort(c.region)}</span>}
          </>
        ) : c.region ? (
          fmtBBoxShort(c.region)
        ) : (
          <span className="text-fg-faint">not set</span>
        )}
      </div>
      <div className="text-xs text-fg-faint">{fmtAgo(c.updated_at)}</div>
      <div className="flex justify-end opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
        <IconButton
          label="Delete case"
          onClick={(e) => {
            e.stopPropagation();
            onDelete();
          }}
          className="hover:!text-danger"
        >
          <Trash2 />
        </IconButton>
      </div>
    </div>
  );
}
