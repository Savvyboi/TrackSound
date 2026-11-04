import { useQueryClient } from "@tanstack/react-query";
import { Activity, Check, Clock3, Database, ExternalLink, FileUp, Globe2, Info, ListOrdered, Play, Trash2, Waves } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { api, errorMessage } from "../../api/client";
import { isActive, useStartJob } from "../../api/jobs";
import { casePath, qk, useCaseJobs, useEnf, useEnfMatch, useEnfReferences, usePatchCase } from "../../api/queries";
import type { EnfMatch, EnfReference } from "../../api/types";
import { Button, IconButton } from "../../components/ui/Button";
import { Field, Input, Segmented, Select, Switch } from "../../components/ui/form";
import { Badge, EmptyState, ErrorNote, Hint, KV, ProgressBar, Section, Skeleton, Stat } from "../../components/ui/misc";
import { confirmDialog, Modal } from "../../components/ui/overlay";
import { toast } from "../../components/ui/toast";
import { cn } from "../../lib/cn";
import { fmtAgo, fmtDuration, fmtNum, fmtPct } from "../../lib/format";
import { fmtDateTime, fmtUtc, fromUtcInput, toUtcInput, utcDayRange } from "../../lib/tz";
import { toggleLayer, useWs, ws } from "../../state/workspace";
import { caseTz, recordingReady, useCaseData } from "../workspace/caseContext";

export function EnfTab() {
  const c = useCaseData();
  const enf = useEnf(c.id, recordingReady(c));
  if (enf.error) {
    return (
      <div className="p-4">
        <ErrorNote error={enf.error} what="Mains hum (ENF) analysis" />
      </div>
    );
  }
  return (
    <div>
      <ExtractSection />
      <ReferencesSection />
      <MatchSection />
      <MatchesSection />
    </div>
  );
}

function useEnfJob(word: string) {
  const c = useCaseData();
  return useCaseJobs(c.id).find((j) => isActive(j) && j.kind.startsWith("enf") && j.kind.includes(word));
}

