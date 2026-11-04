import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?url";
import {
  AttributionControl,
  Map as MLMap,
  Marker,
  NavigationControl,
  Popup,
  ScaleControl,
  setWorkerUrl,
  type MapGeoJSONFeature,
  type MapMouseEvent,
} from "maplibre-gl";
import { Crosshair, Layers as LayersIcon, Map as MapIcon, SquareDashed, WifiOff, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { errorMessage } from "../../api/client";
import { useAlerts, usePatchCase, useEnf, useFlightSet, useFlightsGeo, useGridMap, useLightning, useSearch, useSolve, useStrikes } from "../../api/queries";
import type { BBox, FlightsGeoJSON } from "../../api/types";
import { Button, IconButton } from "../../components/ui/Button";
import { Switch } from "../../components/ui/form";
import { Popover, usePopover } from "../../components/ui/overlay";
import { toast } from "../../components/ui/toast";
import { cn } from "../../lib/cn";
import { colormapGradient } from "../../lib/colormap";
import { fmtCoord } from "../../lib/format";
import { bboxPolygon, normalizeBBox, validBBox } from "../../lib/geo";
import { loadPref, savePref } from "../../lib/store";
import { useTheme } from "../../lib/theme";
import { fmtClock, fmtDateIso } from "../../lib/tz";
import { toggleLayer, useWs, ws, type Layers } from "../../state/workspace";
import { caseC, caseDuration, caseTz, useCaseData } from "../workspace/caseContext";
import { BASEMAPS, isDarkBasemap, styleFor, type BasemapId } from "./basemaps";
import { EMPTY, candidateGeometry, heatImage, positionHeadingAt, solveGeometry, strikesFC, withCategories } from "./geometry";
import { INTERACTIVE_LAYERS, newSyncState, syncOverlays, type OverlayData } from "./layers";
import { registerMap } from "./registry";
import { locationLabel } from "../alerts/AlertsTab";

setWorkerUrl(workerUrl);

export function MapPanel() {
  const c = useCaseData();
  const theme = useTheme();
  const el = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MLMap | null>(null);
  const syncState = useRef(newSyncState());
  const [styleGen, setStyleGen] = useState(0);
  const [basemap, setBasemapState] = useState<BasemapId>(() => loadPref<BasemapId>("ts.basemap", "auto"));
  const [offline, setOffline] = useState<null | "style" | "tiles">(null);
  const styleOk = useRef(false);
  // set on "style.load"; isStyleLoaded() is false while tiles load, which would skip overlay updates
  const styleReady = useRef(false);
  const tileErrors = useRef(0);
  const effective: BasemapId = offline === "style" ? "plain" : basemap;
  const dark = isDarkBasemap(effective, theme);

  const layers = useWs((s) => s.layers);
  const mapTool = useWs((s) => s.mapTool);
  const tab = useWs((s) => s.tab);
  const searchId = useWs((s) => s.searchId);
  const rank = useWs((s) => s.candidateRank);
  const solveId = useWs((s) => s.solveId);
  const flightIdx = useWs((s) => s.flightIdx);
  const pickPoint = useWs((s) => s.pickPoint);
  const eventPick = useWs((s) => s.eventPick);
  const mapFocus = useWs((s) => s.mapFocus);
  const checked = useWs((s) => s.checkedEventIds);
  const flightsDuringOnly = useWs((s) => s.flightsDuringOnly);
  const patch = usePatchCase(c.id);
  // stable handle for map event handlers (the mutation object changes every render)
  const patchRef = useRef(patch.mutate);
  patchRef.current = patch.mutate;

  const setBasemap = (b: BasemapId) => {
    setBasemapState(b);
    savePref("ts.basemap", b);
    setOffline(null);
  };

  // ---- data -------------------------------------------------------------------------------
  const flightSet = useFlightSet(c.id);
  const haveFlights = flightSet.data?.status === "ready" && flightSet.data.n_flights > 0;
  const D = caseDuration(c);
  const during = flightsDuringOnly && c.start_utc != null;
  const flights = useFlightsGeo(c.id, layers.flights && haveFlights, during ? Math.floor(c.start_utc! - 300) : null, during ? Math.ceil(c.start_utc! + D + 300) : null);
  const search = useSearch(c.id, searchId);
  const lightning = useLightning(c.id);
  const strikes = useStrikes(c.id, layers.strikes && !!lightning.data?.strikes?.count);
  const solve = useSolve(c.id, solveId);
  const enf = useEnf(c.id);
  const gridMap = useGridMap(layers.enfGrid || effective === "plain");
  const alerts = useAlerts(c.id, tab === "alerts");
  const alertId = useWs((s) => s.alertId);

  // flights with a helicopter flag (category comes from the flight set, not the GeoJSON)
  const flightsFC = useMemo(() => {
    const cats = new Map((flightSet.data?.flights ?? []).map((f) => [f.idx, f.category]));
    return withCategories(flights.data, cats);
  }, [flights.data, flightSet.data]);

  const cand = useMemo(() => {
    const s = search.data;
    if (!s) return null;
    return s.candidates.find((x) => x.rank === rank) ?? null;
  }, [search.data, rank]);

  // heat maps are rasterized once per result (max score per pixel at the finest grid)
  const aHeatImg = useMemo(() => {
    const spacing = Number((search.data?.request as { spacing_m?: number } | undefined)?.spacing_m) || (search.data?.mode === "refine" ? 50 : 200);
    return heatImage(search.data?.heat, spacing);
  }, [search.data]);
  const lHeatImg = useMemo(() => {
    const spacing = Number((solve.data?.request as { spacing_m?: number } | undefined)?.spacing_m) || 250;
    return heatImage(solve.data?.heat, spacing);
  }, [solve.data]);

  const overlay: OverlayData = useMemo(() => {
    const sgeo = solveGeometry(solve.data, strikes.data?.strikes);
    const cg = candidateGeometry(cand, flightsFC, caseC(c));
    return {
      dark,
      land: effective === "plain" ? ((gridMap.data as unknown as OverlayData["land"]) ?? null) : null,
      enfGrid: (gridMap.data as unknown as OverlayData["enfGrid"]) ?? null,
      enfNominal: enf.data?.analysis?.nominal_hz ?? null,
      region: c.region && validBBox(c.region) ? { type: "FeatureCollection", features: [bboxPolygon(c.region)] } : EMPTY,
      regionDraft: EMPTY,
      aHeat: aHeatImg,
      flights: (flightsFC as unknown as OverlayData["flights"]) ?? EMPTY,
      flightsAttribution: haveFlights && layers.flights ? flightSet.data?.attribution || "Flights: adsb.lol (ODbL)" : "",
      flightHl: cand ? cand.flights.map((f) => f.flight_idx) : [],
      flightSel: flightIdx,
      candLinks: cg.links,
      candCpa: cg.cpa,
      lHeat: lHeatImg,
      ranges: sgeo.ranges,
      lMatch: sgeo.lMatch,
      strikes: strikesFC(strikes.data?.strikes, sgeo.matched),
      lUnc: sgeo.lUnc,
      hoverAc: EMPTY,
      vis: {
        land: effective === "plain",
        enfGrid: layers.enfGrid,
        region: layers.region,
        aHeat: layers.aircraftHeat,
        flights: layers.flights,
        cand: layers.candidates,
        strikes: layers.strikes,
        lSolve: layers.lightning,
        hoverAc: layers.flights,
      },
    };
  }, [dark, effective, gridMap.data, enf.data, c, search.data, flightsFC, flightSet.data, haveFlights, cand, flightIdx, solve.data, strikes.data, layers, aHeatImg, lHeatImg]);

  const overlayRef = useRef(overlay);
  overlayRef.current = overlay;

  // ---- map lifecycle ------------------------------------------------------------------------
  useEffect(() => {
    const region = c.region && validBBox(c.region) ? c.region : null;
    const map = new MLMap({
      container: el.current!,
      style: styleFor(effective, theme),
      center: [0, 25],
      zoom: 1.3,
      attributionControl: false,
      dragRotate: false,
      pitchWithRotate: false,
      maxPitch: 0,
      fadeDuration: 100,
    });
    if (region)
      map.fitBounds(
        [
          [region.lon_min, region.lat_min],
          [region.lon_max, region.lat_max],
        ],
        { padding: 40, duration: 0 },
      );
    map.addControl(new NavigationControl({ showCompass: false }), "top-right");
    map.addControl(new ScaleControl({ unit: "metric", maxWidth: 110 }), "bottom-left");
    map.addControl(
      new AttributionControl({ compact: false }),
      "bottom-right",
    );
    map.touchZoomRotate.disableRotation();
    // some hosted styles reference sprite images they don't ship: fill them with a blank pixel
    map.setMissingStyleImageResolver((id) => {
      if (!map.hasImage(id)) map.addImage(id, { width: 1, height: 1, data: new Uint8Array(4) });
    });
    map.on("style.load", () => {
      styleOk.current = true;
      styleReady.current = true;
      syncState.current = newSyncState();
      try {
        syncOverlays(map, overlayRef.current, syncState.current);
      } catch (e) {
        console.warn("overlay sync failed", e);
      }
      setStyleGen((g) => g + 1);
    });
    map.on("error", (e) => {
      const ev = e as unknown as { sourceId?: string; error?: { message?: string; status?: number } };
      if (!styleOk.current && !ev.sourceId) {
        setOffline("style");
        return;
      }
      if (ev.sourceId && !ev.sourceId.startsWith("ts-")) {
        tileErrors.current++;
        if (tileErrors.current === 6) setOffline((o) => o ?? "tiles");
      }
    });
    mapRef.current = map;
    registerMap(map);
    const ro = new ResizeObserver(() => map.resize());
    ro.observe(el.current!);
    return () => {
      ro.disconnect();
      registerMap(null);
      map.remove();
      mapRef.current = null;
    };
    // created once per case workspace
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // basemap / theme switch
  const firstStyle = useRef(true);
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    if (firstStyle.current) {
      firstStyle.current = false;
    } else {
      styleOk.current = false;
      styleReady.current = false;
      tileErrors.current = 0;
      map.setStyle(styleFor(effective, theme), { diff: false });
    }
    const style = styleFor(effective, theme);
    if (typeof style !== "string") return;
    const h = window.setTimeout(() => {
      if (!styleOk.current) setOffline("style");
    }, 9000);
    return () => window.clearTimeout(h);
  }, [effective, theme]);

  // overlays
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !styleReady.current) return;
    try {
      syncOverlays(map, overlay, syncState.current);
    } catch (e) {
      console.warn("overlay sync failed", e);
    }
  }, [overlay, styleGen]);

  // focus requests (summary "zoom to", candidate selection, ...)
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapFocus) return;
    if (mapFocus.bbox && validBBox(mapFocus.bbox)) {
      const b = mapFocus.bbox;
      map.fitBounds(
        [
          [b.lon_min, b.lat_min],
          [b.lon_max, b.lat_max],
        ],
        { padding: 50, maxZoom: 15, duration: 700 },
      );
    } else if (mapFocus.center) {
      map.flyTo({ center: [mapFocus.center.lon, mapFocus.center.lat], zoom: mapFocus.zoom ?? Math.max(map.getZoom(), 12), duration: 800 });
    }
  }, [mapFocus]);

  // aircraft positions at the timeline cursor / playhead
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    let last: number | null = null;
    const update = () => {
      const s = ws.get();
      const t = s.hoverT ?? (s.playback.state !== "idle" ? s.playback.t : null);
      const start = c.start_utc;
      const fc = flightsFC as FlightsGeoJSON | undefined;
      const key = t == null || start == null || !fc ? null : Math.round(t * 2);
      if (key === last) return;
      last = key;
      const src = map.getSource("ts-hoverAc") as { setData?: (d: unknown) => void } | undefined;
      if (!src?.setData) return;
      if (key == null || !fc || start == null || t == null) {
        src.setData(EMPTY);
        return;
      }
      const utc = start + t;
      const features = [];
      for (const f of fc.features) {
        const p = positionHeadingAt(f, utc);
        if (p)
          features.push({
            type: "Feature",
            properties: { reg: f.properties.reg ?? f.properties.icao, bearing: p[2], heli: (f.properties as { heli?: boolean }).heli === true },
            geometry: { type: "Point", coordinates: [p[0], p[1]] },
          });
      }
      src.setData({ type: "FeatureCollection", features });
    };
    return ws.subscribe(update);
  }, [flightsFC, c.start_utc, styleGen]);

  // ---- region drawing and clicks ------------------------------------------------------------
  const [clickAt, setClickAt] = useState<{ lng: number; lat: number } | null>(null);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const canvas = map.getCanvas();
    if (mapTool === "region") {
      map.dragPan.disable();
      map.boxZoom.disable();
      map.doubleClickZoom.disable();
      canvas.style.cursor = "crosshair";
    } else if (mapTool === "pick-point" || mapTool === "pick-event") {
      canvas.style.cursor = "crosshair";
    } else {
      canvas.style.cursor = "";
    }
    let start: { lng: number; lat: number } | null = null;
    const draft = (b: BBox | null) => {
      const src = map.getSource("ts-regionDraft") as { setData?: (d: unknown) => void } | undefined;
      src?.setData?.(b ? { type: "FeatureCollection", features: [bboxPolygon(b)] } : EMPTY);
    };
    const down = (e: MapMouseEvent) => {
      if (mapTool !== "region") return;
      e.preventDefault();
      start = { lng: e.lngLat.lng, lat: e.lngLat.lat };
    };
    const move = (e: MapMouseEvent) => {
      if (mapTool !== "region" || !start) return;
      draft(normalizeBBox({ lat: start.lat, lon: start.lng }, { lat: e.lngLat.lat, lon: e.lngLat.lng }));
    };
    const up = (e: MapMouseEvent) => {
      if (mapTool !== "region" || !start) return;
      const b = normalizeBBox({ lat: start.lat, lon: start.lng }, { lat: e.lngLat.lat, lon: e.lngLat.lng });
      start = null;
      draft(null);
      if (b.lat_max - b.lat_min < 1e-4 || b.lon_max - b.lon_min < 1e-4) return;
      ws.set({ mapTool: "none" });
      patchRef.current(
        { region: b },
        { onError: (err) => toast({ tone: "error", title: "Could not save the region", body: errorMessage(err) }) },
      );
    };
    const click = (e: MapMouseEvent) => {
      const s = ws.get();
      if (s.mapTool === "region") return;
      const p = { lat: e.lngLat.lat, lon: e.lngLat.lng };
      if (s.mapTool === "pick-point") {
        ws.set({ pickPoint: p, mapTool: "none", searchMode: "point" });
        return;
      }
      if (s.mapTool === "pick-event") {
        ws.set({ eventPick: p, mapTool: "none" });
        return;
      }
      const hits = map.queryRenderedFeatures(e.point, { layers: INTERACTIVE_LAYERS.filter((l) => map.getLayer(l)) });
      const flight = hits.find((h) => h.layer.id.startsWith("ts-flights"));
      if (flight) {
        const idx = Number(flight.properties?.idx);
        ws.set({ flightIdx: s.flightIdx === idx ? null : idx });
        return;
      }
      if (s.tab === "aircraft" && s.checkedEventIds.length >= 2) setClickAt({ lng: p.lon, lat: p.lat });
    };
    map.on("mousedown", down);
    map.on("mousemove", move);
    map.on("mouseup", up);
    map.on("click", click);
    return () => {
      map.off("mousedown", down);
      map.off("mousemove", move);
      map.off("mouseup", up);
      map.off("click", click);
      map.dragPan.enable();
      map.boxZoom.enable();
      map.doubleClickZoom.enable();
      canvas.style.cursor = "";
    };
  }, [mapTool]);

  // hover tooltips for flights and strikes
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const popup = new Popup({ closeButton: false, closeOnClick: false, offset: 10, maxWidth: "260px" });
    const tz = caseTz(c);
    const esc = (s: unknown) => String(s ?? "").replace(/[<>&"]/g, (ch) => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;" })[ch]!);
    const move = (e: MapMouseEvent & { features?: MapGeoJSONFeature[] }) => {
      if (ws.get().mapTool !== "none") return;
      const f = e.features?.[0];
      if (!f) return;
      map.getCanvas().style.cursor = "pointer";
      const p = f.properties ?? {};
      let html = "";
      if (f.layer.id.startsWith("ts-flights")) {
        const t0 = Number(p.t_start_utc);
        const t1 = Number(p.t_end_utc);
        html = `<div style="font-weight:600">${esc(p.reg || p.icao)} <span style="opacity:.6;font-weight:500">${esc(p.type || "")}</span></div>
          <div style="opacity:.75">${esc(p.desc || "")}</div>
          <div style="opacity:.6;margin-top:2px">${Number.isFinite(t0) ? `${fmtClock(t0, "UTC", false)}–${fmtClock(t1, "UTC", false)} UTC` : ""}</div>`;
      } else {
        const t = Number(p.t_utc);
        const isEvent = p.event === true || p.event === "true";
        const matched = p.matched === true || p.matched === "true";
        const when = Number.isFinite(t) ? `${fmtDateIso(t, "UTC")} ${fmtClock(t, "UTC")} UTC · ${fmtClock(t, tz)} local` : "";
        const extra: string[] = [];
        if (isEvent && p.energy != null && p.energy !== "null" && String(p.source).startsWith("usgs")) extra.push(`M ${Number(p.energy).toFixed(1)}`);
        if (p.depth_m != null && p.depth_m !== "null") extra.push(`depth ${(Number(p.depth_m) / 1000).toFixed(1)} km`);
        if (p.sigma_m != null) extra.push(`±${Math.round(Number(p.sigma_m))} m`);
        html = `<div style="font-weight:600;text-transform:${isEvent ? "capitalize" : "none"}">${isEvent ? esc(p.kind || "event") : `Lightning ${esc(p.kind || "strike")}`}</div>
          <div style="opacity:.75">${when}</div>
          <div style="opacity:.6">${esc(p.source || "")}${p.ref && p.ref !== "null" ? ` · ${esc(p.ref)}` : ""}${extra.length ? ` · ${extra.join(" · ")}` : ""}${matched ? " · matched" : ""}</div>`;
      }
      popup.setLngLat(e.lngLat).setHTML(html).addTo(map);
    };
    const leave = () => {
      popup.remove();
      if (ws.get().mapTool === "none") map.getCanvas().style.cursor = "";
    };
    for (const l of INTERACTIVE_LAYERS) {
      map.on("mousemove", l, move);
      map.on("mouseleave", l, leave);
    }
    return () => {
      for (const l of INTERACTIVE_LAYERS) {
        map.off("mousemove", l, move);
        map.off("mouseleave", l, leave);
      }
      popup.remove();
    };
  }, [c]);

  // ---- markers --------------------------------------------------------------------------------
  const markerItems = useMemo(() => {
    const items: MarkerItem[] = [];
    if (layers.candidates && search.data) {
      for (const k of search.data.candidates.slice(0, 10).reverse()) {
        items.push({
          id: `cand-${search.data.id}-${k.rank}`,
          lng: k.lon,
          lat: k.lat,
          kind: "cand",
          label: String(k.rank),
          active: k.rank === rank,
          best: k.rank === 1,
          onClick: () => ws.set({ candidateRank: k.rank }),
          title: `Candidate ${k.rank} · score ${k.score.toFixed(2)}`,
        });
      }
    }
    if (layers.lightning && solve.data?.best) {
      const b = solve.data.best;
      items.push({ id: `lbest-${solve.data.id}`, lng: b.lon, lat: b.lat, kind: "lightning", label: "", active: true, title: "Lightning solve: best point" });
    }
    if (tab === "alerts") {
      for (const h of alerts.data?.headers ?? []) {
        for (const l of h.locations) {
          if (l.lat == null || l.lon == null) continue;
          items.push({
            id: `alert-${h.id}-${l.code}`,
            lng: l.lon,
            lat: l.lat,
            kind: "alert",
            label: h.event,
            active: h.id === alertId,
            title: `${h.event_name}: ${locationLabel(l)} (county centre)`,
            onClick: () => ws.set({ alertId: h.id }),
          });
        }
      }
    }
    if (eventPick && tab === "lightning") {
      items.push({ id: "event-pick", lng: eventPick.lon, lat: eventPick.lat, kind: "pick", label: "", active: true, title: "Location of the event being added" });
    }
    if (pickPoint && (tab === "aircraft" || mapTool === "pick-point")) {
      items.push({ id: "pick", lng: pickPoint.lon, lat: pickPoint.lat, kind: "pick", label: "", active: true, title: "Known point to check" });
    }
    return items;
  }, [layers.candidates, layers.lightning, search.data, rank, solve.data, pickPoint, eventPick, tab, mapTool, alerts.data, alertId]);
  useMarkers(mapRef, markerItems);

  // close the click popup when its preconditions go away
  useEffect(() => {
    if (tab !== "aircraft" || checked.length < 2) setClickAt(null);
  }, [tab, checked.length]);

  return (
    <div className="relative h-full w-full overflow-hidden bg-surface-2">
      {/* MapLibre's own CSS makes its container position:relative, so size it from a wrapper */}
      <div className="absolute inset-0">
        <div ref={el} className="h-full w-full" data-testid="map" />
      </div>
      <MapToolbar basemap={basemap} setBasemap={setBasemap} effective={effective} hasGrid={!gridMap.error} />
      {mapTool !== "none" && (
        <div className="pointer-events-none absolute top-3 left-1/2 z-10 -translate-x-1/2 rounded-full border border-line-strong bg-surface/95 px-3 py-1.5 text-xs text-fg shadow-pop">
          {mapTool === "region"
            ? "Drag a rectangle to set the search region"
            : mapTool === "pick-event"
              ? "Click the map where the event happened"
              : "Click the map to choose the point to check"}{" "}
          ·{" "}
          <span className="text-fg-muted">Esc to cancel</span>
        </div>
      )}
      {offline && (
        <div className="absolute top-14 left-3 z-10 flex max-w-[360px] items-start gap-2.5 rounded-lg border border-line-strong bg-surface/95 px-3 py-2 text-xs shadow-pop backdrop-blur">
          <WifiOff className="mt-0.5 size-3.5 shrink-0 text-warn" />
          <div className="min-w-0">
            <div className="font-medium text-fg">{offline === "style" ? "Basemap unavailable" : "Basemap tiles aren't loading"}</div>
            <div className="mt-0.5 text-fg-muted">
              {offline === "style" ? "Couldn't reach the map style server (offline?). Showing a plain map; everything else still works." : "You may be offline. Results still draw on the map."}
            </div>
            <div className="mt-1.5 flex gap-2">
              <button className="font-medium text-accent hover:underline" onClick={() => setOffline(null)}>
                Retry
              </button>
              {offline === "tiles" && (
                <button className="font-medium text-accent hover:underline" onClick={() => setBasemap("plain")}>
                  Use plain map
                </button>
              )}
            </div>
          </div>
          <button className="-mt-0.5 -mr-1 text-fg-faint hover:text-fg" onClick={() => setOffline(offline === "style" ? "style" : null)} aria-label="Dismiss">
            {offline === "tiles" && <X className="size-3.5" />}
          </button>
        </div>
      )}
      <Legend overlay={overlay} hasSearchHeat={!!search.data?.heat?.length} hasSolve={!!solve.data?.heat?.length} esri={effective === "satellite"} />
      {clickAt && mapRef.current && (
        <ClickPopup map={mapRef.current} at={clickAt} onClose={() => setClickAt(null)}>
          <div className="w-[220px]">
            <div className="text-xs font-semibold text-fg">Known-point check</div>
            <div className="mt-0.5 text-2xs text-fg-muted tnum">{fmtCoord(clickAt.lat, clickAt.lng, 5)}</div>
            <div className="mt-1.5 text-2xs leading-snug text-fg-muted">Score just this point against the two selected flyovers (search mode “point”).</div>
            <Button
              size="sm"
              variant="primary"
              className="mt-2 w-full"
              icon={<Crosshair />}
              onClick={() => {
                ws.set({ pickPoint: { lat: clickAt.lat, lon: clickAt.lng }, searchMode: "point" });
                setClickAt(null);
                document.getElementById("aircraft-search")?.scrollIntoView({ behavior: "smooth", block: "start" });
              }}
            >
              Check this point
            </Button>
          </div>
        </ClickPopup>
      )}
    </div>
  );
}

