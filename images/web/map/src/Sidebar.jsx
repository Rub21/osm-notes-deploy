import { colorOf, COLORS, PAGE_SIZE } from "./api.js";
import { ago } from "./i18n.js";

const fmt = (n) => (n == null ? "" : n.toLocaleString());

/** Left panel, like OSMCha: order + filters on top, the list of notes, pages. */
export default function Sidebar(props) {
  const { t, lang, names, filters, sort, setSort, open, onFilters,
          list, loading, page, setPage, onRefresh, selectedId, onSelect, mode } = props;
  const active = filters.cat.length + ["theme", "lang", "app", "conf", "days", "q"]
    .filter((k) => filters[k]).length;
  const pages = list ? Math.max(1, Math.ceil(list.total / PAGE_SIZE)) : 1;

  return (
    <aside className="side">
      <div className="toolbar">
        <select value={sort} onChange={(e) => setSort(e.target.value)} aria-label={t.sort}>
          <option value="new">{t.sortNew}</option>
          <option value="old">{t.sortOld}</option>
          <option value="conf">{t.sortConf}</option>
          <option value="low">{t.sortLow}</option>
        </select>
        <button className={`ghost filters-btn${open ? " on" : ""}`} onClick={onFilters} aria-expanded={open}>
          {t.filters}{active ? <span className="badge">{active}</span> : null}
        </button>
      </div>
      <div className="count">
        <span><strong>{list ? fmt(list.total) : "…"}</strong> {t.inView}</span>
        <button className="ghost icon" onClick={onRefresh} title={t.refresh} aria-label={t.refresh}>⟳</button>
      </div>
      <Mix t={t} names={names} byCat={list?.by_category} />
      <div className={`progress${loading ? " on" : ""}`} aria-hidden="true" />

      <>
          {list?.restricted ? <p className="restricted">🔒 {t.restricted}</p> : null}
          {mode === "grid" ? <p className="hint">{t.zoomIn}</p> : null}
          {loading && !list ? (
            <div className="loader" role="status"><span className="spin big" /><span>{t.loading}</span></div>
          ) : null}
          <ol className={`list${loading ? " stale" : ""}`} aria-busy={loading}>
            {list?.items.map((n) => (
              <li key={n.note_id}>
                <button className={`item${n.note_id === selectedId ? " sel" : ""}`} style={{ "--cat": colorOf(n.category) }}
                        onClick={() => onSelect(n.note_id, n)}>
                  <span className="item-top">
                    <span className="cat"><span className="dot" />{names.cat[n.category] || n.category}</span>
                    <span className="when">{ago(n.created_at, lang)}</span>
                  </span>
                  <span className="item-text">{n.text || "—"}</span>
                  <span className="item-foot">
                    <span className="tags">
                      {n.theme && n.theme !== "none" ? <span className="tag">{names.theme[n.theme]}</span> : null}
                      {n.app && n.app !== "other" ? <span className="tag">{n.app}</span> : null}
                      {n.lang && n.lang !== "und" ? <span className="tag">{n.lang}</span> : null}
                    </span>
                    <span className="conf-badge" title={t.conf}>
                      <span className="meter"><span style={{ width: `${Math.round(n.category_conf * 100)}%` }} /></span>
                      {Math.round(n.category_conf * 100)}%
                    </span>
                  </span>
                  <span className="item-id">#{n.note_id}{n.country ? ` · ${n.country}` : ""}</span>
                </button>
              </li>
            ))}
          </ol>
          {list && !list.items.length && !loading && !list.restricted ? <p className="muted pad">{t.empty}</p> : null}
          {pages > 1 ? <Pager page={page} pages={pages} setPage={setPage} /> : null}
      </>
    </aside>
  );
}

/** One thin bar split by category: what the notes in view need. */
function Mix({ t, names, byCat }) {
  const rows = Object.entries(byCat || {}).filter(([, n]) => n > 0).sort((a, b) => b[1] - a[1]);
  const total = rows.reduce((s, [, n]) => s + n, 0);
  if (!total) return <div className="mix empty" />;
  return (
    <div className="mix" title={t.mix}>
      <div className="mix-bar">
        {rows.map(([c, n]) => (
          <span key={c} style={{ flexGrow: n, background: COLORS[c] || "#999" }}
                title={`${names.cat[c] || c}: ${fmt(n)} (${Math.round((n / total) * 100)}%)`} />
        ))}
      </div>
      <div className="mix-keys">
        {rows.slice(0, 3).map(([c, n]) => (
          <span key={c}><i style={{ background: COLORS[c] }} />{names.cat[c] || c} <b>{Math.round((n / total) * 100)}%</b></span>
        ))}
      </div>
    </div>
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