function ExtractSection() {
  const c = useCaseData();
  const enf = useEnf(c.id);
  const startJob = useStartJob();
  const job = useEnfJob("extract");
  const [nominal, setNominal] = useState<"auto" | 50 | 60>("auto");
  const grid = useWs((s) => s.layers.enfGrid);
  const a = enf.data?.analysis ?? null;
  const run = async () => {
    try {
      await startJob(`${casePath(c.id)}/enf/extract`, { nominal });
    } catch (e) {
      toast({ tone: "error", title: "Could not start the extraction", body: errorMessage(e) });
    }
  };
  return (
    <Section title="Mains hum" icon={<Activity />}>
      <p className="-mt-1 mb-3 text-xs leading-relaxed text-fg-muted">
        Electrical hum (50 or 60 Hz and its harmonics) drifts with the power grid's load. That wobble is the same for everyone on one grid, so matching it against grid records gives the date and time, and which grid.
      </p>
      {job ? (
        <div className="rounded-lg border border-accent-line/50 bg-accent-soft px-3 py-2.5">
          <div className="flex items-center justify-between text-xs">
            <span className="font-medium text-accent">Extracting the hum…</span>
            <span className="text-accent tnum">{Math.round(job.progress * 100)}%</span>
          </div>
          <ProgressBar className="mt-1.5" value={job.progress} indeterminate={job.status === "queued"} />
          <div className="mt-1.5 truncate text-2xs text-fg-muted">{job.message}</div>
        </div>
      ) : (
        <div className="flex items-center gap-2">
          <Segmented
            value={nominal}
            onChange={setNominal}
            ariaLabel="Nominal mains frequency"
            options={[
              { value: "auto", label: "Auto" },
              { value: 50, label: "50 Hz" },
              { value: 60, label: "60 Hz" },
            ]}
          />
          <div className="flex-1" />
          <Button size="sm" variant={a ? "secondary" : "primary"} icon={<Waves />} onClick={run} disabled={!recordingReady(c)}>
            {a ? "Extract again" : "Extract"}
          </Button>
        </div>
      )}

      {enf.isLoading ? (
        <Skeleton className="mt-3 h-16" />
      ) : a ? (
        <div className="mt-3 flex flex-col gap-3">
          <div className="grid grid-cols-3 gap-2">
            <Stat label="Mains" value={a.nominal_hz ? `${a.nominal_hz} Hz` : "none"} sub={a.nominal_hz ? (a.nominal_hz === 50 ? "Europe, Asia, Africa…" : "Americas, parts of Asia") : "no hum found"} />
            <Stat label="Confidence" value={fmtPct(a.confidence)} />
            <Stat label="Usable" value={fmtPct(a.quality)} sub="of the trace" />
          </div>
          {a.harmonics.length > 0 && (
            <div>
              <div className="mb-1 grid grid-cols-[28px_1fr_70px_36px] gap-2 text-[10px] font-semibold tracking-wider text-fg-faint uppercase">
                <span>n</span>
                <span>Frequency</span>
                <span className="text-right">SNR</span>
                <span className="text-right">Used</span>
              </div>
              {a.harmonics.map((h) => (
                <div key={h.n} className={cn("grid grid-cols-[28px_1fr_70px_36px] gap-2 py-0.5 text-xs tnum", !h.used && "text-fg-faint")}>
                  <span className="font-semibold">{h.n}×</span>
                  <span>{fmtNum(h.freq_hz, 3)} Hz</span>
                  <span className="text-right">{fmtNum(h.snr_db, 1)} dB</span>
                  <span className="flex justify-end">{h.used ? <Check className="size-3.5 text-ok" /> : "–"}</span>
                </div>
              ))}
            </div>
          )}
          {a.notes.length > 0 && (
            <ul className="flex flex-col gap-1">
              {a.notes.map((n, i) => (
                <li key={i} className="flex items-start gap-1.5 text-2xs leading-snug text-fg-muted">
                  <Info className="mt-px size-3 shrink-0 text-info" />
                  {n}
                </li>
              ))}
            </ul>
          )}
          <Hint icon={<Activity />}>The timeline above shows the hum's frequency over the recording.</Hint>
        </div>
      ) : (
        <EmptyState compact icon={<Waves />} title="Not extracted yet" className="mt-2">
          Extract the hum to see whether the recording carries mains frequency, and at 50 or 60 Hz.
        </EmptyState>
      )}
      <div className="mt-3 border-t border-line pt-3">
        <Switch
          checked={grid}
          onChange={(v) => toggleLayer("enfGrid", v)}
          label={
            <span className="flex items-center gap-1.5">
              <Globe2 className="size-3.5 text-fg-faint" /> Show the mains frequency map
            </span>
          }
          hint={a?.nominal_hz ? `Countries on ${a.nominal_hz} Hz grids are highlighted` : "Countries coloured by 50/60 Hz"}
        />
      </div>
    </Section>
  );
}

function ReferencesSection() {
  const refs = useEnfReferences();
  const [importing, setImporting] = useState(false);
  return (
    <Section
      title="Grid references"
      icon={<Database />}
      actions={
        <Button size="xs" variant="ghost" icon={<FileUp />} onClick={() => setImporting(true)}>
          Import
        </Button>
      }
    >
      {refs.isLoading ? (
        <Skeleton className="h-12" />
      ) : refs.error ? (
        <ErrorNote error={refs.error} what="ENF references" />
      ) : !refs.data?.length ? (
        <EmptyState compact icon={<Database />} title="No references">
          Import a grid-frequency CSV (columns <code className="font-mono">time,frequency</code>) to match against.
        </EmptyState>
      ) : (
        <div className="flex flex-col gap-1.5">
          {refs.data.map((r) => (
            <RefRow key={r.id} r={r} />
          ))}
        </div>
      )}
      <ImportRefDialog open={importing} onClose={() => setImporting(false)} />
    </Section>
  );
}

