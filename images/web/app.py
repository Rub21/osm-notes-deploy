"""Labeling API for OSM notes.

- GET  /api/categories            fixed label set (ml.categories)
- GET  /api/themes                second label axis: what the note is about
- GET  /api/langs                 languages with note and label counts
- GET  /api/batch?lang=&size=     random unlabeled notes in a language (JSON)
- GET  /api/batch.csv?lang=&size= same batch as CSV to label offline
- POST /api/labels                save labels from the page
- POST /api/import                upload a CSV: `category` = reviewed label,
                                  only `claude_category` = suggestion to review
- GET  /api/review?lang=&size=    AI suggestions not reviewed yet
- GET  /api/stats                 labels per user/language/category
- GET  /api/labels.csv            all labels with note text (filters: lang,
                                    user, since, category, theme, changed)
  batch.csv, import, review and labels.csv work only from the server or an SSH
  tunnel, not through Cloudflare (see local_only).
- GET  /api/me                    who is logged in
- GET  /, /api/map/*              map viewer of the model's predictions (see below)
- GET  /label                      labeling page
- GET  /auth/login, /auth/callback, /auth/logout   OSM OAuth 2.0 login

Login: when OSM_CLIENT_ID is set, writes need an OSM login and the label
is saved with the OSM username. Without it (local dev) the page sends a
free-text user name.

CSV format (export and import):
  note_id,lang,country,app,template,user_text,text,category,theme,claude_category,claude_theme
Only note_id and category are required on import. A row with only
claude_category goes to the review queue. theme / claude_theme are optional.
"""

import base64
import csv
import datetime as dt
import hashlib
import io
import os
import random
import re
import secrets
import time
from urllib.parse import urlencode

import httpx

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

OSM_URL = os.environ.get("OSM_URL", "https://www.openstreetmap.org").rstrip("/")
OSM_API_URL = os.environ.get("OSM_API_URL", "https://api.openstreetmap.org").rstrip("/")
OSM_CLIENT_ID = os.environ.get("OSM_CLIENT_ID", "")
OSM_CLIENT_SECRET = os.environ.get("OSM_CLIENT_SECRET", "")
PUBLIC_URL = os.environ.get("PUBLIC_URL", "http://localhost:8090").rstrip("/")
SESSION_SECRET = os.environ.get("SESSION_SECRET") or secrets.token_urlsafe(32)
OSM_LOGIN = bool(OSM_CLIENT_ID)
# Who can see notes with personal data: OSM moderators/admins (role read at login),
# plus the OSM names in MODERATORS (comma list). "*" = everyone (local dev only).
MODERATORS = {x.strip().lower() for x in os.environ.get("MODERATORS", "").split(",") if x.strip()}
MAX_BATCH = 1000
NO_THEME_CATEGORIES = {"spam", "empty", "personal_data"}
CSV_COLUMNS = ["note_id", "lang", "country", "app", "template", "user_text", "text",
               "category", "theme", "claude_category", "claude_theme"]
USER_RE = re.compile(r"^[\w .@-]{2,40}$")

# Uses PGHOST, PGUSER (labeler), PGPASSWORD, PGDATABASE.
pool = ConnectionPool(kwargs={"row_factory": dict_row}, min_size=1, max_size=5, open=True)
app = FastAPI(title="OSM notes")
app.add_middleware(
    SessionMiddleware, secret_key=SESSION_SECRET, session_cookie="labeler_session",
    max_age=30 * 24 * 3600, same_site="lax", https_only=PUBLIC_URL.startswith("https"))

NOTES_SQL = """
SELECT nl.note_id, nl.lang, nl.app, nl.template, nl.poi_type, nl.theme_auto, nl.context,
       COALESCE(c.country_name_en, c.country_name, 'Unknown') AS country,
       n.created_at, n.status, n.latitude AS lat, n.longitude AS lon,
       t.body AS text, COALESCE(nl.user_text, t.body) AS user_text
FROM unnest(%(ids)s::int[]) WITH ORDINALITY AS p(note_id, ord)
JOIN ml.note_lang nl ON nl.note_id = p.note_id
JOIN notes n ON n.note_id = p.note_id
JOIN note_comments nc ON nc.note_id = p.note_id AND nc.event = 'opened'
JOIN LATERAL (
  SELECT x.body FROM note_comments_text x
  WHERE x.note_id = nc.note_id AND x.sequence_action = nc.sequence_action
  LIMIT 1) t ON true
LEFT JOIN countries c ON c.country_id = n.id_country
ORDER BY p.ord
"""

PICK_SQL = """
SELECT l.note_id FROM ml.note_lang l
WHERE l.lang = %(lang)s AND l.rnd {op} %(r)s
  AND l.rule_category IS NULL
  AND NOT EXISTS (SELECT 1 FROM ml.note_labels x WHERE x.note_id = l.note_id)
ORDER BY l.rnd {order}
LIMIT %(n)s
"""


