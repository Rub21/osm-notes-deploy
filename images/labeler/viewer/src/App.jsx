import { useEffect, useMemo, useRef, useState } from "react";
import { api, readUrl, writeUrl } from "./api.js";
import { startLang, TEXT } from "./i18n.js";
import MapView from "./MapView.jsx";
import NoteDetail from "./NoteDetail.jsx";
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
  const [selectedId, setSelectedId] = useState(initial.note);
  const [selected, setSelected] = useState(null);
  const [flyTo, setFlyTo] = useState(null);
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
    const cat = {}, theme = {};
    for (const c of options?.all_categories || []) cat[c.code] = c[key];
    for (const th of options?.themes || []) theme[th.code] = th[key];
    return { cat, theme };
  }, [options, lang]);

  // Sidebar list for the view and filters. Starts again at offset 0 on any change.
  const loadList = (offset) => {
    if (!view) return;
    abort.current?.abort();
    abort.current = new AbortController();
    setLoading(true);
    api.list(filters, view.bbox, sort, offset, abort.current.signal)
      .then((res) => {
        setList((prev) => (offset && prev ? { ...res, items: [...prev.items, ...res.items] } : res));
        setLoading(false);
      })
      .catch((e) => { if (e.name !== "AbortError") { console.warn("list", e); setLoading(false); } });
  };
  useEffect(() => { loadList(0); }, [view, filters, sort]); // eslint-disable-line react-hooks/exhaustive-deps

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
    <div className="app">
      <Sidebar t={t} lang={lang} setLang={setLang} me={me} options={options} names={names}
               filters={filters} setFilters={setFilters} sort={sort} setSort={setSort}
               list={list} loading={loading} onMore={() => loadList(list?.items.length || 0)}
               selectedId={selectedId} onSelect={select} onFly={setFlyTo}
               bbox={view?.bbox} mode={mode} />
      <main className="stage">
        <MapView filters={filters} selected={selected} onSelect={(id) => select(id)} onView={setView}
                 flyTo={flyTo} start={initial.view} t={t} onMode={setMode} />
        {selectedId ? (
          <NoteDetail key={selectedId} id={selectedId} t={t} names={names} me={me}
                      options={options || { categories: [], themes: [] }}
                      onClose={() => { setSelectedId(null); setSelected(null); }} onLoaded={onLoaded} />
        ) : null}
      </main>
    </div>
  );
}

function inView(n, bbox) {
  if (!bbox) return false;
  const [w, s, e, nn] = bbox;
  return n.lon >= w && n.lon <= e && n.lat >= s && n.lat <= nn;
}
