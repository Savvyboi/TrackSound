import type { Feature, FeatureCollection } from "geojson";
import type { Map as MLMap } from "maplibre-gl";
import type { Theme } from "../../lib/theme";

export type StyleSpec = Exclude<Parameters<MLMap["setStyle"]>[0], string | null | undefined>;

export type BasemapId = "auto" | "streets" | "light" | "dark" | "satellite" | "plain";

export const BASEMAPS: { id: BasemapId; label: string; hint: string }[] = [
  { id: "auto", label: "Auto", hint: "Dark or light map to match the theme" },
  { id: "streets", label: "Streets", hint: "OpenFreeMap Liberty" },
  { id: "light", label: "Light", hint: "OpenFreeMap Positron" },
  { id: "dark", label: "Dark", hint: "OpenFreeMap Dark" },
  { id: "satellite", label: "Satellite", hint: "Esri World Imagery" },
  { id: "plain", label: "Plain", hint: "No tiles: works offline" },
];

const OFM = "https://tiles.openfreemap.org/styles/";

export const ESRI_ATTRIBUTION = "Esri, Maxar, Earthstar Geographics, and the GIS User Community";

function graticule(): FeatureCollection {
  const features: Feature[] = [];
  for (let lon = -180; lon <= 180; lon += 10) {
    const coords: [number, number][] = [];
    for (let lat = -80; lat <= 80; lat += 2) coords.push([lon, lat]);
    features.push({ type: "Feature", properties: { major: lon % 30 === 0 }, geometry: { type: "LineString", coordinates: coords } });
  }
  for (let lat = -80; lat <= 80; lat += 10) {
    const coords: [number, number][] = [];
    for (let lon = -180; lon <= 180; lon += 2) coords.push([lon, lat]);
    features.push({ type: "Feature", properties: { major: lat % 30 === 0 }, geometry: { type: "LineString", coordinates: coords } });
  }
  return { type: "FeatureCollection", features };
}

export function plainStyle(theme: Theme): StyleSpec {
  const dark = theme === "dark";
  return {
    version: 8,
    name: "plain",
    sources: { graticule: { type: "geojson", data: graticule() } },
    layers: [
      { id: "background", type: "background", paint: { "background-color": dark ? "#0c1016" : "#e8ecf1" } },
      {
        id: "graticule",
        type: "line",
        source: "graticule",
        paint: {
          "line-color": dark ? "#ffffff" : "#0f172a",
          "line-opacity": ["case", ["get", "major"], dark ? 0.09 : 0.1, dark ? 0.045 : 0.05],
          "line-width": 0.6,
        },
      },
    ],
  };
}

export function satelliteStyle(): StyleSpec {
  return {
    version: 8,
    name: "satellite",
    sources: {
      esri: {
        type: "raster",
        tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"],
        tileSize: 256,
        maxzoom: 19,
        attribution: ESRI_ATTRIBUTION,
      },
    },
    layers: [
      { id: "background", type: "background", paint: { "background-color": "#0a0d12" } },
      { id: "esri", type: "raster", source: "esri" },
    ],
  };
}

export function styleFor(id: BasemapId, theme: Theme): string | StyleSpec {
  switch (id) {
    case "auto":
      return theme === "dark" ? `${OFM}dark` : `${OFM}positron`;
    case "streets":
      return `${OFM}liberty`;
    case "light":
      return `${OFM}positron`;
    case "dark":
      return `${OFM}dark`;
    case "satellite":
      return satelliteStyle();
    case "plain":
      return plainStyle(theme);
  }
}

/** Does this basemap look dark (to pick overlay colours that read well on it)? */
export function isDarkBasemap(id: BasemapId, theme: Theme): boolean {
  if (id === "auto" || id === "plain") return theme === "dark";
  return id === "dark" || id === "satellite";
}
