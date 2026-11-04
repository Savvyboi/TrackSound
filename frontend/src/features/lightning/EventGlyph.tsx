import { Zap } from "lucide-react";
import { EVENT_STYLE, eventIcon } from "./kinds";

/** Small inline glyph for an event kind (same shapes and colours as the map symbols). */
export function EventGlyph({ kind, className }: { kind: string | null | undefined; className?: string }) {
  const icon = eventIcon(kind);
  if (icon === "lightning") return <Zap className={className ?? "size-3.5 text-src-lightning"} />;
  const color = EVENT_STYLE[icon].color;
  const stroke = { stroke: "rgba(6,8,12,0.85)", strokeWidth: 1.2, strokeLinejoin: "round" as const };
  return (
    <svg viewBox="-10 -10 20 20" className={className ?? "size-3.5"} aria-hidden>
      {icon === "blast" && <path d={starPath(8, 9, 4)} fill={color} {...stroke} />}
      {icon === "fireworks" && (
        <>
          <path d={starPath(4, 9, 2.4)} fill={color} {...stroke} />
          <circle cx="6.5" cy="-6.5" r="1.6" fill={color} />
          <circle cx="-6.5" cy="6.5" r="1.3" fill={color} />
        </>
      )}
      {icon === "boom" && <path d="M-7-7 L0 0 L-7 7 M0-7 L7 0 L0 7" fill="none" stroke={color} strokeWidth={3} strokeLinecap="round" strokeLinejoin="round" />}
      {icon === "quake" && (
        <>
          <circle r="8" fill="none" stroke={color} strokeWidth={1.8} />
          <circle r="4.5" fill="none" stroke={color} strokeWidth={1.8} />
          <circle r="1.8" fill={color} />
        </>
      )}
      {icon === "meteor" && (
        <>
          <path d="M-8.5-8.5 L2-2" stroke={color} strokeWidth={2.4} strokeLinecap="round" opacity={0.7} />
          <circle cx="3.5" cy="3.5" r="4.5" fill={color} {...stroke} />
        </>
      )}
      {icon === "event" && <path d="M0-9 L9 0 L0 9 L-9 0 Z" fill={color} {...stroke} />}
    </svg>
  );
}

/** SVG path of an n-pointed star (outer radius ro, inner radius ri). */
export function starPath(n: number, ro: number, ri: number): string {
  const pts: string[] = [];
  for (let i = 0; i < n * 2; i++) {
    const r = i % 2 ? ri : ro;
    const a = (i / (n * 2)) * Math.PI * 2 - Math.PI / 2;
    pts.push(`${(Math.cos(a) * r).toFixed(2)} ${(Math.sin(a) * r).toFixed(2)}`);
  }
  return `M${pts.join(" L")} Z`;
}
