import { useState } from "react";
import { api, colorOf, searchPlace } from "./api.js";

const fmt = (n) => (n == null ? "" : n.toLocaleString());
const CONF = [0, 0.5, 0.7, 0.9];
const AGES = [["anyAge", 0], ["days30", 30], ["year", 365], ["years3", 1095]];

/** Left panel: place search, filters, the list of notes and export. */
export default function Sidebar(props) {
  const { t, lang, setLang, me, options, names, filters, setFilters, sort, setSort,
          list, loading, onMore, selectedId, onSelect, onFly, bbox, mode } = props;
  const [place, setPlace] = useState("");
  const [places, setPlaces] = useState(null);
  const [text, setText] = useState(filters.q);
  const [copied, setCopied] = useState(false);

  const set = (patch) => setFilters({ ...filters, ...patch });
  const toggleCat = (code) => set({
    cat: filters.cat.includes(code) ? filters.cat.filter((c) => c !== code) : [...filters.cat, code],
  });
  const groups = [["map", t.groupMap], ["close", t.groupClose]].map(([grp, title]) => ({
    grp, title, cats: (options?.categories || []).filter((c) => (grp === "map") === (c.grp === "map")),
  }));
  const toggleGroup = (cats) => {
    const codes = cats.map((c) => c.code);
    const all = codes.every((c) => filters.cat.includes(c));
    set({ cat: all ? filters.cat.filter((c) => !codes.includes(c))
                   : [...new Set([...filters.cat, ...codes])] });
  };

  const findPlace = async (e) => {
    e.preventDefault();
    if (!place.trim()) return;
    setPlaces([]);
    try {
      const res = await searchPlace(place.trim(), lang);
      setPlaces(res);
      if (res.length === 1) go(res[0]);
    } catch { setPlaces([]); }
  };
  const go = (r) => {
    const [s, n, w, e] = r.boundingbox.map(Number);
    onFly({ bbox: [[w, s], [e, n]] });
    setPlaces(null);
    setPlace(r.display_name.split(",")[0]);
  };
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(api.exportUrl(filters, bbox, false));
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch { /* clipboard blocked */ }
  };
  const active = filters.cat.length || filters.theme || filters.lang || filters.app ||
                 filters.conf || filters.days || filters.q;

  return (
    <aside className="side">
      <header className="brand">
        <div>
          <small>{t.tagline}</small>
          <h1>{t.title}</h1>
          {options ? (
            <p className="muted">
              {fmt(options.total)} {t.notes} · {options.models?.[0]} · {t.updated}{" "}
              {options.updated ? new Date(options.updated).toLocaleDateString() : "—"}
            </p>
          ) : null}
        </div>
        <div className="who">
          <button className="ghost" onClick={() => setLang(lang === "en" ? "es" : "en")}>
            {lang === "en" ? "ES" : "EN"}
          </button>
          {me?.osm_login ? (me.user ? (
            <a className="ghost" href={`/auth/logout?next=${encodeURIComponent(location.pathname + location.search)}`} title={t.logout}>
              {me.user.name} ⏻
            </a>
          ) : (
            <a className="ghost" href={`/auth/login?next=${encodeURIComponent(location.pathname + location.search + location.hash)}`}>{t.login}</a>
          )) : null}
        </div>
      </header>
      <p className="about">{t.about}</p>
      <p className="help"><a href="/label">{t.help} →</a> <span>{t.helpHint}</span></p>

      <form className="search" onSubmit={findPlace}>
        <label htmlFor="place">{t.place}</label>
        <input id="place" value={place} onChange={(e) => setPlace(e.target.value)} placeholder={t.placeHint} />
        {places ? (
          <ul className="places">
            {places.length === 0 ? <li className="muted">{t.noPlace}</li> : places.map((r) => (
              <li key={r.place_id}><button type="button" onClick={() => go(r)}>{r.display_name}</button></li>
            ))}
          </ul>
        ) : null}
      </form>
      <form className="search" onSubmit={(e) => { e.preventDefault(); set({ q: text.trim() }); }}>
        <label htmlFor="q">{t.text}</label>
        <input id="q" value={text} onChange={(e) => setText(e.target.value)} placeholder={t.textHint} />
      </form>

      <section className="filters">
        {groups.map(({ grp, title, cats }) => (
          <div key={grp} className="group">
            <button className="group-title" onClick={() => toggleGroup(cats)}>{title}</button>
            <div className="chips">
              {cats.map((c) => {
                const on = filters.cat.includes(c.code);
                return (
                  <button key={c.code} className={`chip${on ? " on" : ""}`} style={{ "--cat": colorOf(c.code) }}
                          onClick={() => toggleCat(c.code)} title={c.description} aria-pressed={on}>
                    <span className="dot" />{names.cat[c.code]}
                    <span className="count">{fmt(list?.by_category?.[c.code] || 0)}</span>
                  </button>
                );
              })}
            </div>
          </div>
        ))}

        <div className="selects">
          <label>{t.theme}
            <select value={filters.theme} onChange={(e) => set({ theme: e.target.value })}>
              <option value="">{t.all}</option>
              {options?.themes.map((th) => <option key={th.code} value={th.code}>{names.theme[th.code]}</option>)}
            </select>
          </label>
          <label>{t.lang}
            <select value={filters.lang} onChange={(e) => set({ lang: e.target.value })}>
              <option value="">{t.all}</option>
              {options?.langs.map((l) => <option key={l.lang} value={l.lang}>{l.lang} ({fmt(l.n)})</option>)}
            </select>
          </label>
          <label>{t.app}
            <select value={filters.app} onChange={(e) => set({ app: e.target.value })}>
              <option value="">{t.all}</option>
              {options?.apps.map((a) => (
                <option key={a.app} value={a.app}>{a.app === "none" ? t.noApp : a.app} ({fmt(a.n)})</option>
              ))}
            </select>
          </label>
          <label>{t.age}
            <select value={filters.days} onChange={(e) => set({ days: Number(e.target.value) })}>
              {AGES.map(([k, d]) => <option key={d} value={d}>{t[k]}</option>)}
            </select>
          </label>
        </div>
        <div className="conf" role="group" aria-label={t.conf}>
          <span>{t.conf}</span>
          {CONF.map((c) => (
            <button key={c} className={filters.conf === c ? "on" : ""} onClick={() => set({ conf: c })}>
              {c ? `≥ ${c}` : t.all}
            </button>
          ))}
        </div>
        {active ? (
          <button className="link" onClick={() => { setText(""); setFilters({ cat: [], theme: "", lang: "", app: "", conf: 0, days: 0, q: "" }); }}>
            {t.clear}
          </button>
        ) : null}
      </section>

      <div className="list-head">
        <strong>{list ? fmt(list.total) : "…"}</strong> <span>{t.inView}</span>
        <select value={sort} onChange={(e) => setSort(e.target.value)} aria-label={t.sort}>
          <option value="conf">{t.sortConf}</option>
          <option value="low">{t.sortLow}</option>
          <option value="new">{t.sortNew}</option>
          <option value="old">{t.sortOld}</option>
        </select>
      </div>
      {mode === "grid" ? <p className="hint">{t.zoomIn}</p> : null}

      <ol className="list">
        {list?.items.map((n) => (
          <li key={n.note_id}>
            <button className={`item${n.note_id === selectedId ? " sel" : ""}`} style={{ "--cat": colorOf(n.category) }}
                    onClick={() => onSelect(n.note_id, n)}>
              <span className="item-top">
                <span className="dot" />
                <strong>{names.cat[n.category] || n.category}</strong>
                <span className="pill">{Math.round(n.category_conf * 100)}%</span>
                <span className="theme">{names.theme[n.theme] || ""}</span>
              </span>
              <span className="item-text">{n.text || "—"}</span>
              <span className="item-meta">
                #{n.note_id} · {n.lang || "?"}{n.app ? ` · ${n.app}` : ""} ·{" "}
                {n.created_at ? new Date(n.created_at).toLocaleDateString() : ""}
              </span>
            </button>
          </li>
        ))}
      </ol>
      {list && !list.items.length && !loading ? <p className="muted pad">{t.empty}</p> : null}
      {loading ? <p className="muted pad">{t.loading}</p> : null}
      {list && list.items.length < list.total && !loading ? (
        <button className="more" onClick={onMore}>{t.more}</button>
      ) : null}

      <footer className="export">
        <strong>{t.export}</strong>
        <a href={api.exportUrl(filters, bbox, true)}>{t.download}</a>
        <button onClick={copy}>{copied ? t.copied : t.copyUrl}</button>
        <p className="muted">{t.exportHint}</p>
      </footer>
    </aside>
  );
}
