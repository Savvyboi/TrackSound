import { AudioWaveform, Plane, RadioTower, User, Zap } from "lucide-react";
import type { StartSource } from "../api/types";
import { Badge } from "./ui/misc";

export const SOURCE_META: Record<StartSource, { label: string; tone: "aircraft" | "lightning" | "enf" | "alerts" | "user"; icon: typeof Plane; color: string }> = {
  user: { label: "entered", tone: "user", icon: User, color: "var(--src-user)" },
  aircraft: { label: "aircraft", tone: "aircraft", icon: Plane, color: "var(--src-aircraft)" },
  lightning: { label: "lightning", tone: "lightning", icon: Zap, color: "var(--src-lightning)" },
  enf: { label: "mains hum", tone: "enf", icon: AudioWaveform, color: "var(--src-enf)" },
  alerts: { label: "alert", tone: "alerts", icon: RadioTower, color: "var(--src-alerts)" },
};

export function SourceBadge({ source, prefix }: { source: StartSource | null | undefined; prefix?: string }) {
  if (!source) return null;
  const m = SOURCE_META[source];
  const Icon = m.icon;
  return (
    <Badge tone={m.tone} title={`Start time ${source === "user" ? "entered by you" : `estimated from ${m.label}`}`}>
      <Icon className="size-3" />
      {prefix}
      {m.label}
    </Badge>
  );
}
