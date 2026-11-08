import type { FeatureCollection } from "geojson";
import type { AddLayerObject, GeoJSONSource, Map as MLMap } from "maplibre-gl";
import { ensureEventIcons, ensureIcons, ICONS } from "./icons";
import type { HeatImage } from "./geometry";

export type OverlayData = {
  dark: boolean;
  land: FeatureCollection | null;
  enfGrid: FeatureCollection | null;
  enfNominal: 50 | 60 | null;
  region: FeatureCollection;
  regionDraft: FeatureCollection;
  /** outlines of the case's countries (the "recorded in" filter) */
  countries: FeatureCollection;
  aHeat: HeatImage | null;
  flights: FeatureCollection;
  flightsAttribution: string;
  flightHl: number[]; // flight idx highlighted (candidate's flights)
  flightSel: number | null;
  candLinks: FeatureCollection;
  candCpa: FeatureCollection;
  lHeat: HeatImage | null;
  ranges: FeatureCollection;
  lMatch: FeatureCollection;
  strikes: FeatureCollection;
  lUnc: FeatureCollection;
  hoverAc: FeatureCollection;
  vis: {
    land: boolean;
    enfGrid: boolean;
    region: boolean;
    draft: boolean;
    aHeat: boolean;
    flights: boolean;
    cand: boolean;
    strikes: boolean;
    lSolve: boolean;
    hoverAc: boolean;
  };
};

type SourceKey =
  | "land"
  | "enfGrid"
  | "countries"
  | "region"
  | "regionDraft"
  | "flights"
  | "candLinks"
  | "candCpa"
  | "ranges"
  | "lMatch"
  | "strikes"
  | "lUnc"
  | "hoverAc";

const SOURCES: SourceKey[] = ["land", "enfGrid", "countries", "region", "regionDraft", "flights", "ranges", "lMatch", "strikes", "candLinks", "candCpa", "lUnc", "hoverAc"];
const IMAGE_SOURCES = ["aHeat", "lHeat"] as const;
const BLANK_PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=";
const BLANK_COORDS: HeatImage["coordinates"] = [
  [0, 0.0001],
  [0.0001, 0.0001],
  [0.0001, 0],
  [0, 0],
];

const sid = (k: SourceKey | "aHeat" | "lHeat") => `ts-${k}`;

type Layer = { id: string; group: keyof OverlayData["vis"]; spec: (d: OverlayData) => AddLayerObject };

