import { useEffect, useState } from "react";
import { api, colorOf } from "./api.js";

const pct = (x) => (x == null ? "" : `${Math.round(x * 100)}%`);

/** Right panel with one note: prediction, text, links, and 👍/👎 feedback. */
export default function NoteDetail({ id, t, names, options, me, onClose, onLoaded }) {
  const [note, setNote] = useState(null);
  const [error, setError] = useState("");
  const [fixing, setFixing] = useState(false);
  const [cat, setCat] = useState("");
  const [theme, setTheme] = useState("");
  const [status, setStatus] = useState("");
  const [name, setName] = useState(() => {
    try { return localStorage.getItem("viewer_user") || ""; } catch { return ""; }
  });

  useEffect(() => {
    let live = true;
    setNote(null); setError(""); setFixing(false); setStatus("");
    api.note(id)
      .then((n) => { if (live) { setNote(n); onLoaded(n); } })
      .catch((e) => { if (live) setError(e.status === 404 ? t.notFound : e.message); });
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  const needLogin = me?.osm_login && !me.user;
  const send = async (category, th) => {
    setStatus("");
    try {
      if (!me?.osm_login) {
        try { localStorage.setItem("viewer_user", name); } catch { /* private mode */ }
      }
      await api.feedback({ note_id: id, category, theme: th || null, user: name || null });
      setStatus(t.saved);
      setFixing(false);
      setNote((n) => ({ ...n, my_label: { category, theme: th } }));
    } catch (e) {
      setStatus(e.message);
    }
  };

  if (error) {
    return (
      <aside className="detail">
        <button className="x" onClick={onClose} aria-label={t.close}>×</button>
        <p className="muted">#{id} · {error}</p>
      </aside>
    );
  }
  if (!note) {
    return (
      <aside className="detail">
        <button className="x" onClick={onClose} aria-label={t.close}>×</button>
        <p className="muted">{t.loading}</p>
      </aside>
    );
  }

  const osm = me?.osm_url || "https://www.openstreetmap.org";
  const shown = note.user_text || note.text;
  return (
    <aside className="detail" style={{ "--cat": colorOf(note.category) }}>
      <button className="x" onClick={onClose} aria-label={t.close}>×</button>
      <div className="detail-cat">
        <span className="dot" />
        <strong>{names.cat[note.category] || note.category}</strong>
        <span className="pill">{pct(note.category_conf)}</span>
      </div>
      <div className="bar"><span style={{ width: pct(note.category_conf) }} /></div>
      <p className="meta">
        {note.category_2 ? <>{t.second}: {names.cat[note.category_2] || note.category_2} {pct(note.category_2_conf)} · </> : null}
        {t.theme}: {names.theme[note.theme] || note.theme} {note.theme_conf != null && pct(note.theme_conf)}
        {note.source === "rule" ? ` · ${t.rule}` : ""}
      </p>

      <blockquote className="note-text">{shown || "—"}</blockquote>
      {note.context ? <p className="ctx"><span>{t.appContext}</span> {note.context}</p> : null}
      {note.full_text && note.full_text !== shown ? (
        <details className="orig"><summary>{t.original}</summary><pre>{note.full_text}</pre></details>
      ) : null}

      <p className="meta">
        #{note.note_id} · {note.lang || "?"} · {note.app || "—"} · {note.country || ""} ·{" "}
        {note.created_at ? new Date(note.created_at).toLocaleDateString() : ""}
      </p>
      <p className="links">
        <a href={`${osm}/note/${note.note_id}`} target="_blank" rel="noreferrer">{t.openOsm} ↗</a>
        <a href={`${osm}/edit?editor=id#map=19/${note.lat}/${note.lon}`} target="_blank" rel="noreferrer">{t.editId} ↗</a>
      </p>

      <div className="feedback">
        {note.my_label ? (
          <p className="mine">{t.yourLabel}: <strong>{names.cat[note.my_label.category] || note.my_label.category}</strong>
            {note.my_label.theme ? ` · ${names.theme[note.my_label.theme] || note.my_label.theme}` : ""}</p>
        ) : null}
        {needLogin ? (
          <p className="muted">
            <a href={`/auth/login?next=${encodeURIComponent(location.pathname + location.search + location.hash)}`}>{t.login}</a> · {t.needLogin}
          </p>
        ) : (
          <>
            {!me?.osm_login ? (
              <input className="name" placeholder={t.yourName} value={name} onChange={(e) => setName(e.target.value)} />
            ) : null}
            {!fixing ? (
              <div className="row">
                <span>{t.right}</span>
                <button onClick={() => send(note.category, note.theme)}>👍 {t.yes}</button>
                <button onClick={() => { setFixing(true); setCat(note.category); setTheme(note.theme || ""); }}>👎 {t.no}</button>
              </div>
            ) : (
              <div className="fix">
                <select value={cat} onChange={(e) => setCat(e.target.value)}>
                  {options.categories.map((c) => <option key={c.code} value={c.code}>{names.cat[c.code]}</option>)}
                  <option value="personal_data">{names.cat.personal_data || "personal_data"}</option>
                </select>
                <select value={theme} onChange={(e) => setTheme(e.target.value)}>
                  {options.themes.map((th) => <option key={th.code} value={th.code}>{names.theme[th.code]}</option>)}
                </select>
                <div className="row">
                  <button className="primary" onClick={() => send(cat, theme)}>{t.save}</button>
                  <button onClick={() => setFixing(false)}>{t.cancel}</button>
                </div>
              </div>
            )}
          </>
        )}
        {status ? <p className="status">{status}</p> : null}
        {note.labels?.length ? (
          <details className="people"><summary>{t.people} ({note.labels.length})</summary>
            <ul>{note.labels.map((l) => (
              <li key={l.user_name}>{l.user_name}: {names.cat[l.category] || l.category}{l.theme ? ` · ${names.theme[l.theme] || l.theme}` : ""}</li>
            ))}</ul>
          </details>
        ) : null}
      </div>
    </aside>
  );
}