// ---- toolbar ---------------------------------------------------------------------------------

function MapToolbar({ basemap, setBasemap, effective, hasGrid }: { basemap: BasemapId; setBasemap: (b: BasemapId) => void; effective: BasemapId; hasGrid: boolean }) {
  const mapTool = useWs((s) => s.mapTool);
  const layers = useWs((s) => s.layers);
  const lp = usePopover();
  const bp = usePopover();
  const flightsDuringOnly = useWs((s) => s.flightsDuringOnly);
  const L: { k: keyof Layers; label: string; hint?: string }[] = [
    { k: "region", label: "Search region" },
    { k: "flights", label: "Flights (ADS-B)", hint: "Prop and unknown-type tracks for the case date" },
    { k: "aircraftHeat", label: "Aircraft search heat", hint: "Score at every grid point of the selected search" },
    { k: "candidates", label: "Aircraft candidates", hint: "Ranked best points and the flights they heard" },
    { k: "strikes", label: "Lightning strikes", hint: "Coloured by time" },
    { k: "lightning", label: "Lightning solve", hint: "Heat, best point, uncertainty and flash-to-bang ranges" },
    { k: "enfGrid", label: "Mains frequency map", hint: "Countries by 50/60 Hz grid" },
  ];
  return (
    <div className="absolute top-3 left-3 z-10 flex items-center gap-1 rounded-lg border border-line-strong bg-surface/95 p-1 shadow-pop backdrop-blur">
      <Button
        size="sm"
        variant="ghost"
        active={mapTool === "region"}
        icon={<SquareDashed />}
        onClick={() => ws.set({ mapTool: mapTool === "region" ? "none" : "region" })}
        tip="Drag a rectangle on the map to set the search region (R)"
      >
        Draw region
      </Button>
      <div className="mx-0.5 h-4 w-px bg-line" />
      <IconButton ref={lp.anchor} label="Layers" active={lp.open} onClick={lp.toggle}>
        <LayersIcon />
      </IconButton>
      <Popover open={lp.open} onClose={() => lp.setOpen(false)} anchor={lp.anchor} align="start" width={270}>
        <div className="p-3">
          <div className="mb-2 text-2xs font-semibold tracking-wider text-fg-muted uppercase">Layers</div>
          <div className="flex flex-col gap-2.5">
            {L.map((l) => (
              <Switch key={l.k} checked={layers[l.k]} onChange={(v) => toggleLayer(l.k, v)} label={l.label} hint={l.hint} disabled={l.k === "enfGrid" && !hasGrid} />
            ))}
            <div className="border-t border-line pt-2.5">
              <Switch
                checked={flightsDuringOnly}
                onChange={(v) => ws.set({ flightsDuringOnly: v })}
                label="Only flights during the recording"
                hint="Needs a start time"
              />
            </div>
          </div>
        </div>
      </Popover>
      <IconButton ref={bp.anchor} label="Basemap" active={bp.open} onClick={bp.toggle}>
        <MapIcon />
      </IconButton>
      <Popover open={bp.open} onClose={() => bp.setOpen(false)} anchor={bp.anchor} align="start" width={230}>
        <div className="p-1.5">
          <div className="px-2 pt-1.5 pb-1 text-2xs font-semibold tracking-wider text-fg-muted uppercase">Basemap</div>
          {BASEMAPS.map((b) => (
            <button
              key={b.id}
              onClick={() => {
                setBasemap(b.id);
                bp.setOpen(false);
              }}
              className={cn(
                "flex w-full items-center justify-between gap-2 rounded-md px-2 py-1.5 text-left text-xs",
                basemap === b.id ? "bg-accent-soft text-accent" : "text-fg hover:bg-surface-3",
              )}
            >
              <span className="font-medium">{b.label}</span>
              <span className={cn("text-2xs", basemap === b.id ? "text-accent/80" : "text-fg-faint")}>{b.hint}</span>
            </button>
          ))}
          {effective !== basemap && <div className="px-2 py-1 text-2xs text-warn">Showing the plain map: the selected basemap failed to load.</div>}
        </div>
      </Popover>
    </div>
  );
}