function RefRow({ r }: { r: EnfReference }) {
  const cov = r.coverage;
  return (
    <div className="rounded-md border border-line bg-surface-2/50 px-2.5 py-2">
      <div className="flex items-center gap-1.5">
        <span className="min-w-0 flex-1 truncate text-xs font-medium text-fg">{r.name}</span>
        <Badge tone={r.nominal_hz === 50 ? "info" : "enf"}>{r.nominal_hz} Hz</Badge>
        <Badge>{r.source}</Badge>
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-x-2 text-2xs text-fg-muted tnum">
        <span>{r.grid}</span>
        <span className="text-fg-faint">·</span>
        <span>{r.resolution_s} s steps</span>
        <span className="text-fg-faint">·</span>
        <span>{cov ? (cov.length ? `${fmtUtc(cov[0].t0_utc, false)} → ${fmtUtc(cov[cov.length - 1].t1_utc, false)}` : "no data") : "fetched on demand"}</span>
      </div>
      {(r.licence || r.url) && (
        <div className="mt-1 flex items-center gap-2 text-[10px] text-fg-faint">
          {r.licence && <span>{r.licence}</span>}
          {r.url && (
            <a href={r.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-0.5 hover:text-fg">
              source <ExternalLink className="size-2.5" />
            </a>
          )}
        </div>
      )}
    </div>
  );
}

function ImportRefDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [grid, setGrid] = useState("");
  const [nominal, setNominal] = useState<50 | 60>(50);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const submit = async () => {
    if (!file) return;
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append("file", file, file.name);
      fd.append("name", name || file.name.replace(/\.[^.]+$/, ""));
      fd.append("grid", grid || "Unknown");
      fd.append("nominal_hz", String(nominal));
      await api.form<EnfReference>("/api/enf/references/import", fd);
      void qc.invalidateQueries({ queryKey: qk.enfRefs });
      toast({ tone: "success", title: "Reference imported", body: name || file.name });
      onClose();
      setFile(null);
      setName("");
      setGrid("");
    } catch (e) {
      toast({ tone: "error", title: "Import failed", body: errorMessage(e) });
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal open={open} onClose={onClose} labelledBy="imp-title">
      <div className="p-5">
        <h2 id="imp-title" className="text-base font-semibold text-fg">
          Import a grid frequency record
        </h2>
        <p className="mt-1 text-xs leading-relaxed text-fg-muted">
          CSV with columns <code className="font-mono">time</code> (ISO-8601 or unix seconds) and <code className="font-mono">frequency</code> (Hz).
        </p>
        <div className="mt-4 flex flex-col gap-3">
          <Field label="File">
            <div className="flex items-center gap-2">
              <Button size="sm" variant="secondary" icon={<FileUp />} onClick={() => input.current?.click()}>
                Choose CSV…
              </Button>
              <span className="truncate text-xs text-fg-muted">{file?.name ?? "No file chosen"}</span>
            </div>
            <input ref={input} type="file" accept=".csv,text/csv" className="hidden" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          </Field>
          <Field label="Name">
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. National Grid ESO, Sept 2026" />
          </Field>
          <div className="grid grid-cols-[1fr_auto] gap-3">
            <Field label="Grid">
              <Input value={grid} onChange={(e) => setGrid(e.target.value)} placeholder="e.g. Great Britain" />
            </Field>
            <Field label="Nominal">
              <Segmented value={nominal} onChange={setNominal} options={[{ value: 50, label: "50 Hz" }, { value: 60, label: "60 Hz" }]} />
            </Field>
          </div>
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" disabled={!file} loading={busy} onClick={submit}>
            Import
          </Button>
        </div>
      </div>
    </Modal>
  );
}

