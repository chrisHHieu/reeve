// A map: countries shaded by a value and places pinned by their coordinates, on a map
// that pans, zooms and flies to what is clicked (MapLibre).
//
// Drawn on the online basemap (OpenFreeMap: streets, places, terrain from OpenStreetMap) unless
// the person turned it off in Settings > Visuals, or it cannot be reached; then on the bundled
// country outlines alone (`worldTopo.json`), with nothing fetched. It opens on what it carries:
// the countries and points with a margin, or street level on a single place. In the answer the
// wheel scrolls the conversation and Ctrl+wheel zooms; expanded, the wheel zooms.
//
// A country's shade runs from the panel to the accent, lightest for the smallest value. Hovering
// names a country or a place with its value; clicking flies to it; "Reset view" flies back.

import "maplibre-gl/dist/maplibre-gl.css";
// The map's tile worker, bundled by Vite into one file of its own: MapLibre looks for it beside its
// own module, which Vite's dependency bundling moves, and the worker failed to load there.
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import { useEffect, useMemo, useRef, useState } from "react";
import type { Map as MapLibreMap, StyleSpecification } from "maplibre-gl";
import { formatValue, useVisualLocale } from "./format";
import { useExpanded } from "./expanded";
import { countryBox, loadCountries, openingOf, shareOf, unshaped, type Box, type Countries } from "./mapData";
import { useDarkTheme, useMapBasemap } from "./mapPrefs";
import type { MapSpec } from "./visualModel";

const BASEMAP = (dark: boolean) => `https://tiles.openfreemap.org/styles/${dark ? "dark" : "positron"}`;

/** The app's colours, as a WebGL style reads them: any CSS colour, normalised by the browser. */
function themeColors(element: HTMLElement) {
  const style = getComputedStyle(element);
  const probe = document.createElement("canvas").getContext("2d");
  const read = (name: string, fallback: string) => {
    const raw = style.getPropertyValue(name).trim() || fallback;
    if (!probe) return raw;
    probe.fillStyle = fallback;
    probe.fillStyle = raw;
    return String(probe.fillStyle);
  };
  return {
    accent: read("--accent", "#0d6a8a"),
    panel: read("--panel", "#fffefc"),
    paper: read("--paper", "#f0ede7"),
    canvas: read("--canvas", "#faf8f4"),
    line: read("--line-strong", "#cfcac0"),
    pin: read("--viz-2", "#8a6410"),
  };
}

type Colors = ReturnType<typeof themeColors>;

/** The outlines alone: a sea of the canvas, every country on the paper. */
function outlineStyle(colors: Colors): StyleSpecification {
  return { version: 8, sources: {}, layers: [{ id: "sea", type: "background", paint: { "background-color": colors.canvas } }] };
}

const PIN_LABELS = 10;

