import type { Map as MLMap } from "maplibre-gl";
import { EVENT_STYLE, type EventIcon } from "../lightning/kinds";

/** Map icons drawn on a canvas (no sprite server needed). Both point east (direction of travel). */
const SIZE = 40; // px at pixelRatio 2 → 20 css px

function draw(kind: "plane" | "heli", fill: string): ImageData {
  const c = document.createElement("canvas");
  c.width = SIZE;
  c.height = SIZE;
  const ctx = c.getContext("2d")!;
  ctx.translate(SIZE / 2, SIZE / 2);
  ctx.lineJoin = "round";
  ctx.lineCap = "round";
  ctx.beginPath();
  if (kind === "plane") {
    // fuselage, wings and tailplane of a light aircraft seen from above
    const P: [number, number][] = [
      [17, 0], [14, -2.4], [5, -2.6], [-1, -15.5], [-5, -15.5], [-2.5, -2.6], [-11, -2.4], [-15, -7.5], [-17.5, -7.5],
      [-16, -1.6], [-16, 1.6], [-17.5, 7.5], [-15, 7.5], [-11, 2.4], [-2.5, 2.6], [-5, 15.5], [-1, 15.5], [5, 2.6], [14, 2.4],
    ];
    ctx.moveTo(P[0][0], P[0][1]);
    for (const [x, y] of P.slice(1)) ctx.lineTo(x, y);
    ctx.closePath();
    ctx.lineWidth = 3;
    ctx.strokeStyle = "rgba(6,8,12,0.9)";
    ctx.stroke();
    ctx.fillStyle = fill;
    ctx.fill();
  } else {
    // main rotor disc, cabin, tail boom and tail rotor
    ctx.strokeStyle = "rgba(6,8,12,0.9)";
    ctx.lineWidth = 5.5;
    ctx.moveTo(-17, 0);
    ctx.lineTo(0, 0);
    ctx.moveTo(-16.5, -4.5);
    ctx.lineTo(-16.5, 4.5);
    ctx.stroke();
    ctx.beginPath();
    ctx.strokeStyle = fill;
    ctx.lineWidth = 2.5;
    ctx.moveTo(-17, 0);
    ctx.lineTo(0, 0);
    ctx.moveTo(-16.5, -4.5);
    ctx.lineTo(-16.5, 4.5);
    ctx.stroke();
    ctx.beginPath();
    ctx.ellipse(4, 0, 8, 5.5, 0, 0, Math.PI * 2);
    ctx.lineWidth = 3;
    ctx.strokeStyle = "rgba(6,8,12,0.9)";
    ctx.stroke();
    ctx.fillStyle = fill;
    ctx.fill();
    ctx.beginPath();
    ctx.lineWidth = 4.5;
    ctx.strokeStyle = "rgba(6,8,12,0.75)";
    ctx.moveTo(-9, -12);
    ctx.lineTo(17, 12);
    ctx.moveTo(-9, 12);
    ctx.lineTo(17, -12);
    ctx.stroke();
    ctx.beginPath();
    ctx.lineWidth = 2;
    ctx.strokeStyle = fill;
    ctx.moveTo(-9, -12);
    ctx.lineTo(17, 12);
    ctx.moveTo(-9, 12);
    ctx.lineTo(17, -12);
    ctx.stroke();
  }
  return ctx.getImageData(0, 0, SIZE, SIZE);
}

export const ICONS = {
  plane: { id: "ts-plane", kind: "plane" as const, fill: "#a5c4ff", fillLight: "#2563eb" },
  heli: { id: "ts-heli", kind: "heli" as const, fill: "#f0abfc", fillLight: "#c026d3" },
  planeHover: { id: "ts-plane-hl", kind: "plane" as const, fill: "#fde047", fillLight: "#f59e0b" },
  heliHover: { id: "ts-heli-hl", kind: "heli" as const, fill: "#fde047", fillLight: "#f59e0b" },
};

const registered = new WeakMap<MLMap, boolean>();

/** Register the icons with the current style (styles drop images when swapped; colours follow the basemap). */
export function ensureIcons(map: MLMap, dark: boolean) {
  const recolour = registered.get(map) !== dark;
  for (const ic of Object.values(ICONS)) {
    const data = () => draw(ic.kind, dark ? ic.fill : ic.fillLight);
    if (!map.hasImage(ic.id)) map.addImage(ic.id, data(), { pixelRatio: 2 });
    else if (recolour) map.updateImage(ic.id, data());
  }
  registered.set(map, dark);
}