const LAYERS: Layer[] = [
  {
    id: "ts-land-fill",
    group: "land",
    spec: (d) => ({ id: "ts-land-fill", type: "fill", source: sid("land"), paint: { "fill-color": d.dark ? "#151b24" : "#f8fafc", "fill-opacity": 1 } }),
  },
  {
    id: "ts-land-line",
    group: "land",
    spec: (d) => ({ id: "ts-land-line", type: "line", source: sid("land"), paint: { "line-color": d.dark ? "#2c3542" : "#cbd5e1", "line-width": 0.7 } }),
  },
  {
    id: "ts-enf-fill",
    group: "enfGrid",
    spec: () => ({
      id: "ts-enf-fill",
      type: "fill",
      source: sid("enfGrid"),
      paint: { "fill-color": "#888", "fill-opacity": 0.3 },
    }),
  },
  {
    id: "ts-enf-line",
    group: "enfGrid",
    spec: () => ({ id: "ts-enf-line", type: "line", source: sid("enfGrid"), paint: { "line-color": "#000", "line-opacity": 0.25, "line-width": 0.5 } }),
  },
  {
    id: "ts-countries-fill",
    group: "region",
    spec: (d) => ({ id: "ts-countries-fill", type: "fill", source: sid("countries"), paint: { "fill-color": d.dark ? "#e2e8f0" : "#334155", "fill-opacity": 0.05 } }),
  },
  {
    id: "ts-countries-line",
    group: "region",
    spec: (d) => ({
      id: "ts-countries-line",
      type: "line",
      source: sid("countries"),
      layout: { "line-join": "round" },
      paint: { "line-color": d.dark ? "#e2e8f0" : "#334155", "line-opacity": 0.85, "line-width": 1.6, "line-dasharray": [3, 2] },
    }),
  },
  {
    id: "ts-region-fill",
    group: "region",
    spec: (d) => ({ id: "ts-region-fill", type: "fill", source: sid("region"), paint: { "fill-color": regionColor(d), "fill-opacity": 0.06 } }),
  },
  {
    id: "ts-aheat",
    group: "aHeat",
    spec: () => ({ id: "ts-aheat", type: "raster", source: "ts-aHeat", paint: { "raster-resampling": "nearest", "raster-fade-duration": 0 } }),
  },
  {
    id: "ts-lheat",
    group: "lSolve",
    spec: () => ({ id: "ts-lheat", type: "raster", source: "ts-lHeat", paint: { "raster-resampling": "nearest", "raster-fade-duration": 0 } }),
  },
  {
    id: "ts-flights",
    group: "flights",
    spec: (d) => ({
      id: "ts-flights",
      type: "line",
      source: sid("flights"),
      layout: { "line-cap": "round", "line-join": "round" },
      paint: {
        "line-color": flightColor(d) as never,
        "line-opacity": 0.5,
        "line-width": ["interpolate", ["linear"], ["zoom"], 6, 0.8, 12, 1.6] as never,
      },
    }),
  },
  {
    id: "ts-flights-hl",
    group: "flights",
    spec: () => ({
      id: "ts-flights-hl",
      type: "line",
      source: sid("flights"),
      filter: ["in", ["get", "idx"], ["literal", []]] as never,
      layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": "#67e8f9", "line-width": 3, "line-opacity": 0.95 },
    }),
  },
  {
    id: "ts-flights-sel",
    group: "flights",
    spec: () => ({
      id: "ts-flights-sel",
      type: "line",
      source: sid("flights"),
      filter: ["==", ["get", "idx"], -1] as never,
      layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": "#fde047", "line-width": 3.5, "line-opacity": 1 },
    }),
  },
  {
    id: "ts-flights-icons",
    group: "flights",
    spec: () => ({
      id: "ts-flights-icons",
      type: "symbol",
      source: sid("flights"),
      layout: {
        "symbol-placement": "line",
        "symbol-spacing": 340,
        "icon-image": ["case", ["==", ["get", "heli"], true], ICONS.heli.id, ICONS.plane.id] as never,
        "icon-size": ["interpolate", ["linear"], ["zoom"], 5, 0.5, 10, 0.75, 14, 0.95] as never,
        "icon-rotation-alignment": "map",
        "icon-allow-overlap": true,
        "icon-ignore-placement": true,
        "icon-keep-upright": false,
      },
      paint: { "icon-opacity": 0.8 },
    }),
  },
  {
    id: "ts-ranges",
    group: "lSolve",
    spec: () => ({
      id: "ts-ranges",
      type: "line",
      source: sid("ranges"),
      paint: { "line-color": "#facc15", "line-width": 1.2, "line-opacity": 0.7, "line-dasharray": [3, 3] },
    }),
  },
  {
    id: "ts-lmatch",
    group: "lSolve",
    spec: (d) => ({
      id: "ts-lmatch",
      type: "line",
      source: sid("lMatch"),
      paint: { "line-color": d.dark ? "#ffffff" : "#0f172a", "line-width": 1, "line-opacity": 0.55, "line-dasharray": [2, 2] },
    }),
  },
  {
    id: "ts-strikes",
    group: "strikes",
    spec: (d) => ({
      id: "ts-strikes",
      type: "circle",
      source: sid("strikes"),
      filter: ["!", ["get", "event"]] as never,
      paint: {
        "circle-radius": ["case", ["get", "matched"], 6, 3.5] as never,
        "circle-color": ["interpolate", ["linear"], ["get", "u"], 0, "#60a5fa", 0.5, "#facc15", 1, "#f43f5e"] as never,
        "circle-opacity": 0.9,
        "circle-stroke-color": ["case", ["get", "matched"], "#ffffff", d.dark ? "#0b0e13" : "#ffffff"] as never,
        "circle-stroke-width": ["case", ["get", "matched"], 1.8, 0.8] as never,
      },
    }),
  },
  {
    id: "ts-events-ring",
    group: "strikes",
    spec: () => ({
      id: "ts-events-ring",
      type: "circle",
      source: sid("strikes"),
      filter: ["all", ["get", "event"], ["get", "matched"]] as never,
      paint: { "circle-radius": 15, "circle-color": "rgba(0,0,0,0)", "circle-stroke-color": "#ffffff", "circle-stroke-width": 2 },
    }),
  },
  {
    id: "ts-events",
    group: "strikes",
    spec: () => ({
      id: "ts-events",
      type: "symbol",
      source: sid("strikes"),
      filter: ["get", "event"] as never,
      layout: {
        "icon-image": ["get", "icon"] as never,
        "icon-size": ["case", ["get", "matched"], 1.1, 0.9] as never,
        "icon-allow-overlap": true,
        "icon-ignore-placement": true,
      },
    }),
  },
  {
    id: "ts-cand-links",
    group: "cand",
    spec: () => ({
      id: "ts-cand-links",
      type: "line",
      source: sid("candLinks"),
      paint: { "line-color": "#67e8f9", "line-width": 1.4, "line-opacity": 0.9, "line-dasharray": [2, 2] },
    }),
  },
  {
    id: "ts-cand-cpa",
    group: "cand",
    spec: () => ({
      id: "ts-cand-cpa",
      type: "circle",
      source: sid("candCpa"),
      paint: { "circle-radius": 4.5, "circle-color": "#67e8f9", "circle-stroke-color": "#06080c", "circle-stroke-width": 1.5 },
    }),
  },
  {
    id: "ts-lunc-fill",
    group: "lSolve",
    spec: () => ({ id: "ts-lunc-fill", type: "fill", source: sid("lUnc"), paint: { "fill-color": "#facc15", "fill-opacity": 0.1 } }),
  },
  {
    id: "ts-lunc-line",
    group: "lSolve",
    spec: () => ({ id: "ts-lunc-line", type: "line", source: sid("lUnc"), paint: { "line-color": "#facc15", "line-width": 1.5, "line-opacity": 0.9 } }),
  },
  {
    id: "ts-region-line",
    group: "region",
    spec: (d) => ({
      id: "ts-region-line",
      type: "line",
      source: sid("region"),
      paint: { "line-color": regionColor(d), "line-width": 2, "line-opacity": 0.95 },
    }),
  },
  // the rectangle being drawn: always shown (even with the region layer off), with a halo so it
  // stands out on light, dark and satellite maps
  {
    id: "ts-draft-fill",
    group: "draft",
    spec: (d) => ({ id: "ts-draft-fill", type: "fill", source: sid("regionDraft"), paint: { "fill-color": regionColor(d), "fill-opacity": 0.14 } }),
  },
  {
    id: "ts-draft-halo",
    group: "draft",
    spec: (d) => ({
      id: "ts-draft-halo",
      type: "line",
      source: sid("regionDraft"),
      paint: { "line-color": d.dark ? "#06080c" : "#ffffff", "line-width": 5, "line-opacity": 0.85 },
    }),
  },
  {
    id: "ts-draft-line",
    group: "draft",
    spec: (d) => ({
      id: "ts-draft-line",
      type: "line",
      source: sid("regionDraft"),
      paint: { "line-color": regionColor(d), "line-width": 2.5, "line-dasharray": [2, 1.5] },
    }),
  },
  {
    id: "ts-hover-ac",
    group: "hoverAc",
    spec: () => ({
      id: "ts-hover-ac",
      type: "symbol",
      source: sid("hoverAc"),
      layout: {
        "icon-image": ["case", ["==", ["get", "heli"], true], ICONS.heliHover.id, ICONS.planeHover.id] as never,
        "icon-rotate": ["-", ["get", "bearing"], 90] as never,
        "icon-rotation-alignment": "map",
        "icon-size": 1.15,
        "icon-allow-overlap": true,
        "icon-ignore-placement": true,
      },
    }),
  },
];

