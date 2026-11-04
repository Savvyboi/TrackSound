/** Kinds of catalogued events, and how each is drawn (map icons and list glyphs share these). */

export type EventIcon = "lightning" | "blast" | "fireworks" | "boom" | "quake" | "meteor" | "event";

const LIGHTNING_KINDS = new Set(["flash", "cg", "ic", "cc", "lightning", "stroke", "strike", "pulse", "group", "cloud-to-ground", "intracloud"]);

export function eventIcon(kind: string | null | undefined): EventIcon {
  const k = (kind ?? "").trim().toLowerCase();
  if (!k || LIGHTNING_KINDS.has(k)) return "lightning";
  if (k.includes("firework")) return "fireworks";
  if (k.includes("sonic")) return "boom";
  if (k.includes("quake") || k.includes("seism")) return "quake";
  if (k.includes("meteor") || k.includes("bolide")) return "meteor";
  if (k.includes("blast") || k.includes("explos") || k.includes("mining")) return "blast";
  return "event";
}

export const EVENT_STYLE: Record<Exclude<EventIcon, "lightning">, { color: string; label: string }> = {
  blast: { color: "#fb923c", label: "Blast or explosion" },
  fireworks: { color: "#f472b6", label: "Fireworks" },
  boom: { color: "#38bdf8", label: "Sonic boom" },
  quake: { color: "#f87171", label: "Earthquake" },
  meteor: { color: "#a78bfa", label: "Meteor" },
  event: { color: "#2dd4bf", label: "Event" },
};

/** Kinds offered when entering a known event by hand. */
export const MANUAL_KINDS = ["event", "explosion", "fireworks", "blast", "other"] as const;

/** USGS ComCat event types, and whether each is on by default. */
export const USGS_TYPES: { type: string; on: boolean }[] = [
  { type: "quarry blast", on: true },
  { type: "mining explosion", on: true },
  { type: "explosion", on: true },
  { type: "sonic boom", on: true },
  { type: "earthquake", on: false },
  { type: "meteor", on: false },
];
