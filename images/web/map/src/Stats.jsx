import { useEffect, useMemo, useState } from "react";
import { api, colorOf } from "./api.js";
import { ago, startLang, TEXT } from "./i18n.js";
import Header from "./Header.jsx";

const fmt = (n) => (n == null ? "–" : n.toLocaleString());
const pct = (ok, n) => (n ? `${Math.round((100 * ok) / n)}%` : "–");
const OSM = "https://www.openstreetmap.org";
const MIN_CHECKED = 30;   // fewer labels than this: a % would mislead
const rate = (ok, n) => (n >= MIN_CHECKED ? pct(ok, n) : "–");

/** /stats: what people say about the model, and notes closed on OSM.
 *  ?user=<name> shows one person. Notes with personal data have no id or text here. */
export default function Stats() {
  const [lang, setLangState] = useState(startLang);
  const t = TEXT[lang];
  const [me, setMe] = useState(null);
  const [options, setOptions] = useState(null);
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const user = new URLSearchParams(location.search).get("user") || "";

  const setLang = (l) => {
    setLangState(l);
    try { localStorage.setItem("viewer_lang", l); } catch { /* private mode */ }
  };

  useEffect(() => {
    api.me().then(setMe).catch(() => setMe({ osm_login: false }));
    api.options().then(setOptions).catch(() => {});
    api.stats(user).then(setData).catch((e) => setError(e.message));
  }, [user]);
  useEffect(() => { document.documentElement.lang = lang; document.title = `${t.statsTitle} · ${t.title}`; }, [lang, t]);

  const names = useMemo(() => {
    const key = lang === "es" ? "name_es" : "name_en";
    return Object.fromEntries((options?.all_categories || []).map((c) => [c.code, c[key]]));
  }, [options, lang]);
  const cat = (c) => (c ? <span className="scat" style={{ "--cat": colorOf(c) }}><span className="dot" />{names[c] || c}</span> : "–");
  const myName = me?.user?.name;

  return (
    <div className="shell">
      <Header t={t} lang={lang} setLang={setLang} me={me} options={options} />
      <main className="statspage">
        <div className="stats-head">
          <h1>{user ? user : t.statsTitle}</h1>
          <div className="seg" role="group">
            <a className={user ? "" : "on"} href="/stats">{t.everyone}</a>
            {myName ? <a className={user === myName ? "on" : ""} href={`/stats?user=${encodeURIComponent(myName)}`}>{t.me}</a> : null}
          </div>
        </div>
        {error ? <p className="muted">{error}</p> : null}
        {!data ? <p className="muted">{t.loading}</p> : user ? (
          <UserView u={data.user} t={t} lang={lang} cat={cat} />
        ) : (
          <EveryoneView e={data.everyone} t={t} lang={lang} cat={cat} />
        )}
        <p className="muted small">{t.statsNote}</p>
      </main>
    </div>
  );
}

function Tile({ n, label }) {
  return <div className="tile"><b>{n}</b><span>{label}</span></div>;
}

function NoteRef({ r, t, map }) {
  if (r.private) return <span className="muted">{t.privateHidden}</span>;
  const href = map ? `/?note=${r.note_id}` : `${OSM}/note/${r.note_id}`;
  return <a href={href} target={map ? undefined : "_blank"} rel="noreferrer">#{r.note_id}</a>;
}

function Verdict({ r }) {
  if (!r.model || r.category === "unsure") return null;
  return <span title={r.model}>{r.category === r.model ? "👍" : "👎"}</span>;
}

