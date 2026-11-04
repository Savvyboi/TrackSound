import { AlertTriangle } from "lucide-react";
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode, type RefObject } from "react";
import { createPortal } from "react-dom";
import { cn } from "../../lib/cn";
import { createStore } from "../../lib/store";
import { Button } from "./Button";

// ---- tooltips: any element with data-tip="…" (and optional data-tip-side) ---------------

export function GlobalTooltip() {
  const [tip, setTip] = useState<{ text: string; rect: DOMRect; side: string } | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const current = useRef<Element | null>(null);

  useEffect(() => {
    const show = (el: Element) => {
      const text = el.getAttribute("data-tip");
      if (!text) return;
      setTip({ text, rect: el.getBoundingClientRect(), side: el.getAttribute("data-tip-side") || "bottom" });
    };
    const over = (e: PointerEvent) => {
      const el = (e.target as Element | null)?.closest?.("[data-tip]") ?? null;
      if (el === current.current) return;
      current.current = el;
      window.clearTimeout(timer.current);
      setTip(null);
      if (el && el.getAttribute("data-tip")) timer.current = window.setTimeout(() => show(el), 450);
    };
    const hide = () => {
      window.clearTimeout(timer.current);
      current.current = null;
      setTip(null);
    };
    document.addEventListener("pointerover", over);
    document.addEventListener("pointerdown", hide, true);
    window.addEventListener("blur", hide);
    window.addEventListener("wheel", hide, { passive: true });
    return () => {
      document.removeEventListener("pointerover", over);
      document.removeEventListener("pointerdown", hide, true);
      window.removeEventListener("blur", hide);
      window.removeEventListener("wheel", hide);
    };
  }, []);

  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null);
  useLayoutEffect(() => {
    if (!tip || !ref.current) {
      setPos(null);
      return;
    }
    const w = ref.current.offsetWidth;
    const h = ref.current.offsetHeight;
    const r = tip.rect;
    let left = r.left + r.width / 2 - w / 2;
    let top = r.bottom + 6;
    if (tip.side === "top" || (tip.side === "bottom" && top + h > window.innerHeight - 4)) top = r.top - h - 6;
    if (tip.side === "left") {
      left = r.left - w - 6;
      top = r.top + r.height / 2 - h / 2;
    }
    if (tip.side === "right") {
      left = r.right + 6;
      top = r.top + r.height / 2 - h / 2;
    }
    left = Math.max(4, Math.min(window.innerWidth - w - 4, left));
    setPos({ left, top });
  }, [tip]);

  if (!tip) return null;
  return createPortal(
    <div
      ref={ref}
      role="tooltip"
      className="pointer-events-none fixed z-[200] max-w-[280px] rounded-md border border-line-strong bg-surface-4 px-2 py-1 text-2xs leading-snug font-medium text-fg shadow-pop animate-fade-in"
      style={{ left: pos?.left ?? -9999, top: pos?.top ?? -9999 }}
    >
      {tip.text}
    </div>,
    document.body,
  );
}

// ---- popover ----------------------------------------------------------------------------

export function usePopover() {
  const [open, setOpen] = useState(false);
  const anchor = useRef<HTMLButtonElement>(null);
  return { open, setOpen, anchor, toggle: useCallback(() => setOpen((v) => !v), []) };
}