// ---- non-lightning events (blasts, booms, quakes ...) -------------------------------------


const EV = 48; // px at pixelRatio 2 → 24 css px

function drawEvent(kind: Exclude<EventIcon, "lightning">): ImageData {
  const c = document.createElement("canvas");
  c.width = EV;
  c.height = EV;
  const ctx = c.getContext("2d")!;
  ctx.translate(EV / 2, EV / 2);
  ctx.scale(EV / 22, EV / 22); // draw in a -11..11 box
  const color = EVENT_STYLE[kind].color;
  const outline = "rgba(6,8,12,0.9)";
  ctx.lineJoin = "round";
  ctx.lineCap = "round";
  const star = (n: number, ro: number, ri: number) => {
    ctx.beginPath();
    for (let i = 0; i < n * 2; i++) {
      const r = i % 2 ? ri : ro;
      const a = (i / (n * 2)) * Math.PI * 2 - Math.PI / 2;
      if (i) ctx.lineTo(Math.cos(a) * r, Math.sin(a) * r);
      else ctx.moveTo(Math.cos(a) * r, Math.sin(a) * r);
    }
    ctx.closePath();
  };
  const fillOutlined = () => {
    ctx.lineWidth = 2;
    ctx.strokeStyle = outline;
    ctx.stroke();
    ctx.fillStyle = color;
    ctx.fill();
  };
  if (kind === "blast") {
    star(8, 9.5, 4.2);
    fillOutlined();
  } else if (kind === "fireworks") {
    star(4, 9.5, 2.6);
    fillOutlined();
    for (const [x, y, r] of [
      [6.8, -6.8, 1.8],
      [-6.8, 6.8, 1.5],
    ]) {
      ctx.beginPath();
      ctx.arc(x, y, r, 0, Math.PI * 2);
      fillOutlined();
    }
  } else if (kind === "boom") {
    const path = () => {
      ctx.beginPath();
      ctx.moveTo(-7.5, -7.5);
      ctx.lineTo(0, 0);
      ctx.lineTo(-7.5, 7.5);
      ctx.moveTo(0, -7.5);
      ctx.lineTo(7.5, 0);
      ctx.lineTo(0, 7.5);
    };
    path();
    ctx.lineWidth = 5.5;
    ctx.strokeStyle = outline;
    ctx.stroke();
    path();
    ctx.lineWidth = 3;
    ctx.strokeStyle = color;
    ctx.stroke();
  } else if (kind === "quake") {
    for (const r of [8.5, 4.8]) {
      ctx.beginPath();
      ctx.arc(0, 0, r, 0, Math.PI * 2);
      ctx.lineWidth = 4;
      ctx.strokeStyle = outline;
      ctx.stroke();
      ctx.lineWidth = 2;
      ctx.strokeStyle = color;
      ctx.stroke();
    }
    ctx.beginPath();
    ctx.arc(0, 0, 2, 0, Math.PI * 2);
    ctx.fillStyle = color;
    ctx.fill();
  } else if (kind === "meteor") {
    ctx.beginPath();
    ctx.moveTo(-9, -9);
    ctx.lineTo(2, 2);
    ctx.lineWidth = 3;
    ctx.strokeStyle = color;
    ctx.globalAlpha = 0.75;
    ctx.stroke();
    ctx.globalAlpha = 1;
    ctx.beginPath();
    ctx.arc(3.5, 3.5, 5, 0, Math.PI * 2);
    fillOutlined();
  } else {
    ctx.beginPath();
    ctx.moveTo(0, -9.5);
    ctx.lineTo(9.5, 0);
    ctx.lineTo(0, 9.5);
    ctx.lineTo(-9.5, 0);
    ctx.closePath();
    fillOutlined();
  }
  return ctx.getImageData(0, 0, EV, EV);
}

export const EVENT_ICON_IDS = Object.keys(EVENT_STYLE).map((k) => `ts-ev-${k}`);

export function ensureEventIcons(map: MLMap) {
  for (const k of Object.keys(EVENT_STYLE) as Exclude<EventIcon, "lightning">[]) {
    const id = `ts-ev-${k}`;
    if (!map.hasImage(id)) map.addImage(id, drawEvent(k), { pixelRatio: 2 });
  }
}