function EveryoneView({ e, t, lang, cat }) {
  return (
    <>
      <section className="tiles">
        <Tile n={fmt(e.reviewed)} label={t.reviewed} />
        <Tile n={rate(e.ok, e.checked)} label={`${t.modelRight} (${fmt(e.checked)} ${t.labelsShort})`} />
        <Tile n={fmt(e.people)} label={t.people} />
        <Tile n={fmt(e.closed_30d)} label={t.closed30} />
      </section>

      <section className="card">
        <h2>{t.byCategory}</h2>
        {e.by_category.length ? e.by_category.map((r) => (
          <div className="barrow" key={r.category}>
            {cat(r.category)}
            <span className="hbar"><span style={{ width: pct(r.ok, r.n), background: "var(--ink)" }} /></span>
            <b>{pct(r.ok, r.n)}</b>
            <span className="muted">👍 {r.ok} · 👎 {r.n - r.ok}</span>
          </div>
        )) : <p className="muted">{t.noData}</p>}
      </section>

      <div className="cols">
        <section className="card">
          <h2>{t.fixes}</h2>
          {e.fixes.length ? (
            <table><tbody>{e.fixes.map((r) => (
              <tr key={r.from + r.to}><td>{cat(r.from)}</td><td>→</td><td>{cat(r.to)}</td><td className="num">{r.n}</td></tr>
            ))}</tbody></table>
          ) : <p className="muted">{t.noData}</p>}
        </section>
        <section className="card">
          <h2>{t.topHelpers}</h2>
          <ol className="rank">{e.top.map((r) => (
            <li key={r.user_name}><a href={`/stats?user=${encodeURIComponent(r.user_name)}`}>{r.user_name}</a><span className="num">{fmt(r.n)}</span></li>
          ))}</ol>
        </section>
      </div>

      <section className="card">
        <h2>{t.latest}</h2>
        <table><tbody>{e.latest.map((r, i) => (
          <tr key={i}>
            <td><NoteRef r={r} t={t} map /></td>
            <td>{r.model && r.model !== r.category ? <>{cat(r.model)} → </> : null}{cat(r.category)}</td>
            <td><Verdict r={r} /></td>
            <td><a href={`/stats?user=${encodeURIComponent(r.user_name)}`}>{r.user_name}</a></td>
            <td className="muted num">{ago(r.updated_at, lang)}</td>
          </tr>
        ))}</tbody></table>
      </section>
    </>
  );
}

function UserView({ u, t, lang, cat }) {
  if (!u) return null;
  return (
    <>
      <section className="tiles">
        <Tile n={fmt(u.reviewed)} label={t.reviewed} />
        <Tile n={rate(u.ok, u.checked)} label={`${t.modelRight} (${fmt(u.checked)} ${t.labelsShort})`} />
        <Tile n={u.reviewed ? `#${u.rank}` : "–"} label={t.rank} />
        <Tile n={fmt(u.closed_n)} label={t.closedOsm} />
      </section>

      <section className="card">
        <h2>{t.myLabels}</h2>
        {u.labels.length ? (
          <table>
            <thead><tr><th>{t.note}</th><th>{t.model}</th><th>{t.label}</th><th /><th /></tr></thead>
            <tbody>{u.labels.map((r, i) => (
              <tr key={i}>
                <td><NoteRef r={r} t={t} map={r.status === "open"} /></td>
                <td>{cat(r.model)}</td>
                <td>{cat(r.category)}</td>
                <td><Verdict r={r} /></td>
                <td className="muted num">{ago(r.updated_at, lang)}</td>
              </tr>
            ))}</tbody>
          </table>
        ) : <p className="muted">{t.noData}</p>}
      </section>

      <section className="card">
        <h2>{t.closedOsm}</h2>
        {u.closed.length ? (
          <table>
            <thead><tr><th>{t.note}</th><th>{t.model}</th><th>{t.closeComment}</th><th /></tr></thead>
            <tbody>{u.closed.map((r, i) => (
              <tr key={i}>
                <td><NoteRef r={r} t={t} /></td>
                <td>{r.private ? "–" : cat(r.model)}</td>
                <td className="body">{r.private ? "" : (r.body || "–")}</td>
                <td className="muted num">{ago(r.created_at, lang)}</td>
              </tr>
            ))}</tbody>
          </table>
        ) : <p className="muted">{t.noData}</p>}
      </section>
    </>
  );
}