export const INTERACTIVE_LAYERS = ["ts-flights", "ts-flights-hl", "ts-flights-sel", "ts-strikes", "ts-events"];

export type SyncState = { data: Map<string, unknown>; props: Map<string, string>; attrib: Map<string, string> };
export const newSyncState = (): SyncState => ({ data: new Map(), props: new Map(), attrib: new Map() });

function setProp(map: MLMap, st: SyncState, layer: string, kind: "paint" | "filter", prop: string, value: unknown) {
  const key = `${layer}|${kind}|${prop}`;
  const json = JSON.stringify(value);
  if (st.props.get(key) === json) return;
  st.props.set(key, json);
  if (kind === "filter") map.setFilter(layer, value as never);
  else map.setPaintProperty(layer, prop as never, value as never);
}

function flightColor(d: OverlayData) {
  return ["case", ["==", ["get", "heli"], true], d.dark ? "#f0abfc" : "#c026d3", d.dark ? "#7aa7ff" : "#2563eb"];
}

function regionColor(d: OverlayData) {
  return d.dark ? "#2dd4bf" : "#0f766e";
}

function enfColor(d: OverlayData) {
  return ["match", ["to-string", ["get", "mains_hz"]], "50", "#60a5fa", "60", "#f472b6", "50/60", "#a78bfa", d.dark ? "#4b5563" : "#9ca3af"];
}

function enfOpacity(d: OverlayData) {
  if (!d.enfNominal) return 0.32;
  const n = String(d.enfNominal);
  return ["case", ["any", ["==", ["to-string", ["get", "mains_hz"]], n], ["==", ["to-string", ["get", "mains_hz"]], "50/60"]], 0.5, 0.07];
}

