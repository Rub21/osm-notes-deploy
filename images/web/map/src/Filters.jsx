import { useState } from "react";
import { api, colorOf, EMPTY_FILTERS } from "./api.js";

const fmt = (n) => (n == null ? "" : n.toLocaleString());
const CONF = [0, 0.5, 0.7, 0.9];
const AGES = [["anyAge", 0], ["days30", 30], ["year", 365], ["years3", 1095]];
const PRIVATE = "personal_data";

/** Filters page over the map, like OSMCha: edit a draft, then Apply. */
export default function Filters({ t, me, options, names, filters, setFilters, list, bbox, onClose }) {
  const [draft, setDraft] = useState(filters);
  const [copied, setCopied] = useState(false);
  const set = (patch) => setDraft({ ...draft, ...patch });
  const toggleCat = (code) => set({
    cat: draft.cat.includes(code) ? draft.cat.filter((c) => c !== code) : [...draft.cat, code],
  });
  const groups = [["map", t.groupMap], ["close", t.groupClose]].map(([grp, title]) => ({
    grp, title, cats: (options?.categories || []).filter((c) => (grp === "map") === (c.grp === "map")),
  }));
  const toggleGroup = (cats) => {
    const codes = cats.map((c) => c.code).filter((c) => c !== PRIVATE);
    const all = codes.every((c) => draft.cat.includes(c));
    set({ cat: all ? draft.cat.filter((c) => !codes.includes(c)) : [...new Set([...draft.cat, ...codes])] });
  };
  const count = (code) => (code === PRIVATE && !me?.moderator
    ? options?.private_total : list?.by_category?.[code]) || 0;
  const apply = () => { setFilters({ ...draft, q: draft.q.trim() }); onClose(); };
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(api.exportUrl(filters, bbox, false));
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch { /* clipboard blocked */ }
  };

  return (
    <section className="fpage" aria-label={t.filters}>
      <header className="fpage-head">
        <span />
        <h2>{t.filters}</h2>
        <div className="fpage-actions">
          <button className="link" onClick={() => setDraft(EMPTY_FILTERS)}>{t.reset}</button>
          <button className="primary" onClick={apply}>{t.apply}</button>
          <button className="x" onClick={onClose} aria-label={t.close}>×</button>
        </div>
      </header>

      <form className="fpage-body" onSubmit={(e) => { e.preventDefault(); apply(); }}>
        <h3>{t.basic}</h3>
        <div className="field">
          <label htmlFor="q">{t.text}</label>
          <input id="q" value={draft.q} onChange={(e) => set({ q: e.target.value })} placeholder={t.textHint} />
        </div>
        <div className="row2">
          <div className="field">
            <label htmlFor="age">{t.age}</label>
            <select id="age" value={draft.days} onChange={(e) => set({ days: Number(e.target.value) })}>
              {AGES.map(([k, d]) => <option key={d} value={d}>{t[k]}</option>)}
            </select>
          </div>
          <div className="field">
            <span className="label">{t.conf}</span>
            <div className="seg" role="group" aria-label={t.conf}>
              {CONF.map((c) => (
                <button type="button" key={c} className={draft.conf === c ? "on" : ""} onClick={() => set({ conf: c })}>
                  {c ? `≥ ${Math.round(c * 100)}%` : t.all}
                </button>
              ))}
            </div>
          </div>
        </div>

        <h3>{t.category}</h3>
        {groups.map(({ grp, title, cats }) => (
          <div key={grp} className="group">
            <button type="button" className="group-title" onClick={() => toggleGroup(cats)}>{title}</button>
            <div className="chips">
              {cats.map((c) => {
                const on = draft.cat.includes(c.code);
                const locked = c.code === PRIVATE;
                return (
                  <button type="button" key={c.code} className={`chip${on ? " on" : ""}${locked ? " locked" : ""}`}
                          style={{ "--cat": colorOf(c.code) }} onClick={() => toggleCat(c.code)}
                          title={locked ? `${t.modOnly}. ${c.description}` : c.description} aria-pressed={on}>
                    <span className="dot" />{names.cat[c.code]}
                    <span className="n">{fmt(count(c.code))}</span>
                    {locked ? <span className="lock">🔒 {t.modOnly}</span> : null}
                  </button>
                );
              })}
            </div>
          </div>
        ))}

        <h3>{t.more}</h3>
        <div className="row3">
          <div className="field">
            <label htmlFor="lang">{t.lang}</label>
            <select id="lang" value={draft.lang} onChange={(e) => set({ lang: e.target.value })}>
              <option value="">{t.all}</option>
              {options?.langs.map((l) => <option key={l.lang} value={l.lang}>{l.lang} ({fmt(l.n)})</option>)}
            </select>
          </div>
          <div className="field">
            <label htmlFor="app">{t.app}</label>
            <select id="app" value={draft.app} onChange={(e) => set({ app: e.target.value })}>
              <option value="">{t.all}</option>
              {options?.apps.map((a) => (
                <option key={a.app} value={a.app}>{a.app === "none" || a.app === "other" ? t.noApp : a.app} ({fmt(a.n)})</option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor="theme">{t.theme}</label>
            <select id="theme" value={draft.theme} onChange={(e) => set({ theme: e.target.value })}>
              <option value="">{t.all}</option>
              {options?.themes.map((th) => <option key={th.code} value={th.code}>{names.theme[th.code]}</option>)}
            </select>
          </div>
        </div>

        <h3>{t.export}</h3>
        <div className="export">
          <a href={api.exportUrl(filters, bbox, true)}>{t.download}</a>
          <button type="button" onClick={copy}>{copied ? t.copied : t.copyUrl}</button>
          <p className="muted">{t.exportHint}</p>
        </div>
        <p className="about">{t.about}</p>
      </form>
    </section>
  );
}