// ---- legend ----------------------------------------------------------------------------------

function Legend({ overlay, hasSearchHeat, hasSolve, esri }: { overlay: OverlayData; hasSearchHeat: boolean; hasSolve: boolean; esri: boolean }) {
  const rows: ReactNode[] = [];
  const v = overlay.vis;
  if ((v.aHeat && hasSearchHeat) || (v.lSolve && hasSolve)) {
    rows.push(
      <div key="heat" className="flex items-center gap-2">
        <span className="w-[54px] text-fg-muted">Score</span>
        <span className="text-fg-faint">low</span>
        <span className="h-1.5 w-20 rounded-full" style={{ background: colormapGradient("magma") }} />
        <span className="text-fg-faint">high</span>
      </div>,
    );
  }
  if (v.strikes && overlay.strikes.features.length) {
    rows.push(
      <div key="strikes" className="flex items-center gap-2">
        <span className="w-[54px] text-fg-muted">Strikes</span>
        <span className="text-fg-faint">early</span>
        <span className="h-1.5 w-20 rounded-full" style={{ background: "linear-gradient(to right,#60a5fa,#facc15,#f43f5e)" }} />
        <span className="text-fg-faint">late</span>
      </div>,
    );
  }
  if (v.enfGrid && overlay.enfGrid?.features.length) {
    rows.push(
      <div key="enf" className="flex items-center gap-2.5">
        <span className="w-[54px] text-fg-muted">Mains</span>
        {[
          ["#60a5fa", "50 Hz"],
          ["#f472b6", "60 Hz"],
          ["#a78bfa", "both"],
        ].map(([c, l]) => (
          <span key={l} className="flex items-center gap-1">
            <span className="size-2 rounded-sm" style={{ background: c }} />
            {l}
          </span>
        ))}
      </div>,
    );
  }
  if (v.flights && overlay.flights.features.length) {
    const nHeli = overlay.flights.features.filter((f) => (f.properties as { heli?: boolean } | null)?.heli).length;
    rows.push(
      <div key="fl" className="flex items-center gap-2">
        <span className="w-[54px] text-fg-muted">Flights</span>
        <span className="text-fg-faint tnum">{overlay.flights.features.length} tracks</span>
        {nHeli > 0 && (
          <span className="flex items-center gap-1">
            <span className="h-0.5 w-3 rounded bg-[#f0abfc]" /> {nHeli} helicopter{nHeli > 1 ? "s" : ""}
          </span>
        )}
        {overlay.flightHl.length > 0 && (
          <span className="flex items-center gap-1">
            <span className="h-0.5 w-3 rounded bg-[#67e8f9]" /> heard by candidate
          </span>
        )}
      </div>,
    );
  }
  if (!rows.length) return null;
  return (
    <div className={cn("pointer-events-none absolute left-3 z-10 flex flex-col gap-1 rounded-lg border border-line-strong bg-surface/90 px-2.5 py-2 text-2xs shadow-pop backdrop-blur", esri ? "bottom-12" : "bottom-10")}>
      {rows}
    </div>
  );
}

