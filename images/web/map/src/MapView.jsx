import { useEffect, useRef } from "react";
import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { api, COLORS } from "./api.js";

const STYLE = "https://tiles.openfreemap.org/styles/positron";
const EMPTY = { type: "FeatureCollection", features: [] };
const colorExpr = ["match", ["get", "c"], ...Object.entries(COLORS).flat(), "#555"];

const short = (n) => (n >= 1000 ? `${Math.round(n / 1000)}k` : String(n));
const ORDER = Object.keys(COLORS);

/** SVG donut: one slice per category, in the legend order. */
function pieElement(cats, n) {
  const size = n < 10 ? 26 : n < 100 ? 34 : n < 1000 ? 42 : n < 10000 ? 50 : 58;
  const r = size / 2, r0 = r - Math.max(5, size * 0.17), c = r;
  const pt = (a, rad) => `${(c + rad * Math.sin(a)).toFixed(2)} ${(c - rad * Math.cos(a)).toFixed(2)}`;
  const rows = Object.entries(cats).filter(([, v]) => v > 0)
    .sort((a, b) => (ORDER.indexOf(a[0]) + 99) % 99 - (ORDER.indexOf(b[0]) + 99) % 99);
  const total = rows.reduce((s, [, v]) => s + v, 0) || 1;
  let a0 = 0, slices = "";
  for (const [cat, v] of rows) {
    const color = COLORS[cat] || "#888";
    if (v === total) {
      slices += `<circle cx="${c}" cy="${c}" r="${(r + r0) / 2}" fill="none" stroke="${color}" stroke-width="${r - r0}"/>`;
      break;
    }
    const a1 = a0 + (v / total) * Math.PI * 2;
    const big = a1 - a0 > Math.PI ? 1 : 0;
    slices += `<path fill="${color}" d="M${pt(a0, r)}A${r} ${r} 0 ${big} 1 ${pt(a1, r)}L${pt(a1, r0)}A${r0} ${r0} 0 ${big} 0 ${pt(a0, r0)}Z"/>`;
    a0 = a1;
  }
  const el = document.createElement("button");
  el.className = "pie";
  el.type = "button";
  el.title = rows.map(([k, v]) => `${k}: ${v.toLocaleString()}`).join("\n");
  el.style.width = el.style.height = `${size}px`;
  el.innerHTML = `<svg viewBox="0 0 ${size} ${size}" width="${size}" height="${size}" aria-hidden="true">${slices}`
    + `<circle cx="${c}" cy="${c}" r="${r0}" fill="#fff"/></svg><span>${short(n)}</span>`;
  return el;
}

/** The map. Loads points (or grid cells when zoomed out) for the current view. */
export default function MapView({ filters, selected, onSelect, onView, flyTo, start, t, onMode, onLoading }) {
  const box = useRef(null);
  const map = useRef(null);
  const ready = useRef(false);
  const filtersRef = useRef(filters);
  const selectedRef = useRef(selected);
  const abort = useRef(null);
  const pies = useRef(new Map());   // key -> maplibregl.Marker

  // Zoomed out: one pie per grid cell, split by category, with the count inside.
  const drawPies = (m, fc) => {
    const next = new Map();
    if (fc.mode === "grid") {
      for (const f of fc.features) {
        const { n, c, cats } = f.properties;
        const at = f.geometry.coordinates;
        const key = `${at[0]},${at[1]},${n}`;
        let mk = pies.current.get(key);
        if (!mk) {
          const el = pieElement(cats || { [c]: n }, n);
          el.addEventListener("click", () => m.easeTo({ center: at, zoom: Math.min(m.getZoom() + 2.5, 16) }));
          mk = new maplibregl.Marker({ element: el }).setLngLat(at).addTo(m);
        }
        next.set(key, mk);
      }
    }
    for (const [key, mk] of pies.current) if (!next.has(key)) mk.remove();
    pies.current = next;
  };
  const handlers = useRef({ onSelect, onView, onMode, onLoading });
  handlers.current = { onSelect, onView, onMode, onLoading };

  const load = () => {
    const m = map.current;
    if (!m || !ready.current) return;
    const b = m.getBounds();
    const bbox = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()];
    abort.current?.abort();
    const ctl = new AbortController();
    abort.current = ctl;
    handlers.current.onLoading?.(true);
    api.points(filtersRef.current, bbox, m.getZoom(), ctl.signal)
      .then((fc) => {
        m.getSource("notes")?.setData(fc.mode === "grid" ? EMPTY : fc);
        drawPies(m, fc);
        handlers.current.onMode(fc.mode);
      })
      .catch((e) => { if (e.name !== "AbortError") console.warn("points", e); })
      .finally(() => { if (abort.current === ctl) handlers.current.onLoading?.(false); });
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
    m.on("mouseenter", "points", () => { m.getCanvas().style.cursor = "pointer"; });
    m.on("mouseleave", "points", () => { m.getCanvas().style.cursor = ""; });
    return () => { abort.current?.abort(); pies.current.clear(); m.remove(); };
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
