import { useEffect, useMemo, useRef, useState } from "react";
import { api, COLORS, PAGE_SIZE, readUrl, writeUrl } from "./api.js";
import { startLang, TEXT } from "./i18n.js";
import Filters from "./Filters.jsx";
import Header from "./Header.jsx";
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
  const abort = useRef(null);

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

  const select = (id, row) => {
    setSelectedId(id);
    setSelected(row ? { ...row } : { note_id: id });
  };
  const onLoaded = (note) => {
    setSelected(note);
    // Opened from a shared link: center the map on the note.
    if (!view || !inView(note, view.bbox)) setFlyTo({ lon: note.lon, lat: note.lat, zoom: 15 });
  };

  return (
    <div className="shell">
    <Header t={t} lang={lang} setLang={setLang} me={me} options={options} />
    <div className="app">
      <Sidebar t={t} lang={lang} names={names}
               filters={filters} sort={sort} setSort={setSort}
               open={filtersOpen} onFilters={() => setFiltersOpen(!filtersOpen)}
               list={list} loading={loading} page={page} setPage={goPage}
               onRefresh={() => loadList(page)} selectedId={selectedId} onSelect={select}
               mode={mode} />
      <main className="stage">
        <PlaceSearch t={t} lang={lang} onFly={setFlyTo} />
        <MapView filters={filters} selected={selected} onSelect={(id) => select(id)} onView={setView}
                 flyTo={flyTo} start={initial.view} t={t} onMode={setMode} onLoading={setMapLoading} />
        {mapLoading ? <div className="map-loading" role="status"><span className="spin" />{t.loading}</div> : null}
        {selectedId ? (
          <NoteDetail key={selectedId} id={selectedId} t={t} names={names} me={me}
                      options={options || { categories: [], themes: [] }}
                      onClose={() => { setSelectedId(null); setSelected(null); }} onLoaded={onLoaded} />
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