// ---- DOM markers -----------------------------------------------------------------------------

type MarkerItem = {
  id: string;
  lng: number;
  lat: number;
  kind: "cand" | "lightning" | "pick" | "alert";
  label: string;
  active: boolean;
  best?: boolean;
  title?: string;
  onClick?: () => void;
};

function markerEl(m: MarkerItem): HTMLElement {
  const el = document.createElement("div");
  el.title = m.title ?? "";
  if (m.kind === "cand") {
    el.className = "ts-marker";
    el.innerHTML = `<div style="display:flex;align-items:center;justify-content:center;min-width:22px;height:22px;padding:0 5px;border-radius:999px;font:600 11px Inter Variable,Inter,system-ui;cursor:pointer;
      background:${m.active ? "#67e8f9" : m.best ? "#0b0e13" : "rgba(11,14,19,0.85)"};color:${m.active ? "#06080c" : "#e6e9ef"};
      border:2px solid ${m.active ? "#ffffff" : m.best ? "#67e8f9" : "rgba(255,255,255,0.55)"};box-shadow:0 2px 8px rgba(0,0,0,.45)${m.active ? ",0 0 0 4px rgba(103,232,249,.25)" : ""}">${m.label}</div>`;
  } else if (m.kind === "alert") {
    el.innerHTML = `<div style="display:flex;align-items:center;gap:4px;height:20px;padding:0 6px 0 4px;border-radius:999px;cursor:pointer;font:600 10px Inter Variable,Inter,system-ui;
      background:${m.active ? "#fb923c" : "rgba(11,14,19,0.88)"};color:${m.active ? "#1a0d02" : "#fdba74"};border:1.5px solid ${m.active ? "#fff" : "#fb923c"};box-shadow:0 2px 8px rgba(0,0,0,.45)">
      <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M4.9 16.1C1 12.2 1 5.8 4.9 1.9"/><path d="M7.8 4.7a6.14 6.14 0 0 0-.8 7.5"/><circle cx="12" cy="9" r="2"/><path d="M16.2 4.8c2 2 2.26 5.11.8 7.47"/><path d="M19.1 1.9a9.96 9.96 0 0 1 0 14.1"/><path d="M9.5 18h5"/><path d="m8 22 4-11 4 11"/></svg>${m.label}</div>`;
  } else if (m.kind === "lightning") {
    el.innerHTML = `<div style="width:26px;height:26px;border-radius:999px;background:#0b0e13;border:2px solid #facc15;display:flex;align-items:center;justify-content:center;box-shadow:0 2px 8px rgba(0,0,0,.45),0 0 0 4px rgba(250,204,21,.2)">
      <svg width="13" height="13" viewBox="0 0 24 24" fill="#facc15"><path d="M13 2 3 14h7l-1 8 10-12h-7z"/></svg></div>`;
  } else {
    el.innerHTML = `<div style="width:22px;height:22px;border-radius:999px;border:2px solid #fff;background:rgba(45,212,191,.25);box-shadow:0 0 0 2px #0d9488,0 2px 8px rgba(0,0,0,.5);display:flex;align-items:center;justify-content:center">
      <div style="width:4px;height:4px;border-radius:999px;background:#fff"></div></div>`;
  }
  if (m.onClick) {
    el.addEventListener("click", (e) => {
      e.stopPropagation();
      m.onClick?.();
    });
  }
  return el;
}