def current_user(request: Request, typed: str | None = None) -> str:
    """OSM login when configured; otherwise the name typed in the page."""
    if OSM_LOGIN:
        user = request.session.get("user")
        if not user:
            raise HTTPException(401, "Log in with your OSM account")
        return user["name"]
    typed = (typed or "").strip()
    if not USER_RE.match(typed):
        raise HTTPException(400, "Invalid user name (2-40 letters, numbers, . @ -)")
    return typed


def is_local(request: Request) -> bool:
    """True when the request does not come through the Cloudflare tunnel: from the
    server itself or an SSH tunnel. Cloudflare always adds CF-Connecting-IP."""
    return "cf-connecting-ip" not in request.headers


def local_only(request: Request) -> None:
    """Bulk tools (label export, CSV import, AI review) only work from the server,
    so nobody on the internet can download all labels or overwrite many at once."""
    if not is_local(request):
        raise HTTPException(404, "Not found")


def is_moderator(request: Request) -> bool:
    if "*" in MODERATORS:
        return True
    user = request.session.get("user") or {}
    return bool(user) and (bool({"moderator", "administrator"} & set(user.get("roles") or []))
                           or user.get("name", "").lower() in MODERATORS)


@app.get("/api/me")
def me(request: Request):
    return {"osm_login": OSM_LOGIN, "osm_url": OSM_URL, "user": request.session.get("user"),
            "moderator": is_moderator(request), "local": is_local(request)}


@app.get("/auth/login")
def login(request: Request):
    if not OSM_LOGIN:
        raise HTTPException(404, "OSM login is not set up")
    # PKCE + state, kept in the signed session cookie.
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    request.session.update(oauth_state=state, oauth_verifier=verifier)
    nxt = request.query_params.get("next", "")
    if nxt.startswith("/") and not nxt.startswith("//"):   # only local paths
        request.session["next"] = nxt
    query = urlencode({
        "response_type": "code", "client_id": OSM_CLIENT_ID,
        "redirect_uri": f"{PUBLIC_URL}/auth/callback", "scope": "read_prefs",
        "state": state, "code_challenge": challenge, "code_challenge_method": "S256"})
    return RedirectResponse(f"{OSM_URL}/oauth2/authorize?{query}")


@app.get("/auth/callback")
def callback(request: Request, code: str = "", state: str = ""):
    if not OSM_LOGIN:
        raise HTTPException(404, "OSM login is not set up")
    expected = request.session.pop("oauth_state", None)
    verifier = request.session.pop("oauth_verifier", None)
    if not code or not expected or state != expected:
        raise HTTPException(400, "Invalid or expired login. Please try again.")
    with httpx.Client(timeout=15) as http:
        tok = http.post(f"{OSM_URL}/oauth2/token", data={
            "grant_type": "authorization_code", "code": code,
            "redirect_uri": f"{PUBLIC_URL}/auth/callback",
            "client_id": OSM_CLIENT_ID, "client_secret": OSM_CLIENT_SECRET,
            "code_verifier": verifier})
        if tok.status_code != 200:
            raise HTTPException(502, "OSM rejected the login")
        details = http.get(f"{OSM_API_URL}/api/0.6/user/details.json", headers={
            "Authorization": f"Bearer {tok.json()['access_token']}"})
        if details.status_code != 200:
            raise HTTPException(502, "Could not read the OSM user")
    u = details.json()["user"]
    # Only the name and id are kept; the OSM access token is dropped.
    request.session["user"] = {"id": u["id"], "name": u["display_name"],
                               "avatar": (u.get("img") or {}).get("href"),
                               "roles": [r for r in (u.get("roles") or []) if isinstance(r, str)]}
    return RedirectResponse(request.session.pop("next", None) or "/")


@app.get("/auth/logout")
def logout(request: Request):
    request.session.clear()
    nxt = request.query_params.get("next", "")
    return RedirectResponse(nxt if nxt.startswith("/") and not nxt.startswith("//") else "/")


def categories() -> dict[str, dict]:
    with pool.connection() as conn:
        rows = conn.execute(
            "SELECT code, grp, action, name_es, name_en, example, description, example_en, "
            "description_en, sort "
            "FROM ml.categories WHERE active ORDER BY sort").fetchall()
    return {r["code"]: r for r in rows}


def themes() -> dict[str, dict]:
    with pool.connection() as conn:
        rows = conn.execute(
            "SELECT code, name_es, name_en, tags, sort FROM ml.themes "
            "WHERE active ORDER BY sort").fetchall()
    return {r["code"]: r for r in rows}


