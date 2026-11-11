import { useState } from "react";
import { api, colorOf, EMPTY_FILTERS, PAGE_SIZE } from "./api.js";

const fmt = (n) => (n == null ? "" : n.toLocaleString());
const CONF = [0, 0.5, 0.7, 0.9];
const AGES = [["anyAge", 0], ["days30", 30], ["year", 365], ["years3", 1095]];

/** "3 days ago" in the page language. */
function ago(date, lang) {
  if (!date) return "";
  const rtf = new Intl.RelativeTimeFormat(lang, { numeric: "auto" });
  const s = (new Date(date) - Date.now()) / 1000;
  for (const [unit, n] of [["year", 31536000], ["month", 2592000], ["day", 86400], ["hour", 3600], ["minute", 60]]) {
    if (Math.abs(s) >= n) return rtf.format(Math.round(s / n), unit);
  }
  return rtf.format(0, "minute");
}

/** Left panel, like OSMCha: order + filters on top, the list of notes, pages. */
export default function Sidebar(props) {
  const { t, lang, setLang, me, options, names, filters, setFilters, sort, setSort,
          list, loading, page, setPage, onRefresh, selectedId, onSelect, bbox, mode } = props;
  const [open, setOpen] = useState(false);
  const active = filters.cat.length + ["theme", "lang", "app", "conf", "days", "q"]
    .filter((k) => filters[k]).length;
  const pages = list ? Math.max(1, Math.ceil(list.total / PAGE_SIZE)) : 1;

  return (
    <aside className="side">
      <header className="brand">
        <div>
          <h1>{t.title}</h1>
          <small>{t.tagline}{options ? ` · ${fmt(options.total)} ${t.notes}` : ""}</small>
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

      <div className="toolbar">
        <select value={sort} onChange={(e) => setSort(e.target.value)} aria-label={t.sort}>
          <option value="new">{t.sortNew}</option>
          <option value="old">{t.sortOld}</option>
          <option value="conf">{t.sortConf}</option>
          <option value="low">{t.sortLow}</option>
        </select>
        <button className={`ghost filters-btn${open ? " on" : ""}`} onClick={() => setOpen(!open)} aria-expanded={open}>
          {t.filters}{active ? <span className="badge">{active}</span> : null}
        </button>
      </div>
      <div className="count">
        <span><strong>{list ? fmt(list.total) : "…"}</strong> {t.inView}</span>
        <button className="ghost icon" onClick={onRefresh} title={t.refresh} aria-label={t.refresh}>⟳</button>
      </div>

      {open ? (
        <Filters {...{ t, options, names, filters, setFilters, list, bbox }} onClose={() => setOpen(false)} />
      ) : (
        <>
          {mode === "grid" ? <p className="hint">{t.zoomIn}</p> : null}
          <ol className="list">
            {list?.items.map((n) => (
              <li key={n.note_id}>
                <button className={`item${n.note_id === selectedId ? " sel" : ""}`} style={{ "--cat": colorOf(n.category) }}
                        onClick={() => onSelect(n.note_id, n)}>
                  <span className="item-top">
                    <span className="dot" />
                    <strong>{names.cat[n.category] || n.category}</strong>
                    <span className="when">{ago(n.created_at, lang)}</span>
                  </span>
                  <span className="item-text">{n.text || "—"}</span>
                  <span className="tags">
                    {n.theme && n.theme !== "none" ? <span className="tag">{names.theme[n.theme]}</span> : null}
                    {n.app && n.app !== "other" ? <span className="tag">{n.app}</span> : null}
                    {n.lang ? <span className="tag">{n.lang}</span> : null}
                  </span>
                  <span className="item-foot">
                    <span>#{n.note_id}</span>
                    <span className="conf-badge" title={t.conf}>{Math.round(n.category_conf * 100)}%</span>
                  </span>
                </button>
              </li>
            ))}
          </ol>
          {list && !list.items.length && !loading ? <p className="muted pad">{t.empty}</p> : null}
          {loading && !list ? <p className="muted pad">{t.loading}</p> : null}
          <p className="help"><a href="/label">{t.help} →</a> <span>{t.helpHint}</span></p>
          {pages > 1 ? <Pager page={page} pages={pages} setPage={setPage} /> : null}
        </>
      )}
    </aside>
  );
}

function Pager({ page, pages, setPage }) {
  const from = Math.max(0, Math.min(page - 2, pages - 5));
  const nums = Array.from({ length: Math.min(5, pages) }, (_, i) => from + i);
  return (
    <nav className="pager">
      <button disabled={page === 0} onClick={() => setPage(page - 1)} aria-label="previous">←</button>
      {nums.map((p) => (
        <button key={p} className={p === page ? "on" : ""} onClick={() => setPage(p)}>{p + 1}</button>
      ))}
      <button disabled={page >= pages - 1} onClick={() => setPage(page + 1)} aria-label="next">→</button>
    </nav>
  );
}

function Filters({ t, options, names, filters, setFilters, list, bbox, onClose }) {
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
    set({ cat: all ? filters.cat.filter((c) => !codes.includes(c)) : [...new Set([...filters.cat, ...codes])] });
  };
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(api.exportUrl(filters, bbox, false));
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch { /* clipboard blocked */ }
  };

  return (
    <section className="filters">
      <form className="search" onSubmit={(e) => { e.preventDefault(); set({ q: text.trim() }); }}>
        <label htmlFor="q">{t.text}</label>
        <input id="q" value={text} onChange={(e) => setText(e.target.value)} placeholder={t.textHint} />
      </form>

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
                  <span className="n">{fmt(list?.by_category?.[c.code] || 0)}</span>
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
              <option key={a.app} value={a.app}>{a.app === "none" || a.app === "other" ? t.noApp : a.app} ({fmt(a.n)})</option>
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

      <div className="filters-foot">
        <button className="link" onClick={() => { setText(""); setFilters(EMPTY_FILTERS); }}>{t.clear}</button>
        <button className="primary" onClick={onClose}>{t.showList}</button>
      </div>

      <div className="export">
        <strong>{t.export}</strong>
        <a href={api.exportUrl(filters, bbox, true)}>{t.download}</a>
        <button onClick={copy}>{copied ? t.copied : t.copyUrl}</button>
        <p className="muted">{t.exportHint}</p>
      </div>
      <p className="about">{t.about}</p>
    </section>
  );
}