function useMarkers(mapRef: React.RefObject<MLMap | null>, items: MarkerItem[]) {
  const live = useRef(new Map<string, { marker: Marker; key: string }>());
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const seen = new Set<string>();
    for (const m of items) {
      seen.add(m.id);
      const key = `${m.lng}|${m.lat}|${m.label}|${m.active}|${m.best}`;
      const cur = live.current.get(m.id);
      if (cur && cur.key === key) continue;
      cur?.marker.remove();
      const marker = new Marker({ element: markerEl(m), anchor: "center" }).setLngLat([m.lng, m.lat]).addTo(map);
      live.current.set(m.id, { marker, key });
    }
    for (const [id, cur] of live.current) {
      if (!seen.has(id)) {
        cur.marker.remove();
        live.current.delete(id);
      }
    }
  }, [items, mapRef]);
  useEffect(() => {
    const l = live.current;
    return () => {
      for (const cur of l.values()) cur.marker.remove();
      l.clear();
    };
  }, []);
}

// ---- click popup (React content inside a MapLibre popup) ------------------------------------

function ClickPopup({ map, at, onClose, children }: { map: MLMap; at: { lng: number; lat: number }; onClose: () => void; children: ReactNode }) {
  const [node] = useState(() => document.createElement("div"));
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    const p = new Popup({ closeButton: false, closeOnClick: true, offset: 8, maxWidth: "260px" }).setLngLat([at.lng, at.lat]).setDOMContent(node).addTo(map);
    p.on("close", () => closeRef.current());
    return () => {
      p.remove();
    };
  }, [map, at, node]);
  return createPortal(
    <div className="relative">
      <button className="absolute -top-0.5 -right-1 text-fg-faint hover:text-fg" onClick={onClose} aria-label="Close">
        <X className="size-3.5" />
      </button>
      {children}
    </div>,
    node,
  );
}

