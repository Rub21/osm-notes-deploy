import { useEffect, useRef } from "react";
import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { api, COLORS } from "./api.js";

const STYLE = "https://tiles.openfreemap.org/styles/positron";
const EMPTY = { type: "FeatureCollection", features: [] };
const colorExpr = ["match", ["get", "c"], ...Object.entries(COLORS).flat(), "#555"];

/** The map. Loads points (or grid cells when zoomed out) for the current view. */
export default function MapView({ filters, selected, onSelect, onView, flyTo, start, t, onMode }) {
  const box = useRef(null);
  const map = useRef(null);
  const ready = useRef(false);
  const filtersRef = useRef(filters);
  const selectedRef = useRef(selected);
  const abort = useRef(null);
  const handlers = useRef({ onSelect, onView, onMode });
  handlers.current = { onSelect, onView, onMode };

  const load = () => {
    const m = map.current;
    if (!m || !ready.current) return;
    const b = m.getBounds();
    const bbox = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()];
    abort.current?.abort();
    abort.current = new AbortController();
    api.points(filtersRef.current, bbox, m.getZoom(), abort.current.signal)
      .then((fc) => {
        m.getSource("notes")?.setData(fc);
        handlers.current.onMode(fc.mode);
      })
      .catch((e) => { if (e.name !== "AbortError") console.warn("points", e); });
  };

  useEffect(() => {
    const m = new maplibregl.Map({
      container: box.current,
      style: STYLE,
      center: start ? [start.lon, start.lat] : [0, 20],
      zoom: start ? start.zoom : 1.6,
      attributionControl: false,
      maxPitch: 0,
      dragRotate: false,
    });
    map.current = m;
    m.touchZoomRotate.disableRotation();
    m.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    m.addControl(new maplibregl.AttributionControl({ compact: true, customAttribution: t.attribution }));
    m.on("load", () => {
      m.addSource("notes", { type: "geojson", data: EMPTY });
      m.addLayer({
        id: "grid", type: "circle", source: "notes", filter: ["has", "n"],
        paint: {
          "circle-color": "#1a1a1a",
          "circle-opacity": ["interpolate", ["linear"], ["sqrt", ["get", "n"]], 1, 0.55, 30, 0.8, 150, 0.9],
          "circle-radius": ["interpolate", ["linear"], ["sqrt", ["get", "n"]], 1, 8, 30, 18, 150, 30],
          "circle-stroke-color": colorExpr,
          "circle-stroke-width": 2,
          "circle-stroke-opacity": 0.9,
        },
      });
      m.addLayer({
        id: "grid-count", type: "symbol", source: "notes", filter: ["has", "n"],
        layout: {
          "text-field": ["case", [">=", ["get", "n"], 1000],
            ["concat", ["to-string", ["round", ["/", ["get", "n"], 1000]]], "k"],
            ["to-string", ["get", "n"]]],
          "text-font": ["Noto Sans Bold"],
          "text-size": 10.5,
          "text-allow-overlap": true,
        },
        paint: { "text-color": "#fff" },
      });
      m.addLayer({
        id: "points", type: "circle", source: "notes", filter: ["!", ["has", "n"]],
        paint: {
          "circle-color": colorExpr,
          "circle-radius": ["interpolate", ["linear"], ["zoom"], 4, 3.5, 10, 6, 16, 9],
          "circle-opacity": ["+", 0.35, ["*", 0.65, ["get", "p"]]],
          "circle-stroke-color": "#ffffff",
          "circle-stroke-width": 1,
        },
      });
      m.addLayer({
        id: "selected", type: "circle", source: "notes",
        filter: ["==", ["get", "id"], selectedRef.current?.note_id ?? -1],
        paint: {
          "circle-color": "rgba(0,0,0,0)",
          "circle-radius": ["interpolate", ["linear"], ["zoom"], 4, 9, 16, 15],
          "circle-stroke-color": "#111111",
          "circle-stroke-width": 2.5,
        },
      });
      ready.current = true;
      load();
      report();
    });

    const report = () => {
      const b = m.getBounds();
      const c = m.getCenter();
      handlers.current.onView({
        bbox: [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()],
        zoom: m.getZoom(), lat: c.lat, lon: c.lng,
      });
    };
    let timer;
    m.on("moveend", () => {
      clearTimeout(timer);
      timer = setTimeout(() => { load(); report(); }, 250);
    });
    m.on("click", "points", (e) => handlers.current.onSelect(e.features[0].properties.id));
    m.on("click", "grid", (e) => {
      m.easeTo({ center: e.features[0].geometry.coordinates, zoom: Math.min(m.getZoom() + 2.5, 16) });
    });
    for (const id of ["points", "grid"]) {
      m.on("mouseenter", id, () => { m.getCanvas().style.cursor = "pointer"; });
      m.on("mouseleave", id, () => { m.getCanvas().style.cursor = ""; });
    }
    return () => { abort.current?.abort(); m.remove(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    filtersRef.current = filters;
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters]);

  useEffect(() => {
    selectedRef.current = selected;
    const m = map.current;
    if (!m || !ready.current) return;
    m.setFilter("selected", ["==", ["get", "id"], selected?.note_id ?? -1]);
  }, [selected]);

  useEffect(() => {
    const m = map.current;
    if (!m || !flyTo) return;
    if (flyTo.bbox) m.fitBounds(flyTo.bbox, { padding: 40, maxZoom: 15, duration: 900 });
    else m.flyTo({ center: [flyTo.lon, flyTo.lat], zoom: Math.max(m.getZoom(), flyTo.zoom || 15) });
  }, [flyTo]);

  return <div ref={box} className="map" aria-label="Map" />;
}
