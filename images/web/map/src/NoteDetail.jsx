import { useEffect, useState } from "react";
import { api, colorOf } from "./api.js";
import { canClose, closeNote } from "./osm.js";

const REASONS = ["done", "spam", "empty", "not_mappable", "other"];
// Comment posted on OSM when closing; Spanish for Spanish notes, else English. Editable.
const CLOSE_TEXT = {
  en: {
    done: "Fixed on the map. Thanks for the note!",
    spam: "Closing: this is advertising or spam, not information for the map.",
    empty: "Closing: the note has no information we can act on.",
    not_mappable: "Closing: this is not something we can map in OpenStreetMap.",
    other: "",
  },
  es: {
    done: "Corregido en el mapa. ¡Gracias por la nota!",
    spam: "Cierro la nota: es publicidad o spam, no información para el mapa.",
    empty: "Cierro la nota: no tiene información para actuar.",
    not_mappable: "Cierro la nota: no es algo que se pueda mapear en OpenStreetMap.",
    other: "",
  },
};

/** Close the note on OSM with a reason and a public comment. */
function CloseBox({ note, t, me, onClosed }) {
  const words = CLOSE_TEXT[note.lang === "es" ? "es" : "en"];
  const first = REASONS.includes(note.category) ? note.category : "done";
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState(first);
  const [text, setText] = useState(words[first]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [switchUser, setSwitchUser] = useState(false);

  const pick = (r) => {
    // Keep the person's own text; only swap our default ones.
    if (Object.values(words).includes(text)) setText(words[r]);
    setReason(r);
  };

  const submit = () => {   // no await before closeNote: the login popup must open now
    setBusy(true); setMsg("");
    closeNote(me, note.note_id, text, switchUser)
      .then(async (res) => {
        setSwitchUser(false);
        await api.close({ note_id: note.note_id, reason });
        onClosed(res === "already" ? t.closedBefore : t.closedOk);
      })
      .catch((e) => {
        if (e.code === "other_user") {
          setSwitchUser(true);
          setMsg(t.closeOtherUser.replace("{name}", e.name).replace("{me}", me.user.name));
        } else if (e.code === "popup") setMsg(t.closePopup);
        else if (e.code === "expired") setMsg(t.closeExpired);
        else setMsg(e.message);
      })
      .finally(() => setBusy(false));
  };

  if (!open) {
    return (
      <div className="close-osm">
        <button onClick={() => setOpen(true)}>✓ {t.closeOnOsm}</button>
        {msg ? <p className="status">{msg}</p> : null}
      </div>
    );
  }
  return (
    <div className="close-osm fix">
      <label>{t.closeReason}
        <select value={reason} onChange={(e) => pick(e.target.value)} disabled={busy}>
          {REASONS.map((r) => <option key={r} value={r}>{t.reasons[r]}</option>)}
        </select>
      </label>
      <label>{t.closeComment}
        <textarea rows={3} value={text} maxLength={2000} onChange={(e) => setText(e.target.value)} disabled={busy} />
      </label>
      <div className="row">
        <button className="primary" onClick={submit} disabled={busy}>{busy ? t.closing : t.closeBtn}</button>
        {/* Always on: if the OSM pop-up is closed without approving, osm-auth never answers. */}
        <button onClick={() => { setOpen(false); setBusy(false); setMsg(""); }}>{t.cancel}</button>
      </div>
      <p className="muted small">{t.closeHint}</p>
      {msg ? <p className="status">{msg}</p> : null}
    </div>
  );
}

const pct = (x) => (x == null ? "" : `${Math.round(x * 100)}%`);
// Photos StreetComplete attaches (westnordost.de is its old host and redirects).
// Only these hosts: we do not load images from any link people write.
const PHOTO = /https?:\/\/(?:streetcomplete\.app|westnordost\.de)\/p\/\d+\.(?:jpe?g|png|webp)/gi;

/** Right panel with one note: prediction, text, links, and 👍/👎 feedback. */
export default function NoteDetail({ id, t, names, options, me, onClose, onLoaded, onSaved }) {
  const [note, setNote] = useState(null);
  const [error, setError] = useState("");
  const [fixing, setFixing] = useState(false);
  const [cat, setCat] = useState("");
  const [theme, setTheme] = useState("");
  const [status, setStatus] = useState("");
  const [closed, setClosed] = useState(false);
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
      setNote((n) => ({ ...n, category, category_conf: 1, category_2: null, theme: th || n.theme,
                        source: "person", my_label: { category, theme: th } }));
      onSaved?.();
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
  const photos = [...new Set((note.full_text || note.text || "").match(PHOTO) || [])].slice(0, 6);
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
        {note.source === "rule" ? ` · ${t.rule}` : note.source === "person" ? ` · ${t.byPerson}` : ""}
      </p>

      <blockquote className="note-text">{shown || "—"}</blockquote>
      {photos.length ? (
        <div className="photos">
          {photos.map((u) => (
            <a key={u} href={u} target="_blank" rel="noreferrer" title={t.photo}>
              <img src={u} alt={t.photo} loading="lazy" referrerPolicy="no-referrer" />
            </a>
          ))}
        </div>
      ) : null}
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
        {canClose(me) && !closed ? (
          <CloseBox note={note} t={t} me={me} onClosed={(msg) => {
            setClosed(true); setStatus(msg);
            onSaved?.();
          }} />
        ) : null}
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
