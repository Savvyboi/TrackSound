import type { FeatureCollection } from "geojson";
import type { AddLayerObject, GeoJSONSource, Map as MLMap } from "maplibre-gl";

export type OverlayData = {
  dark: boolean;
  land: FeatureCollection | null;
  enfGrid: FeatureCollection | null;
  enfNominal: 50 | 60 | null;
  region: FeatureCollection;
  regionDraft: FeatureCollection;
  aHeat: FeatureCollection;
  aHeatR0: number; // circle radius (px) at zoom 0 for one grid cell
  flights: FeatureCollection;
  flightsAttribution: string;
  flightHl: number[]; // flight idx highlighted (candidate's flights)
  flightSel: number | null;
  candLinks: FeatureCollection;
  candCpa: FeatureCollection;
  lHeat: FeatureCollection;
  lHeatR0: number;
  ranges: FeatureCollection;
  lMatch: FeatureCollection;
  strikes: FeatureCollection;
  lUnc: FeatureCollection;
  hoverAc: FeatureCollection;
  vis: {
    land: boolean;
    enfGrid: boolean;
    region: boolean;
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
  | "region"
  | "regionDraft"
  | "aHeat"
  | "flights"
  | "candLinks"
  | "candCpa"
  | "lHeat"
  | "ranges"
  | "lMatch"
  | "strikes"
  | "lUnc"
  | "hoverAc";

const SOURCES: SourceKey[] = ["land", "enfGrid", "region", "regionDraft", "aHeat", "lHeat", "flights", "ranges", "lMatch", "strikes", "candLinks", "candCpa", "lUnc", "hoverAc"];

const sid = (k: SourceKey) => `ts-${k}`;

/** Score heat ramp (dark violet → magenta → orange → pale yellow), by normalised score `u`. */
const HEAT_COLOR = ["interpolate", ["linear"], ["get", "u"], 0, "#3b0f70", 0.4, "#8c2981", 0.65, "#de4968", 0.85, "#fe9f6d", 1, "#fcfdbf"];
const HEAT_OPACITY = ["interpolate", ["linear"], ["get", "u"], 0, 0.28, 0.5, 0.5, 0.8, 0.75, 1, 0.95];

function radiusExpr(r0: number, minPx: number) {
  // radius doubles per zoom level (one grid cell on the ground), but never smaller than minPx
  const zA = Math.max(0, Math.min(21, Math.log2(minPx / Math.max(1e-9, r0))));
  return ["interpolate", ["exponential", 2], ["zoom"], zA, minPx, 22, Math.max(minPx, r0 * 2 ** 22)];
}

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
    id: "ts-region-fill",
    group: "region",
    spec: () => ({ id: "ts-region-fill", type: "fill", source: sid("region"), paint: { "fill-color": "#2dd4bf", "fill-opacity": 0.05 } }),
  },
  {
    id: "ts-aheat",
    group: "aHeat",
    spec: (d) => ({
      id: "ts-aheat",
      type: "circle",
      source: sid("aHeat"),
      paint: {
        "circle-radius": radiusExpr(d.aHeatR0, 3) as never,
        "circle-color": HEAT_COLOR as never,
        "circle-opacity": HEAT_OPACITY as never,
        "circle-blur": 0.15,
      },
    }),
  },
  {
    id: "ts-lheat",
    group: "lSolve",
    spec: (d) => ({
      id: "ts-lheat",
      type: "circle",
      source: sid("lHeat"),
      paint: {
        "circle-radius": radiusExpr(d.lHeatR0, 3) as never,
        "circle-color": HEAT_COLOR as never,
        "circle-opacity": HEAT_OPACITY as never,
        "circle-blur": 0.15,
      },
    }),
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
        "line-color": d.dark ? "#7aa7ff" : "#2563eb",
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
    spec: () => ({
      id: "ts-region-line",
      type: "line",
      source: sid("region"),
      paint: { "line-color": "#2dd4bf", "line-width": 2, "line-opacity": 0.95 },
    }),
  },
  {
    id: "ts-draft-fill",
    group: "region",
    spec: () => ({ id: "ts-draft-fill", type: "fill", source: sid("regionDraft"), paint: { "fill-color": "#2dd4bf", "fill-opacity": 0.1 } }),
  },
  {
    id: "ts-draft-line",
    group: "region",
    spec: () => ({
      id: "ts-draft-line",
      type: "line",
      source: sid("regionDraft"),
      paint: { "line-color": "#5eead4", "line-width": 2, "line-dasharray": [2, 1.5] },
    }),
  },
  {
    id: "ts-hover-ac",
    group: "hoverAc",
    spec: () => ({
      id: "ts-hover-ac",
      type: "circle",
      source: sid("hoverAc"),
      paint: {
        "circle-radius": 5,
        "circle-color": "#fde047",
        "circle-stroke-color": "#06080c",
        "circle-stroke-width": 2,
      },
    }),
  },
];

export const INTERACTIVE_LAYERS = ["ts-flights", "ts-flights-hl", "ts-flights-sel", "ts-strikes"];

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
  setProp(map, st, "ts-flights", "paint", "line-color", d.dark ? "#7aa7ff" : "#2563eb");
  setProp(map, st, "ts-flights", "paint", "line-opacity", d.flightHl.length || d.flightSel != null ? 0.28 : 0.5);
  setProp(map, st, "ts-aheat", "paint", "circle-radius", radiusExpr(d.aHeatR0, 3));
  setProp(map, st, "ts-lheat", "paint", "circle-radius", radiusExpr(d.lHeatR0, 3));
  setProp(map, st, "ts-enf-fill", "paint", "fill-color", enfColor(d));
  setProp(map, st, "ts-enf-fill", "paint", "fill-opacity", enfOpacity(d));
  setProp(map, st, "ts-land-fill", "paint", "fill-color", d.dark ? "#151b24" : "#f8fafc");
  setProp(map, st, "ts-land-line", "paint", "line-color", d.dark ? "#2c3542" : "#cbd5e1");
  setProp(map, st, "ts-lmatch", "paint", "line-color", d.dark ? "#ffffff" : "#0f172a");
}
