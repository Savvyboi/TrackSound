import { CheckCircle2, CircleAlert, Info, TriangleAlert, X } from "lucide-react";
import { useEffect } from "react";
import { createStore } from "../../lib/store";
import { cn } from "../../lib/cn";

export type ToastTone = "success" | "error" | "info" | "warning";
export type Toast = {
  id: number;
  tone: ToastTone;
  title: string;
  body?: string;
  action?: { label: string; onClick: () => void };
  timeout: number;
};

const toasts = createStore<{ list: Toast[] }>({ list: [] });
let nextId = 1;

export function toast(t: Omit<Toast, "id" | "timeout"> & { timeout?: number }): number {
  const id = nextId++;
  const timeout = t.timeout ?? (t.tone === "error" ? 9000 : 5000);
  toasts.set((s) => ({ list: [...s.list.slice(-4), { ...t, id, timeout }] }));
  return id;
}

export function dismissToast(id: number) {
  toasts.set((s) => ({ list: s.list.filter((t) => t.id !== id) }));
}

const ICON = {
  success: <CheckCircle2 className="size-4 text-ok" />,
  error: <CircleAlert className="size-4 text-danger" />,
  info: <Info className="size-4 text-info" />,
  warning: <TriangleAlert className="size-4 text-warn" />,
};

function ToastItem({ t }: { t: Toast }) {
  useEffect(() => {
    const h = setTimeout(() => dismissToast(t.id), t.timeout);
    return () => clearTimeout(h);
  }, [t.id, t.timeout]);
  return (
    <div
      role="status"
      className={cn(
        "pointer-events-auto flex w-[340px] items-start gap-2.5 rounded-lg border border-line-strong bg-surface px-3 py-2.5 shadow-pop animate-slide-up",
      )}
    >
      <div className="mt-px shrink-0">{ICON[t.tone]}</div>
      <div className="min-w-0 flex-1">
        <div className="text-sm font-medium text-fg">{t.title}</div>
        {t.body && <div className="mt-0.5 text-xs break-words text-fg-muted">{t.body}</div>}
        {t.action && (
          <button
            className="mt-1.5 text-xs font-medium text-accent hover:text-accent-strong"
            onClick={() => {
              t.action!.onClick();
              dismissToast(t.id);
            }}
          >
            {t.action.label}
          </button>
        )}
      </div>
      <button
        aria-label="Dismiss"
        className="-mr-1 rounded p-0.5 text-fg-faint hover:bg-surface-3 hover:text-fg"
        onClick={() => dismissToast(t.id)}
      >
        <X className="size-3.5" />
      </button>
    </div>
  );
}

export function Toaster() {
  const list = toasts.use((s) => s.list);
  return (
    <div className="pointer-events-none fixed right-4 bottom-4 z-[100] flex flex-col items-end gap-2">
      {list.map((t) => (
        <ToastItem key={t.id} t={t} />
      ))}
    </div>
  );
}
