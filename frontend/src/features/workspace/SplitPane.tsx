import { useCallback, useRef, useState, type ReactNode } from "react";
import { cn } from "../../lib/cn";
import { clamp } from "../../lib/format";
import { loadPref, savePref } from "../../lib/store";

/** Two panes stacked vertically with a draggable divider (ratio is remembered). */
export function SplitV({ top, bottom, storageKey, initial = 0.56, minPx = 140 }: { top: ReactNode; bottom: ReactNode; storageKey: string; initial?: number; minPx?: number }) {
  const [ratio, setRatio] = useState(() => loadPref<number>(storageKey, initial));
  const box = useRef<HTMLDivElement>(null);
  const [dragging, setDragging] = useState(false);
  const onDown = useCallback(
    (e: React.PointerEvent) => {
      e.preventDefault();
      (e.target as HTMLElement).setPointerCapture(e.pointerId);
      setDragging(true);
    },
    [],
  );
  const onMove = (e: React.PointerEvent) => {
    if (!dragging || !box.current) return;
    const r = box.current.getBoundingClientRect();
    const minR = minPx / r.height;
    setRatio(clamp((e.clientY - r.top) / r.height, minR, 1 - minR));
  };
  const onUp = () => {
    if (!dragging) return;
    setDragging(false);
    savePref(storageKey, ratio);
  };
  return (
    <div ref={box} className="flex h-full min-h-0 flex-col">
      <div className="min-h-0" style={{ flex: `${ratio} 1 0` }}>
        {top}
      </div>
      <div
        role="separator"
        aria-orientation="horizontal"
        aria-label="Resize panels"
        onPointerDown={onDown}
        onPointerMove={onMove}
        onPointerUp={onUp}
        onDoubleClick={() => {
          setRatio(initial);
          savePref(storageKey, initial);
        }}
        className={cn("group relative z-10 h-[5px] shrink-0 cursor-row-resize bg-line transition-colors", dragging ? "bg-accent" : "hover:bg-accent-line")}
      >
        <div className="absolute inset-x-0 -top-1 -bottom-1" />
        <div className={cn("absolute top-1/2 left-1/2 h-[3px] w-8 -translate-x-1/2 -translate-y-1/2 rounded-full bg-fg-faint/40 transition-opacity", dragging ? "opacity-0" : "opacity-100 group-hover:opacity-0")} />
      </div>
      <div className="min-h-0" style={{ flex: `${1 - ratio} 1 0` }}>
        {bottom}
      </div>
      {dragging && <div className="fixed inset-0 z-50 cursor-row-resize" />}
    </div>
  );
}

/** Main area + right sidebar with a draggable divider (sidebar width is remembered). */
export function SplitSidebar({ main, side, storageKey, initial = 392, min = 330, max = 620 }: { main: ReactNode; side: ReactNode; storageKey: string; initial?: number; min?: number; max?: number }) {
  const [width, setWidth] = useState(() => loadPref<number>(storageKey, initial));
  const [dragging, setDragging] = useState(false);
  const onMove = (e: React.PointerEvent) => {
    if (!dragging) return;
    setWidth(clamp(window.innerWidth - e.clientX, min, Math.min(max, window.innerWidth - 520)));
  };
  return (
    <div className="flex h-full min-h-0">
      <div className="min-w-0 flex-1">{main}</div>
      <div
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize sidebar"
        onPointerDown={(e) => {
          e.preventDefault();
          (e.target as HTMLElement).setPointerCapture(e.pointerId);
          setDragging(true);
        }}
        onPointerMove={onMove}
        onPointerUp={() => {
          setDragging(false);
          savePref(storageKey, width);
        }}
        onDoubleClick={() => {
          setWidth(initial);
          savePref(storageKey, initial);
        }}
        className={cn("relative z-10 w-[5px] shrink-0 cursor-col-resize bg-line transition-colors", dragging ? "bg-accent" : "hover:bg-accent-line")}
      >
        <div className="absolute inset-y-0 -right-1 -left-1" />
      </div>
      <div className="min-h-0 shrink-0" style={{ width }}>
        {side}
      </div>
      {dragging && <div className="fixed inset-0 z-50 cursor-col-resize" />}
    </div>
  );
}
