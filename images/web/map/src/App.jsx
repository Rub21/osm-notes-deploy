import { useEffect, useMemo, useRef, useState } from "react";
import { api, COLORS, PAGE_SIZE, readUrl, writeUrl } from "./api.js";
import { startLang, TEXT } from "./i18n.js";
import Filters from "./Filters.jsx";
import Header from "./Header.jsx";
import Editor, { editorView } from "./Editor.jsx";
import MapView from "./MapView.jsx";
import NoteDetail from "./NoteDetail.jsx";
import PlaceSearch from "./PlaceSearch.jsx";
import Sidebar from "./Sidebar.jsx";

const initial = readUrl();

export default function App() {
  const [lang, setLangState] = useState(startLang);
  const t = TEXT[lang];
  const [me, setMe] = useState(null);
  const [options, setOptions] = useState(null);
  const [filters, setFilters] = useState(initial.filters);
  const [sort, setSort] = useState(initial.sort);
  const [view, setView] = useState(null);
  const [mode, setMode] = useState("points");
  const [list, setList] = useState(null);
  const [loading, setLoading] = useState(false);
  const [mapLoading, setMapLoading] = useState(false);
  const [page, setPage] = useState(0);
  const [selectedId, setSelectedId] = useState(initial.note);
  const [selected, setSelected] = useState(null);
  const [flyTo, setFlyTo] = useState(null);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [reload, setReload] = useState(0);   // bump to load the map points again
  const [editing, setEditing] = useState(false);   // iD over the map, note panel on the side
  const [changeset, setChangeset] = useState(null);   // last changeset saved in iD
  const abort = useRef(null);
  const lastIdx = useRef(-1);     // place of the open note in the list (it may leave the list)
  const pendingPick = useRef(null);   // "first" | "last": pick after the next page loads

  const setLang = (l) => {
    setLangState(l);
    try { localStorage.setItem("viewer_lang", l); } catch { /* private mode */ }
  };

  useEffect(() => {
    api.me().then(setMe).catch(() => setMe({ osm_login: false }));
    api.options().then(setOptions).catch((e) => console.warn("options", e));
  }, []);
  useEffect(() => { document.documentElement.lang = lang; document.title = t.title; }, [lang, t]);

  // Category and theme names in the page language.
  const names = useMemo(() => {
    const key = lang === "es" ? "name_es" : "name_en";
    const cat = {}, theme = {}, desc = {};
    for (const c of options?.all_categories || []) {
      cat[c.code] = c[key];
      desc[c.code] = (lang === "es" ? c.description : c.description_en) || c.description;
    }
    for (const th of options?.themes || []) theme[th.code] = th[key];
    return { cat, theme, desc };
  }, [options, lang]);

  // Sidebar list: one page of notes for the view and filters.
  const loadList = (p) => {
    if (!view) return;
    abort.current?.abort();
    abort.current = new AbortController();
    setLoading(true);
    api.list(filters, view.bbox, sort, p * PAGE_SIZE, abort.current.signal)
      .then((res) => { setList(res); setLoading(false); })
      .catch((e) => { if (e.name !== "AbortError") { console.warn("list", e); setLoading(false); } });
  };
  useEffect(() => { setPage(0); loadList(0); }, [view, filters, sort]); // eslint-disable-line react-hooks/exhaustive-deps
  const goPage = (p) => { setPage(p); loadList(p); document.querySelector(".side")?.scrollTo(0, 0); };

  useEffect(() => { writeUrl(filters, sort, selectedId, view); }, [filters, sort, selectedId, view]);

  // Previous / next note in the sidebar list (same filters and order), across pages.
  const items = list?.items || [];
  const idx = items.findIndex((r) => r.note_id === selectedId);
  if (idx >= 0) lastIdx.current = idx;
  const pages = Math.ceil((list?.total || 0) / PAGE_SIZE);
  const stepTo = (dir) => {
    // A closed or fixed note drops out of the list: the one now in its place is "next".
    // A note opened from the map that is not on this page: "next" = first of the list.
    const at = idx >= 0 ? idx + dir
      : lastIdx.current < 0 ? (dir > 0 ? 0 : -1)
      : (dir > 0 ? lastIdx.current : lastIdx.current - 1);
    if (at >= 0 && at < items.length) { select(items[at].note_id, items[at]); return; }
    const p = page + (dir > 0 ? 1 : -1);
    if (p < 0 || p >= pages) return;
    pendingPick.current = dir > 0 ? "first" : "last";
    goPage(p);
  };
  useEffect(() => {
    if (!pendingPick.current || !items.length) return;
    const row = pendingPick.current === "first" ? items[0] : items[items.length - 1];
    pendingPick.current = null;
    select(row.note_id, row);
  }, [list]); // eslint-disable-line react-hooks/exhaustive-deps
  const pos = idx >= 0 ? page * PAGE_SIZE + idx + 1 : null;
  const nav = list ? {
    pos, total: list.total,
    hasPrev: idx > 0 || page > 0 || (idx < 0 && lastIdx.current > 0),
    hasNext: (idx >= 0 ? idx + 1 : Math.max(lastIdx.current, 0)) < items.length || page + 1 < pages,
    prev: () => stepTo(-1), next: () => stepTo(1),
  } : null;

  const select = (id, row) => {
    setChangeset(null);   // a changeset belongs to the note it was made for
    lastIdx.current = (list?.items || []).findIndex((r) => r.note_id === id);   // -1: not in the list
    setSelectedId(id);
    setSelected(row ? { ...row } : { note_id: id });
  };
  const onLoaded = (note) => {
    setSelected(note);
    // Opened from a shared link: center the map on the note.
    if (!view || !inView(note, view.bbox)) setFlyTo({ lon: note.lon, lat: note.lat, zoom: 15 });
  };

  // After 👍/👎 the note may change category: load the map and the list again.
  const onSaved = () => { setReload((r) => r + 1); loadList(page); };

  const startEdit = () => { setChangeset(null); setEditing(true); };
  const stopEdit = () => {
    const v = editorView();   // keep the place where you were in iD
    if (v) setFlyTo({ ...v, exact: true });
    setEditing(false);
    // The note may have been closed inside iD (its notes pane): record it like our
    // button does. The server checks OSM first; still open -> 409, nothing saved.
    if (selectedId) {
      api.close({ note_id: selectedId, reason: "other" })
        .then((r) => { if (r.closed) onSaved(); })
        .catch(() => { /* still open, or not logged in */ });
    }
  };
  const closeDetail = () => { setEditing(false); setSelectedId(null); setSelected(null); };

  const detail = selectedId ? (
    <NoteDetail key={selectedId} id={selectedId} t={t} names={names} me={me}
                options={options || { categories: [], themes: [] }}
                onClose={editing ? stopEdit : closeDetail} onLoaded={onLoaded}
                onSaved={onSaved} editing={editing} onEdit={startEdit} changeset={changeset} nav={nav}
                onZoom={(n) => setFlyTo({ lon: n.lon, lat: n.lat, zoom: 17 })} />
  ) : null;

  return (
    <div className="shell">
    <Header t={t} lang={lang} setLang={setLang} me={me} options={options} />
    <div className={`app${editing ? " editing" : ""}`}>
      {editing ? detail : <Sidebar t={t} lang={lang} names={names}
               filters={filters} sort={sort} setSort={setSort}
               open={filtersOpen} onFilters={() => setFiltersOpen(!filtersOpen)}
               list={list} loading={loading} page={page} setPage={goPage}
               onRefresh={() => loadList(page)} selectedId={selectedId} onSelect={select}
               mode={mode} />}
      <main className="stage">
        <PlaceSearch t={t} lang={lang} onFly={setFlyTo} />
        <MapView filters={filters} reload={reload} selected={selected} onSelect={(id) => select(id)} onView={setView}
                 flyTo={flyTo} start={initial.view} t={t} onMode={setMode} onLoading={setMapLoading} />
        {mapLoading ? <div className="map-loading" role="status"><span className="spin" />{t.loading}</div> : null}
        {editing ? null : detail}
        {editing && selected?.lat != null ? (
          <Editor me={me} lang={lang} note={selected} t={t} onChangeset={setChangeset} />
        ) : null}
        <Legend t={t} names={names} />
        {filtersOpen ? (
          <Filters t={t} me={me} options={options} names={names} filters={filters} setFilters={setFilters}
                   list={list} bbox={view?.bbox} onClose={() => setFiltersOpen(false)} />
        ) : null}
      </main>
    </div>
    </div>
  );
}

/** Small key of the point colors, bottom left of the map. */
function Legend({ t, names }) {
  return (
    <details className="legend" open>
      <summary>{t.legend}</summary>
      <ul>
        {Object.entries(COLORS).map(([c, color]) => (
          <li key={c}><i style={{ background: color }} />{names.cat[c] || c}</li>
        ))}
      </ul>
    </details>
  );
}

function inView(n, bbox) {
  if (!bbox) return false;
  const [w, s, e, nn] = bbox;
  return n.lon >= w && n.lon <= e && n.lat >= s && n.lat <= nn;
}