export function Popover({
  open,
  onClose,
  anchor,
  children,
  align = "end",
  className,
  width,
}: {
  open: boolean;
  onClose: () => void;
  anchor: RefObject<HTMLElement | null>;
  children: ReactNode;
  align?: "start" | "end" | "center";
  className?: string;
  width?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ left: number; top: number; maxH: number } | null>(null);

  useLayoutEffect(() => {
    if (!open) return;
    const place = () => {
      const a = anchor.current?.getBoundingClientRect();
      const el = ref.current;
      if (!a || !el) return;
      const w = el.offsetWidth;
      let left = align === "start" ? a.left : align === "end" ? a.right - w : a.left + a.width / 2 - w / 2;
      left = Math.max(8, Math.min(window.innerWidth - w - 8, left));
      const top = a.bottom + 6;
      setPos({ left, top, maxH: window.innerHeight - top - 12 });
    };
    place();
    window.addEventListener("resize", place);
    return () => window.removeEventListener("resize", place);
  }, [open, anchor, align]);

  useEffect(() => {
    if (!open) return;
    const down = (e: MouseEvent) => {
      if (ref.current?.contains(e.target as Node) || anchor.current?.contains(e.target as Node)) return;
      onClose();
    };
    const key = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("mousedown", down);
    window.addEventListener("keydown", key);
    return () => {
      window.removeEventListener("mousedown", down);
      window.removeEventListener("keydown", key);
    };
  }, [open, onClose, anchor]);

  if (!open) return null;
  return createPortal(
    <div
      ref={ref}
      className={cn("fixed z-[80] overflow-auto rounded-xl border border-line-strong bg-surface shadow-pop animate-pop-in", className)}
      style={{ left: pos?.left ?? -9999, top: pos?.top ?? -9999, width, maxHeight: pos?.maxH }}
    >
      {children}
    </div>,
    document.body,
  );
}

// ---- modal dialog -----------------------------------------------------------------------

export function Modal({
  open,
  onClose,
  children,
  className,
  labelledBy,
}: {
  open: boolean;
  onClose: () => void;
  children: ReactNode;
  className?: string;
  labelledBy?: string;
}) {
  useEffect(() => {
    if (!open) return;
    const key = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [open, onClose]);
  if (!open) return null;
  return createPortal(
    <div className="fixed inset-0 z-[150] flex items-center justify-center p-6">
      <div className="absolute inset-0 bg-black/55 backdrop-blur-[2px] animate-fade-in" onClick={onClose} />
      <div
        role="dialog"
        aria-modal
        aria-labelledby={labelledBy}
        className={cn("relative w-full max-w-[420px] rounded-xl border border-line-strong bg-surface shadow-pop animate-pop-in", className)}
      >
        {children}
      </div>
    </div>,
    document.body,
  );
}

type ConfirmRequest = {
  title: string;
  body?: ReactNode;
  confirmLabel?: string;
  danger?: boolean;
  resolve: (v: boolean) => void;
};

const confirmStore = createStore<{ req: ConfirmRequest | null }>({ req: null });

/** Promise-based confirmation dialog. */
export function confirmDialog(opts: Omit<ConfirmRequest, "resolve">): Promise<boolean> {
  return new Promise((resolve) => confirmStore.set({ req: { ...opts, resolve } }));
}

export function ConfirmHost() {
  const req = confirmStore.use((s) => s.req);
  const close = (v: boolean) => {
    req?.resolve(v);
    confirmStore.set({ req: null });
  };
  return (
    <Modal open={!!req} onClose={() => close(false)} labelledBy="confirm-title">
      {req && (
        <div className="p-5">
          <div className="flex gap-3">
            {req.danger && (
              <div className="flex size-9 shrink-0 items-center justify-center rounded-full bg-danger/12 text-danger">
                <AlertTriangle className="size-4" />
              </div>
            )}
            <div className="min-w-0">
              <h2 id="confirm-title" className="text-base font-semibold text-fg">
                {req.title}
              </h2>
              {req.body && <div className="mt-1.5 text-sm leading-relaxed text-fg-muted">{req.body}</div>}
            </div>
          </div>
          <div className="mt-5 flex justify-end gap-2">
            <Button variant="ghost" onClick={() => close(false)}>
              Cancel
            </Button>
            <Button autoFocus variant={req.danger ? "danger" : "primary"} onClick={() => close(true)}>
              {req.confirmLabel ?? "Confirm"}
            </Button>
          </div>
        </div>
      )}
    </Modal>
  );
}
