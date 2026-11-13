import { ago } from "./i18n.js";

const fmt = (n) => (n == null ? "" : n.toLocaleString());

/** Top bar across the page, like OSMCha: brand, stats, labeling link, language, login. */
export default function Header({ t, lang, setLang, me, options }) {
  const here = () => location.pathname + location.search + location.hash;
  return (
    <header className="top">
      <a className="logo" href="/" aria-label={t.title}>
        <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
          <path d="M5 3h14a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2h-6l-4 4v-4H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z" fill="currentColor" />
          <path d="M8 8h8M8 11.5h5" stroke="var(--bg)" strokeWidth="1.8" strokeLinecap="round" />
        </svg>
        <span>
          <strong>{t.title}</strong>
          <small>{t.tagline}</small>
        </span>
      </a>

      {options ? (
        <p className="stats">
          <span><b>{fmt(options.total)}</b> {t.notes}</span>
          {options.updated ? <span>{t.updated} {ago(options.updated, lang)}</span> : null}
          {options.models?.[0] ? <span className="model">{options.models[0]}</span> : null}
        </p>
      ) : <span />}

      <nav className="who">
        <a className="ghost" href="/stats">{t.statsTitle}</a>
        <a className="cta" href="/label" title={t.helpHint}>{t.help}</a>
        <button className="ghost" onClick={() => setLang(lang === "en" ? "es" : "en")} aria-label="language">
          {lang === "en" ? "ES" : "EN"}
        </button>
        {me?.osm_login ? (me.user ? (
          <>
            <a className="ghost user" href={`/stats?user=${encodeURIComponent(me.user.name)}`} title={t.myStats}>
              <span className="avatar">{me.user.name.slice(0, 1).toUpperCase()}</span>{me.user.name}
            </a>
            <a className="ghost" href={`/auth/logout?next=${encodeURIComponent(here())}`} title={t.logout} aria-label={t.logout}>⎋</a>
          </>
        ) : (
          <a className="ghost" href={`/auth/login?next=${encodeURIComponent(here())}`}>{t.login}</a>
        )) : null}
      </nav>
    </header>
  );
}