def pick_batch(lang: str, size: int) -> list[dict]:
    """Random unlabeled notes. Random start point + index on (lang, rnd);
    wraps around if the tail is short. Different labelers rarely collide."""
    r = random.random()
    with pool.connection() as conn:
        ids = [x["note_id"] for x in conn.execute(
            PICK_SQL.format(op=">=", order="ASC"), {"lang": lang, "r": r, "n": size})]
        if len(ids) < size:
            ids += [x["note_id"] for x in conn.execute(
                PICK_SQL.format(op="<", order="DESC"),
                {"lang": lang, "r": r, "n": size - len(ids)})]
        if not ids:
            return []
        return conn.execute(NOTES_SQL, {"ids": ids}).fetchall()


@app.get("/")
def index():
    """The map viewer is the main page."""
    return FileResponse("/app/static/map/index.html")


@app.get("/label")
def label_page():
    """Labeling page, for people who want to help."""
    return FileResponse("/app/static/index.html")


@app.get("/privacy")
def privacy():
    return FileResponse("/app/static/privacy.html")


@app.get("/api/categories")
def get_categories():
    return list(categories().values())


@app.get("/api/themes")
def get_themes():
    return list(themes().values())


_langs_cache: dict = {"at": 0.0, "data": None}


@app.get("/api/langs")
def get_langs():
    # Counting millions of rows is slow; cache for 5 minutes.
    if _langs_cache["data"] is None or time.time() - _langs_cache["at"] > 300:
        with pool.connection() as conn:
            rows = conn.execute("""
                SELECT l.lang, count(*) AS notes, count(DISTINCT x.note_id) AS labeled
                FROM ml.note_lang l
                LEFT JOIN ml.note_labels x ON x.note_id = l.note_id
                GROUP BY l.lang ORDER BY notes DESC""").fetchall()
        _langs_cache.update(at=time.time(), data=rows)
    return _langs_cache["data"]


@app.get("/api/batch")
def get_batch(lang: str = Query(..., max_length=8), size: int = Query(50, ge=1, le=MAX_BATCH)):
    return pick_batch(lang, size)