/** Make the map's overlay sources and layers match `d` (safe to call after a style swap). */
export function syncOverlays(map: MLMap, d: OverlayData, st: SyncState) {
  ensureIcons(map, d.dark);
  ensureEventIcons(map);
  for (const k of SOURCES) {
    const id = sid(k);
    const data = (d[k] as FeatureCollection | null) ?? { type: "FeatureCollection", features: [] };
    const attribution = k === "flights" ? d.flightsAttribution : "";
    let src = map.getSource(id) as GeoJSONSource | undefined;
    if (src && (st.attrib.get(id) ?? "") !== attribution) {
      // attribution is fixed when a source is created: re-create it (and its layers)
      for (const l of LAYERS) if (map.getLayer(l.id) && (map.getLayer(l.id) as { source?: string }).source === id) map.removeLayer(l.id);
      map.removeSource(id);
      src = undefined;
    }
    if (!src) {
      map.addSource(id, attribution ? { type: "geojson", data, attribution } : { type: "geojson", data });
      st.data.set(id, data);
      st.attrib.set(id, attribution);
    } else if (st.data.get(id) !== data) {
      src.setData(data);
      st.data.set(id, data);
    }
  }
  for (const k of IMAGE_SOURCES) {
    const id = sid(k);
    const img = d[k];
    const src = map.getSource(id) as { updateImage?: (o: { url: string; coordinates: HeatImage["coordinates"] }) => void } | undefined;
    const next = img ?? { url: BLANK_PNG, coordinates: BLANK_COORDS };
    if (!src) {
      map.addSource(id, { type: "image", url: next.url, coordinates: next.coordinates });
      st.data.set(id, img);
    } else if (st.data.get(id) !== img) {
      src.updateImage?.(next);
      st.data.set(id, img);
    }
  }
  for (const l of LAYERS) {
    if (!map.getLayer(l.id)) {
      map.addLayer(l.spec(d));
      // forget remembered props for a freshly added layer
      for (const key of [...st.props.keys()]) if (key.startsWith(`${l.id}|`)) st.props.delete(key);
    }
    const visible = d.vis[l.group];
    const key = `${l.id}|layout|visibility`;
    const v = visible ? "visible" : "none";
    if (st.props.get(key) !== v) {
      map.setLayoutProperty(l.id, "visibility", v);
      st.props.set(key, v);
    }
  }
  // data-dependent styling
  setProp(map, st, "ts-flights-hl", "filter", "", ["in", ["get", "idx"], ["literal", d.flightHl]]);
  setProp(map, st, "ts-flights-sel", "filter", "", ["==", ["get", "idx"], d.flightSel ?? -1]);
  setProp(map, st, "ts-flights", "paint", "line-color", flightColor(d));
  setProp(map, st, "ts-flights", "paint", "line-opacity", d.flightHl.length || d.flightSel != null ? 0.28 : 0.5);
  setProp(map, st, "ts-enf-fill", "paint", "fill-color", enfColor(d));
  setProp(map, st, "ts-enf-fill", "paint", "fill-opacity", enfOpacity(d));
  setProp(map, st, "ts-land-fill", "paint", "fill-color", d.dark ? "#151b24" : "#f8fafc");
  setProp(map, st, "ts-land-line", "paint", "line-color", d.dark ? "#2c3542" : "#cbd5e1");
  setProp(map, st, "ts-lmatch", "paint", "line-color", d.dark ? "#ffffff" : "#0f172a");
  setProp(map, st, "ts-countries-line", "paint", "line-color", d.dark ? "#e2e8f0" : "#334155");
  setProp(map, st, "ts-countries-fill", "paint", "fill-color", d.dark ? "#e2e8f0" : "#334155");
  setProp(map, st, "ts-region-fill", "paint", "fill-color", regionColor(d));
  setProp(map, st, "ts-region-line", "paint", "line-color", regionColor(d));
  setProp(map, st, "ts-draft-fill", "paint", "fill-color", regionColor(d));
  setProp(map, st, "ts-draft-line", "paint", "line-color", regionColor(d));
  setProp(map, st, "ts-draft-halo", "paint", "line-color", d.dark ? "#06080c" : "#ffffff");
  const cand = d.dark ? "#67e8f9" : "#0891b2";
  setProp(map, st, "ts-flights-hl", "paint", "line-color", cand);
  setProp(map, st, "ts-cand-links", "paint", "line-color", cand);
  setProp(map, st, "ts-cand-cpa", "paint", "circle-color", cand);
  setProp(map, st, "ts-cand-cpa", "paint", "circle-stroke-color", d.dark ? "#06080c" : "#ffffff");
}