function MatchSection() {
  const c = useCaseData();
  const enf = useEnf(c.id);
  const refs = useEnfReferences();
  const startJob = useStartJob();
  const job = useEnfJob("match");
  const nominal = enf.data?.analysis?.nominal_hz ?? null;
  const usable = useMemo(() => (refs.data ?? []).filter((r) => !nominal || r.nominal_hz === nominal), [refs.data, nominal]);
  const [refId, setRefId] = useState("");
  useEffect(() => {
    if (!usable.find((r) => r.id === refId)) setRefId(usable[0]?.id ?? "");
  }, [usable, refId]);
  const day = c.date ? utcDayRange(c.date) : null;
  const def: [number, number] | null = c.start_utc != null ? [c.start_utc - 6 * 3600, c.start_utc + 6 * 3600] : day;
  const [t0, setT0] = useState(def ? toUtcInput(def[0]) : "");
  const [t1, setT1] = useState(def ? toUtcInput(def[1]) : "");
  const defKey = def ? `${def[0]}-${def[1]}` : "";
  useEffect(() => {
    if (def) {
      setT0(toUtcInput(def[0]));
      setT1(toUtcInput(def[1]));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [defKey]);
  const a = t0 ? fromUtcInput(t0) : null;
  const b = t1 ? fromUtcInput(t1) : null;
  const bad = a != null && b != null && b <= a;
  const blockers: string[] = [];
  if (!enf.data?.analysis) blockers.push("extract the hum");
  else if (!nominal) blockers.push("no mains hum was found");
  if (!refId) blockers.push("choose a reference");
  if (a == null || b == null || bad) blockers.push("set the UTC window");

  const run = async () => {
    try {
      await startJob(
        `${casePath(c.id)}/enf/match`,
        { reference_id: refId, t0_utc: a, t1_utc: b },
        {
          success: () => "Match ready",
          onDone: (j) => {
            const mid = j.result?.match_id;
            if (mid) ws.set({ matchId: mid });
          },
        },
      );
    } catch (e) {
      toast({ tone: "error", title: "Could not start the match", body: errorMessage(e) });
    }
  };

  return (
    <Section title="Match against a grid" icon={<Play />}>
      <div className="flex flex-col gap-2.5">
        <Field label="Reference" hint={nominal ? `Showing ${nominal} Hz grids` : undefined}>
          <Select value={refId} onChange={(e) => setRefId(e.target.value)} disabled={!usable.length}>
            {usable.length === 0 && <option value="">No matching references</option>}
            {usable.map((r) => (
              <option key={r.id} value={r.id}>
                {r.name} · {r.grid}
              </option>
            ))}
          </Select>
        </Field>
        <div className="flex flex-col gap-1.5">
          <span className="text-xs font-medium text-fg-muted">Start time window</span>
          <div className="grid grid-cols-[44px_1fr] items-center gap-x-2 gap-y-1.5">
            <span className="text-xs text-fg-muted">From</span>
            <Input type="datetime-local" step={60} value={t0} onChange={(e) => setT0(e.target.value)} aria-label="Window from (UTC)" suffix="UTC" />
            <span className="text-xs text-fg-muted">To</span>
            <Input type="datetime-local" step={60} value={t1} onChange={(e) => setT1(e.target.value)} aria-label="Window to (UTC)" suffix="UTC" />
          </div>
        </div>
        {a != null && b != null && !bad && <div className="text-2xs text-fg-faint">The recording's start is searched within this {fmtDuration(b - a)} window.</div>}
        {job ? (
          <div className="rounded-lg border border-accent-line/50 bg-accent-soft px-3 py-2.5">
            <div className="flex items-center justify-between text-xs">
              <span className="font-medium text-accent">Matching…</span>
              <span className="text-accent tnum">{Math.round(job.progress * 100)}%</span>
            </div>
            <ProgressBar className="mt-1.5" value={job.progress} indeterminate={job.status === "queued"} />
            <div className="mt-1.5 truncate text-2xs text-fg-muted">{job.message}</div>
          </div>
        ) : (
          <>
            {blockers.length > 0 && <Hint>To run: {blockers.join(", ")}.</Hint>}
            <Button variant="primary" icon={<Play />} disabled={blockers.length > 0} onClick={run}>
              Find the start time
            </Button>
          </>
        )}
      </div>
    </Section>
  );
}

function MatchesSection() {
  const c = useCaseData();
  const qc = useQueryClient();
  const enf = useEnf(c.id);
  const refs = useEnfReferences();
  const matchId = useWs((s) => s.matchId);
  const matches = enf.data?.matches ?? [];
  const refName = (id: string) => refs.data?.find((r) => r.id === id)?.name ?? id;
  useEffect(() => {
    if (!matchId && matches.length) ws.set({ matchId: matches[0].id });
  }, [matchId, matches]);
  const del = async (m: EnfMatch) => {
    const ok = await confirmDialog({ title: "Delete this match?", confirmLabel: "Delete", danger: true });
    if (!ok) return;
    try {
      await api.del(`${casePath(c.id)}/enf/matches/${encodeURIComponent(m.id)}`);
      if (ws.get().matchId === m.id) ws.set({ matchId: null });
      void qc.invalidateQueries({ queryKey: qk.enf(c.id) });
    } catch (e) {
      toast({ tone: "error", title: "Could not delete the match", body: errorMessage(e) });
    }
  };
  if (!matches.length) return null;
  return (
    <Section title="Matches" icon={<ListOrdered />}>
      <div className="mb-2 flex flex-col gap-1">
        {matches.map((m) => (
          <div
            key={m.id}
            role="button"
            tabIndex={0}
            onClick={() => ws.set({ matchId: m.id })}
            className={cn("group flex items-center gap-2 rounded-md border px-2.5 py-1.5 text-xs", matchId === m.id ? "border-accent-line/60 bg-accent-soft" : "border-line hover:border-line-strong")}
          >
            <span className="min-w-0 flex-1 truncate text-fg">{refName(m.reference_id)}</span>
            <span className="text-fg-muted tnum">{m.best ? `r ${m.best.corr.toFixed(2)} · z ${m.best.z.toFixed(1)}` : "no match"}</span>
            <span className="text-2xs text-fg-faint">{fmtAgo(m.created_at)}</span>
            <IconButton
              size="xs"
              label="Delete"
              className="opacity-0 group-hover:opacity-70 hover:!text-danger"
              onClick={(e) => {
                e.stopPropagation();
                void del(m);
              }}
            >
              <Trash2 />
            </IconButton>
          </div>
        ))}
      </div>
      {matchId && <MatchDetail id={matchId} />}
    </Section>
  );
}

function MatchDetail({ id }: { id: string }) {
  const c = useCaseData();
  const tz = caseTz(c);
  const q = useEnfMatch(c.id, id);
  const patch = usePatchCase(c.id);
  if (q.isLoading) return <Skeleton className="h-20" />;
  if (q.error) return <ErrorNote error={q.error} />;
  const m = q.data;
  if (!m) return null;
  const b = m.best;
  const same = b && c.start_utc != null && Math.abs(c.start_utc - b.start_utc) < 0.5 && c.start_utc_source === "enf";
  const strong = b && b.z >= 5;
  return (
    <div className="rounded-lg border border-line bg-surface p-2.5 animate-fade-in">
      {b ? (
        <>
          <div className="text-2xs font-semibold tracking-wider text-fg-faint uppercase">Best start time</div>
          <div className="mt-0.5 text-sm font-semibold text-fg tnum">{fmtDateTime(b.start_utc, tz)}</div>
          <div className="text-2xs text-fg-muted tnum">{fmtUtc(b.start_utc)}</div>
          <div className="mt-2 grid grid-cols-2 gap-2">
            <Stat label="Correlation" value={b.corr.toFixed(3)} />
            <Stat label="Z-score" value={b.z.toFixed(1)} sub={strong ? "clear peak" : "weak peak: treat with care"} />
          </div>
          {m.top.length > 1 && (
            <div className="mt-2">
              <div className="mb-1 text-2xs font-semibold tracking-wider text-fg-faint uppercase">Alternatives</div>
              {m.top.slice(0, 6).map((t, i) => (
                <div key={i} className="flex justify-between py-0.5 text-xs text-fg-muted tnum">
                  <span>{fmtDateTime(t.start_utc, tz)}</span>
                  <span>r {t.corr.toFixed(3)}</span>
                </div>
              ))}
            </div>
          )}
          {m.overlay && <Hint className="mt-2">The timeline shows the recording's trace over the reference, aligned at this start time.</Hint>}
          <Button
            size="sm"
            className="mt-2.5"
            variant={same ? "ghost" : "secondary"}
            icon={<Clock3 />}
            disabled={!!same}
            onClick={() =>
              patch.mutate(
                { start_utc: b.start_utc, start_utc_source: "enf" },
                {
                  onSuccess: () => toast({ tone: "success", title: "Start time set from mains hum", body: fmtDateTime(b.start_utc, tz) }),
                  onError: (e) => toast({ tone: "error", title: "Could not set the start time", body: errorMessage(e) }),
                },
              )
            }
          >
            {same ? "Is the case start time" : "Use as case start time"}
          </Button>
        </>
      ) : (
        <div className="text-xs text-fg-muted">The hum didn't match this reference anywhere in the window.</div>
      )}
      <KV label="Window" className="mt-2">
        {fmtUtc(m.t0_utc, false)} → {fmtUtc(m.t1_utc, false)}
      </KV>
      {m.notes.length > 0 && (
        <ul className="mt-2 flex flex-col gap-1 border-t border-line pt-2">
          {m.notes.map((n, i) => (
            <li key={i} className="flex items-start gap-1.5 text-2xs leading-snug text-fg-muted">
              <Info className="mt-px size-3 shrink-0 text-info" />
              {n}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
