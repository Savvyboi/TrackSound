import { useEffect, useState } from "react";

export function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const h = setTimeout(() => setV(value), ms);
    return () => clearTimeout(h);
  }, [value, ms]);
  return v;
}

/** Re-render every `ms` (for "3 min ago" and elapsed timers). */
export function useNow(ms = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const h = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(h);
  }, [ms]);
  return now;
}

/** True when keyboard events should be left alone (typing in a field). */
export function isTypingTarget(t: EventTarget | null): boolean {
  const el = t as HTMLElement | null;
  if (!el) return false;
  const tag = el.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el.isContentEditable;
}

/** Controls where Space toggles state (checkboxes, switches, radios): leave Space to them. */
export function isInteractiveTarget(t: EventTarget | null): boolean {
  const el = t as HTMLElement | null;
  if (!el || !el.closest) return false;
  return !!el.closest('[role="checkbox"], [role="switch"], [role="radio"], [role="option"], [role="slider"], [role="tab"], [role="menuitem"]');
}
