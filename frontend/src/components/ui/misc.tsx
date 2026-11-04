import { CircleAlert, PlugZap } from "lucide-react";
import type { ReactNode } from "react";
import { errorMessage, isUnavailable } from "../../api/client";
import { cn } from "../../lib/cn";

export function Spinner({ className }: { className?: string }) {
  return (
    <svg className={cn("size-4 animate-spin", className)} viewBox="0 0 24 24" fill="none" aria-hidden>
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeOpacity="0.2" strokeWidth="3" />
      <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("skeleton h-4", className)} />;
}

export function ProgressBar({
  value,
  className,
  tone = "accent",
  indeterminate,
}: {
  value: number | null | undefined;
  className?: string;
  tone?: "accent" | "ok" | "danger" | "warn";
  indeterminate?: boolean;
}) {
  const pct = Math.max(0, Math.min(1, value ?? 0)) * 100;
  const color = { accent: "bg-accent", ok: "bg-ok", danger: "bg-danger", warn: "bg-warn" }[tone];
  return (
    <div className={cn("relative h-1.5 w-full overflow-hidden rounded-full bg-surface-4", className)}>
      {indeterminate ? (
        <div className={cn("absolute inset-y-0 left-0 w-full origin-left animate-indeterminate rounded-full", color)} />
      ) : (
        <div className={cn("h-full rounded-full transition-[width] duration-300 ease-out", color)} style={{ width: `${pct}%` }} />
      )}
    </div>
  );
}

type Tone = "neutral" | "accent" | "ok" | "warn" | "danger" | "info" | "aircraft" | "lightning" | "enf" | "user";

const TONE: Record<Tone, string> = {
  neutral: "bg-surface-3 text-fg-muted border-line-strong",
  accent: "bg-accent-soft text-accent border-accent-line/40",
  ok: "bg-ok/10 text-ok border-ok/25",
  warn: "bg-warn/10 text-warn border-warn/25",
  danger: "bg-danger/10 text-danger border-danger/25",
  info: "bg-info/10 text-info border-info/25",
  aircraft: "bg-src-aircraft/10 text-src-aircraft border-src-aircraft/25",
  lightning: "bg-src-lightning/10 text-src-lightning border-src-lightning/25",
  enf: "bg-src-enf/10 text-src-enf border-src-enf/25",
  user: "bg-src-user/10 text-src-user border-src-user/25",
};

export function Badge({
  tone = "neutral",
  children,
  className,
  dot,
  title,
}: {
  tone?: Tone;
  children: ReactNode;
  className?: string;
  dot?: boolean;
  title?: string;
}) {
  return (
    <span
      title={title}
      className={cn(
        "inline-flex h-5 shrink-0 items-center gap-1 rounded-[5px] border px-1.5 text-2xs font-medium whitespace-nowrap tnum",
        TONE[tone],
        className,
      )}
    >
      {dot && <span className="size-1.5 rounded-full bg-current" />}
      {children}
    </span>
  );
}

export function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd className="inline-flex h-[18px] min-w-[18px] items-center justify-center rounded border border-line-strong bg-surface-2 px-1 font-sans text-2xs font-medium text-fg-muted shadow-[0_1px_0_var(--line-strong)]">
      {children}
    </kbd>
  );
}

export function EmptyState({
  icon,
  title,
  children,
  action,
  className,
  compact,
}: {
  icon?: ReactNode;
  title: string;
  children?: ReactNode;
  action?: ReactNode;
  className?: string;
  compact?: boolean;
}) {
  return (
    <div className={cn("flex flex-col items-center justify-center text-center", compact ? "gap-1.5 px-4 py-5" : "gap-2.5 px-6 py-10", className)}>
      {icon && (
        <div
          className={cn(
            "flex items-center justify-center rounded-xl border border-line bg-surface-2 text-fg-faint",
            compact ? "size-8 [&_svg]:size-4" : "size-11 [&_svg]:size-5",
          )}
        >
          {icon}
        </div>
      )}
      <div className={cn("font-medium text-fg", compact ? "text-sm" : "text-base")}>{title}</div>
      {children && <div className="max-w-[320px] text-xs leading-relaxed text-fg-muted">{children}</div>}
      {action && <div className="mt-1">{action}</div>}
    </div>
  );
}

