// API calls and the filters <-> URL mapping.

export const COLORS = {
  missing_place: "#2f7d4a",
  place_changed: "#d9622b",
  wrong_name: "#8e44ad",
  wrong_road: "#2b6cb0",
  other_data: "#0f8b8d",
  not_mappable: "#8a8f98",
  empty: "#c3c6cc",
  spam: "#c0392b",
};
export const colorOf = (c) => COLORS[c] || "#555";

export const PAGE_SIZE = 50;
export const EMPTY_FILTERS = { cat: [], theme: "", lang: "", app: "", conf: 0, days: 0, q: "" };

/** Filters from the page URL (?cat=a,b&theme=food&conf=0.9...). */
export function readUrl() {
  const p = new URLSearchParams(location.search);
  const filters = {
    cat: (p.get("cat") || "").split(",").filter(Boolean),
    theme: p.get("theme") || "",
    lang: p.get("lang") || "",
    app: p.get("app") || "",
    conf: Number(p.get("conf")) || 0,
    days: Number(p.get("days")) || 0,
    q: p.get("q") || "",
  };
  const m = /map=([\d.]+)\/(-?[\d.]+)\/(-?[\d.]+)/.exec(location.hash);
  const view = m ? { zoom: +m[1], lat: +m[2], lon: +m[3] } : null;
  return { filters, sort: p.get("sort") || "new", note: Number(p.get("note")) || null, view };
}

export function filterParams(filters, bbox) {
  const p = new URLSearchParams();
  if (filters.cat.length) p.set("cat", filters.cat.join(","));
  for (const k of ["theme", "lang", "app", "q"]) if (filters[k]) p.set(k, filters[k]);
  if (filters.conf) p.set("conf", String(filters.conf));
  if (filters.days) p.set("days", String(filters.days));
  if (bbox) p.set("bbox", bbox.map((x) => x.toFixed(5)).join(","));
  return p;
}

/** Keep the URL shareable: filters in the query, map position in the hash. */
export function writeUrl(filters, sort, note, view) {
  const p = filterParams(filters);
  if (sort && sort !== "new") p.set("sort", sort);
  if (note) p.set("note", String(note));
  const q = p.toString();
  const hash = view ? `#map=${view.zoom.toFixed(2)}/${view.lat.toFixed(5)}/${view.lon.toFixed(5)}` : "";
  history.replaceState(null, "", `${location.pathname}${q ? "?" + q : ""}${hash}`);
}

async function getJson(url, opts) {
  const r = await fetch(url, { credentials: "same-origin", ...opts });
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch { /* not json */ }
    const err = new Error(msg);
    err.status = r.status;
    throw err;
  }
  return r.json();
}

export const api = {
  me: () => getJson("/api/me"),
  options: () => getJson("/api/map/options"),
  points: (filters, bbox, zoom, signal) => {
    const p = filterParams(filters, bbox);
    p.set("zoom", zoom.toFixed(1));
    return getJson(`/api/map/points?${p}`, { signal });
  },
  list: (filters, bbox, sort, offset, signal) => {
    const p = filterParams(filters, bbox);
    p.set("sort", sort);
    p.set("offset", String(offset));
    p.set("limit", String(PAGE_SIZE));
    return getJson(`/api/map/list?${p}`, { signal });
  },
  note: (id) => getJson(`/api/map/note/${id}`),
  feedback: (body) => getJson("/api/map/feedback", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  }),
  exportUrl: (filters, bbox, download) => {
    const p = filterParams(filters, bbox);
    if (download) p.set("download", "1");
    return `${location.origin}/api/map/export.geojson?${p}`;
  },
};

// Nominatim asks for at most 1 request per second and no search-as-you-type.
let lastSearch = 0;
export async function searchPlace(q, lang) {
  const wait = 1000 - (Date.now() - lastSearch);
  if (wait > 0) await new Promise((r) => setTimeout(r, wait));
  lastSearch = Date.now();
  const p = new URLSearchParams({ q, format: "jsonv2", limit: "5", "accept-language": lang });
  const r = await fetch(`https://nominatim.openstreetmap.org/search?${p}`);
  if (!r.ok) throw new Error("Nominatim " + r.status);
  return r.json();
}
