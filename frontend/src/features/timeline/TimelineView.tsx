import { useEffect, useRef } from "react";
import { TimelineCanvas, type Handlers, type Scene } from "./TimelineCanvas";

/** React host for the imperative timeline canvas (spectrogram, thunder envelope, ENF trace). */
export function TimelineView({ scene, handlers }: { scene: Scene; handlers: Handlers }) {
  const container = useRef<HTMLDivElement>(null);
  const base = useRef<HTMLCanvasElement>(null);
  const over = useRef<HTMLCanvasElement>(null);
  const readout = useRef<HTMLDivElement>(null);
  const canvas = useRef<TimelineCanvas | null>(null);
  const handlersRef = useRef(handlers);
  handlersRef.current = handlers;

  useEffect(() => {
    const proxy: Handlers = {
      onSelectEvent: (id) => handlersRef.current.onSelectEvent(id),
      onBox: (b) => handlersRef.current.onBox(b),
      onAddMarker: (k, t) => handlersRef.current.onAddMarker(k, t),
      onMoveMarker: (k, id, t) => handlersRef.current.onMoveMarker(k, id, t),
      onSelectMarker: (k, id) => handlersRef.current.onSelectMarker(k, id),
    };
    const c = new TimelineCanvas(container.current!, base.current!, over.current!, readout.current!, scene, proxy);
    canvas.current = c;
    return () => {
      c.dispose();
      canvas.current = null;
    };
    // the canvas is created once; scene updates go through setScene below
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    canvas.current?.setScene(scene);
  }, [scene]);

  return (
    <div ref={container} className="absolute inset-0 overflow-hidden select-none" data-testid="timeline">
      <canvas ref={base} className="absolute" style={{ imageRendering: "auto" }} />
      <canvas ref={over} className="absolute inset-0 touch-none" />
      <div
        ref={readout}
        className="pointer-events-none absolute top-2 left-[58px] z-10 flex items-center gap-1.5 rounded-md border border-white/10 bg-black/70 px-2 py-1 text-[11px] text-white/80 opacity-0 backdrop-blur-sm transition-opacity duration-75 tnum [&_.sep]:text-white/30 [&_b]:font-semibold [&_b]:text-white"
      />
    </div>
  );
}