@app.get("/api/batch.csv", dependencies=[Depends(local_only)])
def get_batch_csv(lang: str = Query(..., max_length=8),
                  size: int = Query(200, ge=1, le=MAX_BATCH)):
    rows = pick_batch(lang, size)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_COLUMNS, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({**r, "category": "", "theme": "", "claude_category": "",
                    "claude_theme": r.get("theme_auto") or ""})
    name = f"notes_{lang}_{time.strftime('%Y%m%d_%H%M')}.csv"
    # utf-8-sig so Excel opens accents correctly.
    return StreamingResponse(
        iter([buf.getvalue().encode("utf-8-sig")]), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{name}"'})


class Label(BaseModel):
    note_id: int
    category: str
    theme: str | None = None
    claude_category: str | None = None
    claude_theme: str | None = None


class LabelsIn(BaseModel):
    user: str | None = None
    labels: list[Label]


def save_suggestions(user: str, items: list[tuple[int, str, str | None]]) -> int:
    """Store AI suggestions to review later. A new upload replaces the old one."""
    if not items:
        return 0
    with pool.connection() as conn:
        langs = {r["note_id"]: r["lang"] for r in conn.execute(
            "SELECT note_id, lang FROM ml.note_lang WHERE note_id = ANY(%s)",
            ([i[0] for i in items],))}
        with conn.cursor() as cur:
            cur.executemany("""
                INSERT INTO ml.note_suggestions (note_id, lang, category, theme, imported_by)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (note_id) DO UPDATE
                SET category = EXCLUDED.category, theme = EXCLUDED.theme,
                    imported_by = EXCLUDED.imported_by, created_at = now()""",
                [(nid, langs.get(nid), cat, th, user) for nid, cat, th in items])
    return len(items)


def save_labels(user: str, items: list[tuple]) -> dict:
    """Upsert (note_id, user). Items already validated:
    (note_id, category, claude_category, theme, claude_theme)."""
    if not items:
        return {"saved": 0}
    # Spam, empty and private notes are not about a feature: theme is always none.
    items = [(nid, cat, cc, "none" if cat in NO_THEME_CATEGORIES else th, cth)
             for nid, cat, cc, th, cth in items]
    with pool.connection() as conn:
        langs = {r["note_id"]: r["lang"] for r in conn.execute(
            "SELECT note_id, lang FROM ml.note_lang WHERE note_id = ANY(%s)",
            ([i[0] for i in items],))}
        with conn.cursor() as cur:
            cur.executemany("""
                INSERT INTO ml.note_labels (note_id, lang, category, claude_category,
                                            theme, claude_theme, user_name)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (note_id, user_name) DO UPDATE
                SET category = EXCLUDED.category,
                    theme = COALESCE(EXCLUDED.theme, ml.note_labels.theme),
                    claude_category = COALESCE(EXCLUDED.claude_category,
                                               ml.note_labels.claude_category),
                    claude_theme = COALESCE(EXCLUDED.claude_theme,
                                            ml.note_labels.claude_theme),
                    updated_at = now()""",
                [(nid, langs.get(nid), cat, cc, th, cth, user)
                 for nid, cat, cc, th, cth in items])
    return {"saved": len(items)}


@app.post("/api/labels")
def post_labels(body: LabelsIn, request: Request):
    user = current_user(request, body.user)
    cats, ths = categories(), themes()
    items = []
    for lb in body.labels:
        if lb.category not in cats:
            raise HTTPException(400, f"Unknown category: {lb.category}")
        if lb.theme and lb.theme not in ths:
            raise HTTPException(400, f"Unknown theme: {lb.theme}")
        items.append((lb.note_id, lb.category, lb.claude_category or None,
                      lb.theme or None, lb.claude_theme or None))
    return save_labels(user, items)


def read_csv(raw: bytes) -> list[dict]:
    """Accept Excel quirks: BOM, cp1252, ';' separator (Spanish locale)."""
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if not reader.fieldnames or "note_id" not in [f.strip().lower() for f in reader.fieldnames]:
        raise HTTPException(400, "The CSV needs a note_id column")
    return [{(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
            for row in reader]


@app.post("/api/import", dependencies=[Depends(local_only)])
async def import_csv(request: Request, file: UploadFile = File(...),
                     user: str | None = Form(None)):
    user = current_user(request, user)
    raw = await file.read()
    if len(raw) > 20_000_000:
        raise HTTPException(400, "File too big (max. 20 MB)")
    rows = read_csv(raw)
    cats, ths = categories(), themes()
    # Also accept the category / theme name in Spanish/English, not only the code.
    by_name = {c["name_es"].lower(): k for k, c in cats.items()}
    by_name.update({c["name_en"].lower(): k for k, c in cats.items()})
    theme_by_name = {t["name_es"].lower(): k for k, t in ths.items()}
    theme_by_name.update({t["name_en"].lower(): k for k, t in ths.items()})

    ids = [int(r["note_id"]) for r in rows if r.get("note_id", "").isdigit()]
    with pool.connection() as conn:
        known = {r["note_id"] for r in conn.execute(
            "SELECT note_id FROM ml.note_lang WHERE note_id = ANY(%s)", (ids,))}

    def code(value: str) -> str | None:
        value = value.lower()
        return (value if value in cats else by_name.get(value, value)) if value else None

    def theme_code(value: str) -> str | None:
        value = value.lower()
        return (value if value in ths else theme_by_name.get(value, value)) if value else None

    items, suggestions, errors, skipped = [], [], [], 0
    for line, r in enumerate(rows, start=2):  # line 1 is the header
        nid = r.get("note_id", "")
        cat, cc = code(r.get("category", "")), code(r.get("claude_category", ""))
        th, cth = theme_code(r.get("theme", "")), theme_code(r.get("claude_theme", ""))
        if not cat and not cc:
            skipped += 1
            continue
        if not nid.isdigit():
            errors.append({"line": line, "note_id": nid, "error": "invalid note_id"})
        elif int(nid) not in known:
            errors.append({"line": line, "note_id": nid, "error": "note does not exist"})
        elif cat and cat not in cats:
            errors.append({"line": line, "note_id": nid, "error": f"unknown category: {cat}"})
        elif cc and cc not in cats:
            errors.append({"line": line, "note_id": nid,
                           "error": f"unknown claude_category: {cc}"})
        elif (th and th not in ths) or (cth and cth not in ths):
            errors.append({"line": line, "note_id": nid,
                           "error": f"unknown theme: {th if th and th not in ths else cth}"})
        elif cat:
            items.append((int(nid), cat, cc, th, cth))
        else:  # only the AI suggestion: goes to the review queue
            suggestions.append((int(nid), cc, cth))
    result = save_labels(user, items)
    suggested = save_suggestions(user, suggestions)
    return {"rows": len(rows), "saved": result["saved"], "suggested": suggested,
            "skipped_blank": skipped, "errors": errors[:200], "error_count": len(errors)}


REVIEW_SQL = """
SELECT s.note_id, s.category AS suggestion, s.theme AS suggestion_theme
FROM ml.note_suggestions s
WHERE (%(lang)s = '' OR s.lang = %(lang)s)
  AND NOT EXISTS (SELECT 1 FROM ml.note_labels x WHERE x.note_id = s.note_id)
ORDER BY s.created_at, s.note_id
LIMIT %(n)s
"""

REVIEW_COUNT_SQL = """
SELECT count(*) AS n FROM ml.note_suggestions s
WHERE (%(lang)s = '' OR s.lang = %(lang)s)
  AND NOT EXISTS (SELECT 1 FROM ml.note_labels x WHERE x.note_id = s.note_id)
"""


@app.get("/api/review", dependencies=[Depends(local_only)])
def review(lang: str = Query("", max_length=8), size: int = Query(25, ge=1, le=200)):
    """AI suggestions nobody has reviewed yet, with the note text."""
    args = {"lang": lang, "n": size}
    with pool.connection() as conn:
        pending = conn.execute(REVIEW_COUNT_SQL, args).fetchone()["n"]
        sugg = {r["note_id"]: r for r in conn.execute(REVIEW_SQL, args)}
        notes = conn.execute(NOTES_SQL, {"ids": list(sugg)}).fetchall() if sugg else []
    # Theme: the AI suggestion, or the one guessed from the app template.
    return {"pending": pending,
            "notes": [{**n, "suggestion": sugg[n["note_id"]]["suggestion"],
                       "suggestion_theme": sugg[n["note_id"]]["suggestion_theme"]
                       or n["theme_auto"]} for n in notes]}


@app.get("/api/stats")
def stats():
    with pool.connection() as conn:
        by_user = conn.execute("""
            SELECT user_name, lang, count(*) AS labels, max(updated_at) AS last
            FROM ml.note_labels GROUP BY 1, 2 ORDER BY labels DESC""").fetchall()
        by_cat = conn.execute("""
            SELECT lang, category, count(*) AS labels
            FROM ml.note_labels GROUP BY 1, 2 ORDER BY 1, 3 DESC""").fetchall()
        by_theme = conn.execute("""
            SELECT COALESCE(theme, '') AS theme, count(*) AS labels
            FROM ml.note_labels GROUP BY 1 ORDER BY 2 DESC""").fetchall()
        claude = conn.execute("""
            SELECT count(*) FILTER (WHERE claude_category IS NOT NULL) AS with_claude,
                   count(*) FILTER (WHERE claude_category = category) AS claude_right
            FROM ml.note_labels""").fetchone()
    return {"by_user": by_user, "by_category": by_cat, "by_theme": by_theme, "claude": claude}


@app.get("/api/labels.csv", dependencies=[Depends(local_only)])
def labels_csv(lang: str = "", user: str = "", since: str = "", category: str = "",
               theme: str = "", changed: bool = False):
    """Read-only, from the server only (see local_only). Optional filters (comma lists for lang/category/theme):
    ?lang=es,en&user=Rub21&since=2026-10-30&category=spam&theme=food&changed=1
    changed=1: only notes where the person changed the AI suggestion."""
    def items(v: str) -> list[str]:
        return [x.strip() for x in v.split(",") if x.strip()]

    where, params = [], {}
    for col, value in (("lang", lang), ("category", category), ("theme", theme)):
        if items(value):
            where.append(f"{col} = ANY(%({col})s)")
            params[col] = items(value)
    if user:
        where.append("user_name = %(user)s")
        params["user"] = user
    if since:
        try:
            params["since"] = dt.date.fromisoformat(since)
        except ValueError:
            raise HTTPException(400, "since debe ser una fecha AAAA-MM-DD")
        where.append("updated_at >= %(since)s")
    if changed:
        where.append("claude_category IS NOT NULL AND (category IS DISTINCT FROM claude_category"
                     " OR theme IS DISTINCT FROM claude_theme)")
    sql = """SELECT note_id, lang, category, theme, claude_category, claude_theme,
                    user_name, updated_at FROM ml.note_labels"""
    if where:
        sql += " WHERE " + " AND ".join(where)
    with pool.connection() as conn:
        labels = conn.execute(sql + " ORDER BY note_id, user_name", params).fetchall()
        ids = list({lb["note_id"] for lb in labels})
        texts = {r["note_id"]: r for r in conn.execute(NOTES_SQL, {"ids": ids})} if ids else {}
    buf = io.StringIO()
    cols = ["note_id", "lang", "country", "app", "template", "poi_type", "context", "user_text",
            "text", "category", "theme", "claude_category", "claude_theme", "user_name", "updated_at"]
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for lb in labels:
        t = texts.get(lb["note_id"], {})
        w.writerow({**lb, **{k: t.get(k) for k in
                             ("country", "app", "template", "poi_type", "context",
                              "user_text", "text")}})
    return StreamingResponse(
        iter([buf.getvalue().encode("utf-8-sig")]), media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="labels.csv"',
                 "Cache-Control": "no-store"})  # Cloudflare caches .csv for 4 h


# ---------------------------------------------------------------------------
# Map viewer (main page /): open notes with the model's prediction, from ml.map_notes
# (filled by the classifier service). personal_data notes are never there: they are in
# the ml.private_notes table, shown only to moderators when they filter by that category.
# Everyone else gets only the total count (options.private_total).
#
# - GET  /                          the viewer page (React, built into static/map)
# - GET  /api/map/options           categories, themes, languages and apps with counts
# - GET  /api/map/points?bbox=&zoom= points, or grid cells when there are too many
# - GET  /api/map/list?bbox=&sort=  notes for the sidebar + counts per category
# - GET  /api/map/note/{id}         one note with full text and people's labels
# - POST /api/map/feedback          confirm or fix the prediction (saved as a label)
# - GET  /api/map/export.geojson    filtered notes as GeoJSON (for MapRoulette)
# Filters (all optional): bbox=w,s,e,n cat=a,b theme=a,b conf=0.9 lang=es,en
#   app=StreetComplete,none days=30 q=text
# ---------------------------------------------------------------------------

MAP_POINTS_MAX = 5000
MAP_EXPORT_MAX = 50000
MAP_NO_STORE = {"Cache-Control": "no-store"}
_map_options_cache: dict = {}


def _csv_param(request: Request, name: str) -> list[str]:
    return [x.strip() for x in request.query_params.get(name, "").split(",") if x.strip()][:50]


def map_filters(request: Request, skip: tuple[str, ...] = (), bbox_required: bool = False):
    """SQL WHERE (on ml.map_notes) and params from the query string."""
    q = request.query_params
    where, params = ["true"], {}
    bbox = q.get("bbox", "")
    if bbox:
        try:
            w, s, e, n = (float(x) for x in bbox.split(","))
        except ValueError:
            raise HTTPException(400, "bbox = west,south,east,north")
        params.update(w=max(-180.0, w), s=max(-90.0, s), e=min(180.0, e), n=min(90.0, n))
        where.append("geom && ST_MakeEnvelope(%(w)s, %(s)s, %(e)s, %(n)s, 4326)")
    elif bbox_required:
        raise HTTPException(400, "bbox is required")
    for name, col in (("cat", "category"), ("theme", "theme"), ("lang", "lang")):
        if name not in skip and _csv_param(request, name):
            where.append(f"{col} = ANY(%({name})s)")
            params[name] = _csv_param(request, name)
    apps = _csv_param(request, "app")
    if apps and "app" not in skip:
        cond = ["app = ANY(%(app)s)"] + (["app IS NULL"] if "none" in apps else [])
        where.append("(" + " OR ".join(cond) + ")")
        params["app"] = apps
    try:
        conf = float(q.get("conf") or 0)
        days = int(q.get("days") or 0)
    except ValueError:
        raise HTTPException(400, "conf must be a number and days an integer")
    if conf > 0:
        where.append("category_conf >= %(conf)s")
        params["conf"] = conf
    if days > 0:
        where.append("created_at >= now() - make_interval(days => %(days)s)")
        params["days"] = days
    text = q.get("q", "").strip()[:100]
    if text:
        where.append("text ILIKE %(q)s")
        params["q"] = "%" + text.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_") + "%"
    return " AND ".join(where), params


PRIVATE = "personal_data"
MAP_ALL = "(SELECT * FROM ml.map_notes UNION ALL SELECT * FROM ml.private_notes) m"


def map_source(request: Request) -> tuple[str, bool]:
    """Table to read and whether personal_data was asked for without the right."""
    if PRIVATE not in _csv_param(request, "cat"):
        return "ml.map_notes", False
    if is_moderator(request):
        return MAP_ALL, False
    return "ml.map_notes", True


def _no_store(data) -> JSONResponse:
    return JSONResponse(jsonable_encoder(data), headers=MAP_NO_STORE)


@app.get("/map")
@app.get("/map/")
def map_page(request: Request):
    """Old address of the viewer."""
    q = request.url.query
    return RedirectResponse("/" + (f"?{q}" if q else ""))


app.mount("/assets", StaticFiles(directory="/app/static/map/assets", check_dir=False),
          name="map-assets")


@app.get("/api/map/options")
def map_options():
    """Filter choices. Cached 10 minutes: the counts change slowly."""
    if _map_options_cache.get("at", 0) > time.time() - 600:
        return _no_store(_map_options_cache["data"])
    with pool.connection() as conn:
        summary = conn.execute("""
            SELECT count(*) AS total, max(predicted_at) AS updated,
                   (array_agg(DISTINCT model_version))[1:3] AS models
            FROM ml.map_notes""").fetchone()
        langs = conn.execute("""
            SELECT lang, count(*) AS n FROM ml.map_notes WHERE lang IS NOT NULL
            GROUP BY 1 ORDER BY 2 DESC LIMIT 40""").fetchall()
        apps = conn.execute("""
            SELECT COALESCE(app, 'none') AS app, count(*) AS n FROM ml.map_notes
            GROUP BY 1 ORDER BY 2 DESC""").fetchall()
        private_total = conn.execute(
            "SELECT count(*) AS n FROM ml.private_notes").fetchone()["n"]
    all_cats = [{k: c[k] for k in ("code", "grp", "name_es", "name_en", "description",
                                   "description_en")}
                for c in categories().values()]
    # personal_data is listed (with a lock in the page); only moderators can open it.
    cats = [c for c in all_cats if c["code"] != "unsure"]
    ths = [{k: t[k] for k in ("code", "name_es", "name_en")} for t in themes().values()]
    data = {**summary, "categories": cats, "all_categories": all_cats, "themes": ths,
            "langs": langs, "apps": apps, "private_total": private_total}
    _map_options_cache.update(at=time.time(), data=data)
    return _no_store(data)


@app.get("/api/map/points")
def map_points(request: Request, zoom: float = Query(2, ge=0, le=24)):
    """GeoJSON for the map. Up to MAP_POINTS_MAX points; above that, grid cells
    (~5 per tile width) with the number of notes and the most common category."""
    where, params = map_filters(request, bbox_required=True)
    src, _ = map_source(request)
    with pool.connection() as conn:
        n = conn.execute(f"""SELECT count(*) AS n FROM (SELECT 1 FROM {src}
                             WHERE {where} LIMIT {MAP_POINTS_MAX + 1}) x""", params).fetchone()["n"]
        if n <= MAP_POINTS_MAX:
            rows = conn.execute(f"""
                SELECT note_id, ST_X(geom) AS lon, ST_Y(geom) AS lat, category, category_conf
                FROM {src} WHERE {where}""", params).fetchall()
            feats = [{"type": "Feature", "id": r["note_id"],
                      "geometry": {"type": "Point",
                                   "coordinates": [round(r["lon"], 6), round(r["lat"], 6)]},
                      "properties": {"id": r["note_id"], "c": r["category"],
                                     "p": round(r["category_conf"], 3)}} for r in rows]
            return _no_store({"type": "FeatureCollection", "mode": "points", "features": feats})
        params["cell"] = 360 / 2 ** zoom / 5
        rows = conn.execute(f"""
            WITH g AS (
              SELECT floor(ST_X(geom) / %(cell)s) AS cx, floor(ST_Y(geom) / %(cell)s) AS cy,
                     category, count(*) AS n, sum(ST_X(geom)) AS sx, sum(ST_Y(geom)) AS sy
              FROM {src} WHERE {where} GROUP BY 1, 2, 3)
            SELECT sum(n)::int AS n, sum(sx) / sum(n) AS lon, sum(sy) / sum(n) AS lat,
                   (array_agg(category ORDER BY n DESC))[1] AS c,
                   jsonb_object_agg(category, n) AS cats
            FROM g GROUP BY cx, cy""", params).fetchall()
    # n = notes in the cell, c = most common category, cats = notes per category (pie)
    feats = [{"type": "Feature",
              "geometry": {"type": "Point", "coordinates": [round(r["lon"], 5), round(r["lat"], 5)]},
              "properties": {"n": r["n"], "c": r["c"], "cats": r["cats"]}} for r in rows]
    return _no_store({"type": "FeatureCollection", "mode": "grid", "features": feats})


MAP_SORT = {"conf": "category_conf DESC, note_id DESC", "low": "category_conf ASC, note_id DESC",
            "new": "created_at DESC NULLS LAST", "old": "created_at ASC NULLS LAST"}
MAP_COLUMNS = """note_id, ST_X(geom) AS lon, ST_Y(geom) AS lat, created_at, country, lang, app,
                 template, category, category_conf, category_2, category_2_conf, theme,
                 theme_conf, source, model_version, text"""


@app.get("/api/map/list")
def map_list(request: Request, sort: str = "conf", offset: int = Query(0, ge=0, le=100_000),
             limit: int = Query(50, ge=1, le=200)):
    """Notes for the sidebar, plus counts per category with every filter but the
    category one (so the category chips can show how many each would add)."""
    where, params = map_filters(request)
    cwhere, cparams = map_filters(request, skip=("cat",))
    order = MAP_SORT.get(sort, MAP_SORT["conf"])
    src, restricted = map_source(request)
    moderator = is_moderator(request)
    with pool.connection() as conn:
        items = conn.execute(f"""SELECT {MAP_COLUMNS} FROM {src} WHERE {where}
                                 ORDER BY {order} OFFSET %(off)s LIMIT %(lim)s""",
                             {**params, "off": offset, "lim": limit}).fetchall()
        counts = {r["category"]: r["n"] for r in conn.execute(
            f"SELECT category, count(*) AS n FROM ml.map_notes WHERE {cwhere} GROUP BY 1",
            cparams)}
        if moderator:   # others only get the world total, never a count for a small area
            counts[PRIVATE] = conn.execute(
                f"SELECT count(*) AS n FROM ml.private_notes WHERE {cwhere}", cparams).fetchone()["n"]
    cats = _csv_param(request, "cat")
    total = sum(n for c, n in counts.items() if not cats or c in cats)
    return _no_store({"total": total, "by_category": counts, "items": items,
                      "restricted": restricted})


@app.get("/api/map/note/{note_id}")
def map_note(note_id: int, request: Request):
    src = MAP_ALL if is_moderator(request) else "ml.map_notes"
    with pool.connection() as conn:
        row = conn.execute(f"SELECT {MAP_COLUMNS} FROM {src} WHERE note_id = %s",
                           (note_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Note not found or not open")
        full = conn.execute(NOTES_SQL, {"ids": [note_id]}).fetchone() or {}
        labels = conn.execute("""
            SELECT user_name, category, theme, updated_at FROM ml.note_labels
            WHERE note_id = %s ORDER BY updated_at DESC""", (note_id,)).fetchall()
    user = (request.session.get("user") or {}).get("name")
    return _no_store({**row, **{k: full.get(k) for k in
                                ("poi_type", "context", "user_text", "status")},
                      "full_text": full.get("text"), "labels": labels,
                      "my_label": next((lb for lb in labels if lb["user_name"] == user), None)})


class FeedbackIn(BaseModel):
    note_id: int
    category: str
    theme: str | None = None
    user: str | None = None


@app.post("/api/map/feedback")
def map_feedback(body: FeedbackIn, request: Request):
    """Confirm (same category as the model) or fix a prediction. Saved in
    ml.note_labels with the OSM user, so it counts in the next training."""
    user = current_user(request, body.user)
    if body.category not in categories():
        raise HTTPException(400, f"Unknown category: {body.category}")
    if body.theme and body.theme not in themes():
        raise HTTPException(400, f"Unknown theme: {body.theme}")
    with pool.connection() as conn:
        public = conn.execute("SELECT 1 FROM ml.map_notes WHERE note_id = %s",
                              (body.note_id,)).fetchone()
        private = None if public or not is_moderator(request) else conn.execute(
            "SELECT * FROM ml.private_notes WHERE note_id = %s", (body.note_id,)).fetchone()
        if not public and not private:
            raise HTTPException(404, "Note not found or not open")
    out = save_labels(user, [(body.note_id, body.category, None, body.theme or None, None)])
    with pool.connection() as conn:
        if body.category == PRIVATE:   # hide it now, not at the next classifier run
            conn.execute("""
                INSERT INTO ml.private_notes SELECT * FROM ml.map_notes WHERE note_id = %s
                ON CONFLICT (note_id) DO NOTHING""", (body.note_id,))
            conn.execute("UPDATE ml.private_notes SET category = %s, source = 'person' "
                         "WHERE note_id = %s", (PRIVATE, body.note_id))
            conn.execute("DELETE FROM ml.map_notes WHERE note_id = %s", (body.note_id,))
        elif private:                  # a moderator says it has no personal data: show it
            conn.execute("DELETE FROM ml.private_notes WHERE note_id = %s", (body.note_id,))
            conn.execute("""
                INSERT INTO ml.map_notes (note_id, geom, created_at, country, lang, app, template,
                    category, category_conf, theme, source, model_version, predicted_at, text)
                VALUES (%(note_id)s, %(geom)s, %(created_at)s, %(country)s, %(lang)s, %(app)s,
                    %(template)s, %(cat)s, 1, %(th)s, 'person', %(model_version)s,
                    %(predicted_at)s, %(text)s)
                ON CONFLICT (note_id) DO NOTHING""",
                {**private, "cat": body.category, "th": body.theme or None})
        else:                          # a person's label wins over the model on the map
            conn.execute("""
                UPDATE ml.map_notes SET category = %s, category_conf = 1,
                    category_2 = NULL, category_2_conf = NULL,
                    theme = COALESCE(%s, theme), source = 'person'
                WHERE note_id = %s""", (body.category, body.theme or None, body.note_id))
    return out


@app.get("/api/map/export.geojson")
def map_export(request: Request, download: bool = False):
    """Filtered notes as GeoJSON, highest confidence first, up to MAP_EXPORT_MAX.
    MapRoulette can read this URL directly (one task per note)."""
    where, params = map_filters(request)
    with pool.connection() as conn:
        rows = conn.execute(f"""SELECT {MAP_COLUMNS} FROM ml.map_notes WHERE {where}
                                ORDER BY category_conf DESC LIMIT {MAP_EXPORT_MAX}""",
                            params).fetchall()
    names = {c["code"]: c["name_en"] for c in categories().values()}
    feats = [{"type": "Feature", "id": r["note_id"],
              "geometry": {"type": "Point", "coordinates": [round(r["lon"], 6), round(r["lat"], 6)]},
              "properties": {"id": str(r["note_id"]),
                             "note_url": f"{OSM_URL}/note/{r['note_id']}",
                             "category": r["category"], "category_name": names.get(r["category"]),
                             "confidence": round(r["category_conf"], 3), "theme": r["theme"],
                             "lang": r["lang"], "app": r["app"],
                             "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                             "text": r["text"], "model_version": r["model_version"]}}
             for r in rows]
    headers = dict(MAP_NO_STORE)
    if download:
        headers["Content-Disposition"] = 'attachment; filename="osm_notes.geojson"'
    return JSONResponse({"type": "FeatureCollection", "features": feats},
                        media_type="application/geo+json", headers=headers)