export function MapBlock({ spec: given }: { spec: MapSpec }) {
  // The same map by its content: a parent that reads its blocks again must not rebuild the map.
  const key = JSON.stringify(given);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const spec = useMemo(() => given, [key]);
  const locale = useVisualLocale();
  const expanded = useExpanded();
  const [basemap] = useMapBasemap();
  const dark = useDarkTheme();
  const host = useRef<HTMLDivElement>(null);
  const map = useRef<MapLibreMap | null>(null);
  const home = useRef<() => void>(() => {});
  const [countries, setCountries] = useState<Countries | null>(null);
  const [failed, setFailed] = useState(false);
  const [moved, setMoved] = useState(false);
  const [hover, setHover] = useState<{ name: string; value?: number; x: number; y: number } | null>(null);
  const unit = spec.unit;
  const range = useMemo(() => {
    const all = spec.regions.map((r) => r.value).filter(Number.isFinite);
    return all.length ? { min: Math.min(...all), max: Math.max(...all) } : null;
  }, [spec.regions]);

  useEffect(() => {
    let live = true;
    const markers: { remove(): void }[] = [];
    Promise.all([import("maplibre-gl"), loadCountries()])
      .then(([lib, loaded]) => {
        if (!live || !host.current) return;
        setCountries(loaded);
        if (lib.getWorkerUrl() !== workerUrl) lib.setWorkerUrl(workerUrl);
        const colors = themeColors(host.current);
        let online = basemap === "online";
        const opening = openingOf(spec, loaded);
        const pointMax = Math.max(0, ...spec.points.map((p) => Math.abs(p.value ?? 0)));
        const instance = new lib.Map({
          container: host.current,
          style: online ? BASEMAP(dark) : outlineStyle(colors),
          ...(opening && "box" in opening
            ? { bounds: [[opening.box[0], opening.box[1]], [opening.box[2], opening.box[3]]] as [[number, number], [number, number]], fitBoundsOptions: { padding: 36, maxZoom: 6 } }
            : opening
              ? { center: opening.center, zoom: online ? 14 : 4 }
              : { center: [0, 20] as [number, number], zoom: 0.6 }),
          cooperativeGestures: !expanded,
          attributionControl: { compact: true },
          canvasContextAttributes: { preserveDrawingBuffer: true },
          dragRotate: false,
          pitchWithRotate: false,
          touchPitch: false,
          renderWorldCopies: false,
          fadeDuration: 150,
        });
        map.current = instance;
        instance.touchZoomRotate.disableRotation();
        instance.addControl(new lib.NavigationControl({ showCompass: false }), "top-right");

        const fly = (box: Box, maxZoom: number) =>
          instance.fitBounds([[box[0], box[1]], [box[2], box[3]]], { padding: 40, maxZoom, duration: 900, essential: true });
        home.current = () => {
          if (opening && "box" in opening) fly(opening.box, 6);
          else if (opening) instance.flyTo({ center: opening.center, zoom: online ? 14 : 4, duration: 900, essential: true });
          else instance.flyTo({ center: [0, 20], zoom: 0.6, duration: 900, essential: true });
          window.setTimeout(() => setMoved(false), 950);
        };
        instance.on("dragstart", () => setMoved(true));
        // A readout names what is under the pointer; once the map moves, that is something else.
        instance.on("movestart", () => setHover(null));
        instance.on("zoomstart", (event) => {
          if ((event as { originalEvent?: unknown }).originalEvent) setMoved(true);
        });

        const values = new Map(spec.regions.map((r) => [r.id, r]));
        const shaded = {
          type: "FeatureCollection" as const,
          features: [...loaded.byId.entries()]
            .filter(([id]) => !online || values.has(id))
            .map(([id, feature]) => {
              const region = values.get(id);
              return {
                type: "Feature" as const,
                geometry: feature.geometry,
                properties: {
                  code: id,
                  name: region?.name ?? feature.properties.name,
                  valued: !!region,
                  value: region?.value ?? 0,
                  share: region ? shareOf(region.value, range) : 0,
                },
              };
            }),
        };
        // Added on every style load: a basemap that fails before it loads is replaced by the
        // outlines, and the data goes on whichever style is drawn.
        const addData = () => {
          if (instance.getSource("countries")) return;
          instance.addSource("countries", { type: "geojson", data: shaded, promoteId: "code" });
          // Under the basemap's names, so a shaded country keeps its labels.
          const firstLabel = instance.getStyle().layers.find((layer) => layer.type === "symbol")?.id;
          if (!online) {
            instance.addLayer({ id: "land", type: "fill", source: "countries", paint: { "fill-color": colors.paper } }, firstLabel);
          }
          instance.addLayer(
            {
              id: "shaded",
              type: "fill",
              source: "countries",
              filter: ["==", ["get", "valued"], true],
              paint: {
                "fill-color": ["interpolate", ["linear"], ["get", "share"], 0, colors.panel, 1, colors.accent],
                "fill-opacity": ["case", ["boolean", ["feature-state", "hover"], false], online ? 0.85 : 1, online ? 0.62 : 0.92],
              },
            },
            firstLabel,
          );
          instance.addLayer(
            {
              id: "edges",
              type: "line",
              source: "countries",
              paint: {
                "line-color": ["case", ["==", ["get", "valued"], true], colors.accent, online ? colors.line : colors.panel],
                "line-width": ["case", ["boolean", ["feature-state", "hover"], false], 2, ["==", ["get", "valued"], true], 0.8, 0.5],
                "line-opacity": ["case", ["==", ["get", "valued"], true], 0.8, 1],
              },
            },
            firstLabel,
          );
        };
        instance.on("style.load", addData);
        instance.on("error", (event) => {
          // The basemap cannot be reached (offline, blocked): draw on the outlines instead.
          if (!online || instance.isStyleLoaded()) return;
          const url = String((event.error as { url?: string } | undefined)?.url ?? "");
          if (url && !url.includes("openfreemap")) return;
          online = false;
          instance.setStyle(outlineStyle(colors));
        });

        let hovered: string | null = null;
        const unhover = () => {
          if (hovered) instance.setFeatureState({ source: "countries", id: hovered }, { hover: false });
          hovered = null;
        };
        instance.on("mousemove", "shaded", (event) => {
          const feature = event.features?.[0];
          if (!feature) return;
          const code = String(feature.properties.code);
          if (hovered !== code) {
            unhover();
            hovered = code;
            instance.setFeatureState({ source: "countries", id: code }, { hover: true });
          }
          instance.getCanvas().style.cursor = "pointer";
          setHover({ name: String(feature.properties.name), value: Number(feature.properties.value), x: event.point.x, y: event.point.y });
        });
        instance.on("mouseleave", "shaded", () => {
          unhover();
          instance.getCanvas().style.cursor = "";
          setHover(null);
        });
        instance.on("click", "shaded", (event) => {
          const code = event.features?.[0]?.properties.code;
          const box = code ? countryBox(loaded, String(code)) : null;
          if (box) {
            setMoved(true);
            fly(box, 6);
          }
        });

        // Places as pins with their names; many places name themselves only on hover.
        spec.points.forEach((point) => {
          const pin = document.createElement("button");
          pin.type = "button";
          pin.className = "vz-pin";
          if (spec.points.length <= PIN_LABELS && point.label) pin.dataset.labelled = "";
          const size = point.value !== undefined && pointMax > 0 ? 10 + Math.sqrt(Math.abs(point.value) / pointMax) * 16 : 12;
          const dot = document.createElement("span");
          dot.className = "vz-pin-dot";
          dot.style.width = dot.style.height = `${size.toFixed(1)}px`;
          dot.style.background = colors.pin;
          pin.append(dot);
          if (point.label) {
            const label = document.createElement("span");
            label.className = "vz-pin-label";
            label.textContent = point.label;
            pin.append(label);
          }
          pin.setAttribute("aria-label", point.label || `${point.lat}, ${point.lon}`);
          pin.addEventListener("mouseenter", () => {
            const at = instance.project([point.lon, point.lat]);
            setHover({ name: point.label, value: point.value, x: at.x, y: at.y });
          });
          pin.addEventListener("mouseleave", () => setHover(null));
          pin.addEventListener("click", (event) => {
            event.stopPropagation();
            setMoved(true);
            instance.flyTo({ center: [point.lon, point.lat], zoom: Math.max(instance.getZoom() + 2, online ? 13 : 5), duration: 900, essential: true });
          });
          markers.push(new lib.Marker({ element: pin, anchor: "left", offset: [-size / 2, 0] }).setLngLat([point.lon, point.lat]).addTo(instance));
        });
      })
      .catch(() => live && setFailed(true));
    return () => {
      live = false;
      markers.forEach((marker) => marker.remove());
      map.current?.remove();
      map.current = null;
    };
    // A new spec, basemap, theme or view is a new map: the style and its colours are set at creation.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [spec, basemap, dark, expanded]);

  const small = countries ? unshaped(spec, countries) : [];
  return (
    <div className="vz-map">
      <div className="vz-map-frame" data-expanded={expanded || undefined}>
        <div ref={host} className="vz-map-canvas" role="img" aria-label={spec.title ?? "map"} />
        {failed && <div className="vz-map-failed">This map could not be drawn here.</div>}
        {moved && (
          <button type="button" className="vz-map-reset" data-export-skip data-testid="map-reset" onClick={() => home.current()}>
            Reset view
          </button>
        )}
        {hover && (
          <div className="vz-readout vz-map-readout" style={{ left: hover.x, top: hover.y }}>
            <b>{hover.name}</b>
            {hover.value !== undefined && Number.isFinite(hover.value) && <span>{formatValue(hover.value, { unit, locale })}</span>}
          </div>
        )}
      </div>
      {/* A country with no outline at 1:50m (Gibraltar, Tuvalu) is listed, not lost. */}
      {small.length > 0 && (
        <div className="vz-map-small" data-testid="map-small">
          {small.map((r) => `${r.name} ${formatValue(r.value, { unit, locale })}`).join(" · ")}
        </div>
      )}
      {range && (
        <div className="vz-map-legend" aria-hidden>
          <span>{formatValue(range.min, { unit, locale })}</span>
          <i />
          <span>{formatValue(range.max, { unit, locale })}</span>
        </div>
      )}
    </div>
  );
}