/** Inline error box. Shows the API's `detail`, or a friendly note for endpoints that don't exist yet. */
export function ErrorNote({ error, className, what }: { error: unknown; className?: string; what?: string }) {
  if (!error) return null;
  if (isUnavailable(error)) return <Unavailable className={className} what={what} />;
  return (
    <div className={cn("flex items-start gap-2 rounded-md border border-danger/25 bg-danger/8 px-2.5 py-2 text-xs text-danger", className)}>
      <CircleAlert className="mt-px size-3.5 shrink-0" />
      <span className="min-w-0 break-words">{errorMessage(error)}</span>
    </div>
  );
}

export function Unavailable({ className, what }: { className?: string; what?: string }) {
  return (
    <div className={cn("flex items-start gap-2.5 rounded-lg border border-dashed border-line-strong bg-surface-2/60 px-3 py-2.5 text-xs text-fg-muted", className)}>
      <PlugZap className="mt-px size-4 shrink-0 text-fg-faint" />
      <div>
        <div className="font-medium text-fg">Not available yet</div>
        <div className="mt-0.5">
          {what ? `${what} isn't` : "This feature isn't"} implemented by the server you're connected to. Update TrackSound and restart{" "}
          <code className="font-mono text-2xs">tracksound serve</code>.
        </div>
      </div>
    </div>
  );
}

export function Section({
  title,
  icon,
  actions,
  children,
  className,
  description,
  id,
}: {
  title: ReactNode;
  icon?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  description?: ReactNode;
  id?: string;
}) {
  return (
    <section id={id} className={cn("border-b border-line px-4 py-4 last:border-b-0", className)}>
      <div className="mb-3 flex min-h-6 items-center gap-2">
        {icon && <span className="text-fg-faint [&_svg]:size-3.5">{icon}</span>}
        <h3 className="flex-1 text-2xs font-semibold tracking-[0.08em] text-fg-muted uppercase">{title}</h3>
        {actions && <div className="flex items-center gap-1">{actions}</div>}
      </div>
      {description && <p className="-mt-1.5 mb-3 text-xs leading-relaxed text-fg-muted">{description}</p>}
      {children}
    </section>
  );
}

export function KV({ label, children, className, mono }: { label: ReactNode; children: ReactNode; className?: string; mono?: boolean }) {
  return (
    <div className={cn("flex items-baseline justify-between gap-3 py-[3px] text-xs", className)}>
      <span className="shrink-0 text-fg-muted">{label}</span>
      <span className={cn("min-w-0 truncate text-right text-fg tnum", mono && "font-mono text-2xs")}>{children}</span>
    </div>
  );
}

export function Stat({ label, value, sub, className }: { label: string; value: ReactNode; sub?: ReactNode; className?: string }) {
  return (
    <div className={cn("min-w-0 rounded-lg border border-line bg-surface-2 px-2.5 py-2", className)}>
      <div className="text-2xs font-medium tracking-wide text-fg-faint uppercase">{label}</div>
      <div className="mt-0.5 truncate text-base font-semibold text-fg tnum">{value}</div>
      {sub && <div className="truncate text-2xs text-fg-muted tnum">{sub}</div>}
    </div>
  );
}

export function Hint({ children, className, icon }: { children: ReactNode; className?: string; icon?: ReactNode }) {
  return (
    <div className={cn("flex items-start gap-2 text-xs leading-relaxed text-fg-muted", className)}>
      {icon && <span className="mt-0.5 shrink-0 text-fg-faint [&_svg]:size-3.5">{icon}</span>}
      <div className="min-w-0">{children}</div>
    </div>
  );
}

export function Dot({ color, className }: { color: string; className?: string }) {
  return <span className={cn("inline-block size-2 shrink-0 rounded-full", className)} style={{ background: color }} />;
}
