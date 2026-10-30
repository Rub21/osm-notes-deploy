"""Labeling API for OSM notes.

- GET  /api/categories            fixed label set (ml.categories)
- GET  /api/langs                 languages with note and label counts
- GET  /api/batch?lang=&size=     random unlabeled notes in a language (JSON)
- GET  /api/batch.csv?lang=&size= same batch as CSV to label offline
- POST /api/labels                save labels from the page
- POST /api/import                upload a CSV: `category` = reviewed label,
                                  only `claude_category` = suggestion to review
- GET  /api/review?lang=&size=    AI suggestions not reviewed yet
- GET  /api/stats                 labels per user/language/category
- GET  /api/labels.csv            all labels with note text (for training)
- GET  /api/me                    who is logged in
- GET  /auth/login, /auth/callback, /auth/logout   OSM OAuth 2.0 login

Login: when OSM_CLIENT_ID is set, writes need an OSM login and the label
is saved with the OSM username. Without it (local dev) the page sends a
free-text user name.

CSV format (export and import):
  note_id,lang,country,app,text,category,claude_category
Only note_id and category are required on import. Blank category = skipped.
"""

import base64
import csv
import hashlib
import io
import os
import random
import re
import secrets
import time
from urllib.parse import urlencode

import httpx

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
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
MAX_BATCH = 1000
CSV_COLUMNS = ["note_id", "lang", "country", "app", "text", "category", "claude_category"]
USER_RE = re.compile(r"^[\w .@-]{2,40}$")

# Uses PGHOST, PGUSER (labeler), PGPASSWORD, PGDATABASE.
pool = ConnectionPool(kwargs={"row_factory": dict_row}, min_size=1, max_size=5, open=True)
app = FastAPI(title="OSM notes labeler")
app.add_middleware(
    SessionMiddleware, secret_key=SESSION_SECRET, session_cookie="labeler_session",
    max_age=30 * 24 * 3600, same_site="lax", https_only=PUBLIC_URL.startswith("https"))

NOTES_SQL = """
SELECT nl.note_id, nl.lang, nl.app,
       COALESCE(c.country_name_en, c.country_name, 'Unknown') AS country,
       n.created_at, n.status, n.latitude AS lat, n.longitude AS lon,
       t.body AS text
FROM unnest(%(ids)s::int[]) WITH ORDINALITY AS p(note_id, ord)
JOIN ml.note_lang nl ON nl.note_id = p.note_id
JOIN notes n ON n.note_id = p.note_id
JOIN note_comments nc ON nc.note_id = p.note_id AND nc.event = 'opened'
JOIN note_comments_text t
  ON t.note_id = nc.note_id AND t.sequence_action = nc.sequence_action
LEFT JOIN countries c ON c.country_id = n.id_country
ORDER BY p.ord
"""

PICK_SQL = """
SELECT l.note_id FROM ml.note_lang l
WHERE l.lang = %(lang)s AND l.rnd {op} %(r)s
  AND NOT EXISTS (SELECT 1 FROM ml.note_labels x WHERE x.note_id = l.note_id)
ORDER BY l.rnd {order}
LIMIT %(n)s
"""


def current_user(request: Request, typed: str | None = None) -> str:
    """OSM login when configured; otherwise the name typed in the page."""
    if OSM_LOGIN:
        user = request.session.get("user")
        if not user:
            raise HTTPException(401, "Entra con tu cuenta de OSM")
        return user["name"]
    typed = (typed or "").strip()
    if not USER_RE.match(typed):
        raise HTTPException(400, "Nombre de usuario inválido (2-40 letras, números, . @ -)")
    return typed


@app.get("/api/me")
def me(request: Request):
    return {"osm_login": OSM_LOGIN, "osm_url": OSM_URL, "user": request.session.get("user")}


@app.get("/auth/login")
def login(request: Request):
    if not OSM_LOGIN:
        raise HTTPException(404, "Login con OSM no configurado")
    # PKCE + state, kept in the signed session cookie.
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    request.session.update(oauth_state=state, oauth_verifier=verifier)
    query = urlencode({
        "response_type": "code", "client_id": OSM_CLIENT_ID,
        "redirect_uri": f"{PUBLIC_URL}/auth/callback", "scope": "read_prefs",
        "state": state, "code_challenge": challenge, "code_challenge_method": "S256"})
    return RedirectResponse(f"{OSM_URL}/oauth2/authorize?{query}")


@app.get("/auth/callback")
def callback(request: Request, code: str = "", state: str = ""):
    if not OSM_LOGIN:
        raise HTTPException(404, "Login con OSM no configurado")
    expected = request.session.pop("oauth_state", None)
    verifier = request.session.pop("oauth_verifier", None)
    if not code or not expected or state != expected:
        raise HTTPException(400, "Login inválido o vencido. Vuelve a intentar.")
    with httpx.Client(timeout=15) as http:
        tok = http.post(f"{OSM_URL}/oauth2/token", data={
            "grant_type": "authorization_code", "code": code,
            "redirect_uri": f"{PUBLIC_URL}/auth/callback",
            "client_id": OSM_CLIENT_ID, "client_secret": OSM_CLIENT_SECRET,
            "code_verifier": verifier})
        if tok.status_code != 200:
            raise HTTPException(502, "OSM rechazó el login")
        details = http.get(f"{OSM_API_URL}/api/0.6/user/details.json", headers={
            "Authorization": f"Bearer {tok.json()['access_token']}"})
        if details.status_code != 200:
            raise HTTPException(502, "No se pudo leer el usuario de OSM")
    u = details.json()["user"]
    # Only the name and id are kept; the OSM access token is dropped.
    request.session["user"] = {"id": u["id"], "name": u["display_name"],
                               "avatar": (u.get("img") or {}).get("href")}
    return RedirectResponse("/")


@app.get("/auth/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/")


def categories() -> dict[str, dict]:
    with pool.connection() as conn:
        rows = conn.execute(
            "SELECT code, grp, action, name_es, name_en, example, description, sort "
            "FROM ml.categories WHERE active ORDER BY sort").fetchall()
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
    return FileResponse("/app/static/index.html")


@app.get("/privacy")
def privacy():
    return FileResponse("/app/static/privacy.html")


@app.get("/api/categories")
def get_categories():
    return list(categories().values())


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


@app.get("/api/batch.csv")
def get_batch_csv(lang: str = Query(..., max_length=8),
                  size: int = Query(200, ge=1, le=MAX_BATCH)):
    rows = pick_batch(lang, size)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_COLUMNS, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({**r, "category": "", "claude_category": ""})
    name = f"notes_{lang}_{time.strftime('%Y%m%d_%H%M')}.csv"
    # utf-8-sig so Excel opens accents correctly.
    return StreamingResponse(
        iter([buf.getvalue().encode("utf-8-sig")]), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{name}"'})


class Label(BaseModel):
    note_id: int
    category: str
    claude_category: str | None = None


class LabelsIn(BaseModel):
    user: str | None = None
    labels: list[Label]


def save_suggestions(user: str, items: list[tuple[int, str]]) -> int:
    """Store AI suggestions to review later. A new upload replaces the old one."""
    if not items:
        return 0
    with pool.connection() as conn:
        langs = {r["note_id"]: r["lang"] for r in conn.execute(
            "SELECT note_id, lang FROM ml.note_lang WHERE note_id = ANY(%s)",
            ([i[0] for i in items],))}
        with conn.cursor() as cur:
            cur.executemany("""
                INSERT INTO ml.note_suggestions (note_id, lang, category, imported_by)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (note_id) DO UPDATE
                SET category = EXCLUDED.category, imported_by = EXCLUDED.imported_by,
                    created_at = now()""",
                [(nid, langs.get(nid), cat, user) for nid, cat in items])
    return len(items)


def save_labels(user: str, items: list[tuple[int, str, str | None]]) -> dict:
    """Upsert (note_id, user). Returns counts. Items already validated."""
    if not items:
        return {"saved": 0}
    with pool.connection() as conn:
        langs = {r["note_id"]: r["lang"] for r in conn.execute(
            "SELECT note_id, lang FROM ml.note_lang WHERE note_id = ANY(%s)",
            ([i[0] for i in items],))}
        with conn.cursor() as cur:
            cur.executemany("""
                INSERT INTO ml.note_labels (note_id, lang, category, claude_category, user_name)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (note_id, user_name) DO UPDATE
                SET category = EXCLUDED.category,
                    claude_category = COALESCE(EXCLUDED.claude_category,
                                               ml.note_labels.claude_category),
                    updated_at = now()""",
                [(nid, langs.get(nid), cat, cc, user) for nid, cat, cc in items])
    return {"saved": len(items)}


@app.post("/api/labels")
def post_labels(body: LabelsIn, request: Request):
    user = current_user(request, body.user)
    cats = categories()
    items = []
    for lb in body.labels:
        if lb.category not in cats:
            raise HTTPException(400, f"Categoría desconocida: {lb.category}")
        items.append((lb.note_id, lb.category, lb.claude_category or None))
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
        raise HTTPException(400, "El CSV necesita una columna note_id")
    return [{(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
            for row in reader]


@app.post("/api/import")
async def import_csv(request: Request, file: UploadFile = File(...),
                     user: str | None = Form(None)):
    user = current_user(request, user)
    raw = await file.read()
    if len(raw) > 20_000_000:
        raise HTTPException(400, "Archivo muy grande (máx. 20 MB)")
    rows = read_csv(raw)
    cats = categories()
    # Also accept the category name in Spanish/English, not only the code.
    by_name = {c["name_es"].lower(): k for k, c in cats.items()}
    by_name.update({c["name_en"].lower(): k for k, c in cats.items()})

    ids = [int(r["note_id"]) for r in rows if r.get("note_id", "").isdigit()]
    with pool.connection() as conn:
        known = {r["note_id"] for r in conn.execute(
            "SELECT note_id FROM ml.note_lang WHERE note_id = ANY(%s)", (ids,))}

    def code(value: str) -> str | None:
        value = value.lower()
        return (value if value in cats else by_name.get(value, value)) if value else None

    items, suggestions, errors, skipped = [], [], [], 0
    for line, r in enumerate(rows, start=2):  # line 1 is the header
        nid = r.get("note_id", "")
        cat, cc = code(r.get("category", "")), code(r.get("claude_category", ""))
        if not cat and not cc:
            skipped += 1
            continue
        if not nid.isdigit():
            errors.append({"line": line, "note_id": nid, "error": "note_id inválido"})
        elif int(nid) not in known:
            errors.append({"line": line, "note_id": nid, "error": "nota no existe"})
        elif cat and cat not in cats:
            errors.append({"line": line, "note_id": nid, "error": f"categoría desconocida: {cat}"})
        elif cc and cc not in cats:
            errors.append({"line": line, "note_id": nid,
                           "error": f"claude_category desconocida: {cc}"})
        elif cat:
            items.append((int(nid), cat, cc))
        else:  # only the AI suggestion: goes to the review queue
            suggestions.append((int(nid), cc))
    result = save_labels(user, items)
    suggested = save_suggestions(user, suggestions)
    return {"rows": len(rows), "saved": result["saved"], "suggested": suggested,
            "skipped_blank": skipped, "errors": errors[:200], "error_count": len(errors)}


REVIEW_SQL = """
SELECT s.note_id, s.category AS suggestion
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


@app.get("/api/review")
def review(lang: str = Query("", max_length=8), size: int = Query(25, ge=1, le=200)):
    """AI suggestions nobody has reviewed yet, with the note text."""
    args = {"lang": lang, "n": size}
    with pool.connection() as conn:
        pending = conn.execute(REVIEW_COUNT_SQL, args).fetchone()["n"]
        sugg = {r["note_id"]: r["suggestion"] for r in conn.execute(REVIEW_SQL, args)}
        notes = conn.execute(NOTES_SQL, {"ids": list(sugg)}).fetchall() if sugg else []
    return {"pending": pending,
            "notes": [{**n, "suggestion": sugg[n["note_id"]]} for n in notes]}


@app.get("/api/stats")
def stats():
    with pool.connection() as conn:
        by_user = conn.execute("""
            SELECT user_name, lang, count(*) AS labels, max(updated_at) AS last
            FROM ml.note_labels GROUP BY 1, 2 ORDER BY labels DESC""").fetchall()
        by_cat = conn.execute("""
            SELECT lang, category, count(*) AS labels
            FROM ml.note_labels GROUP BY 1, 2 ORDER BY 1, 3 DESC""").fetchall()
        claude = conn.execute("""
            SELECT count(*) FILTER (WHERE claude_category IS NOT NULL) AS with_claude,
                   count(*) FILTER (WHERE claude_category = category) AS claude_right
            FROM ml.note_labels""").fetchone()
    return {"by_user": by_user, "by_category": by_cat, "claude": claude}


@app.get("/api/labels.csv")
def labels_csv(request: Request):
    if OSM_LOGIN:
        current_user(request)
    with pool.connection() as conn:
        ids = [r["note_id"] for r in conn.execute(
            "SELECT DISTINCT note_id FROM ml.note_labels")]
        texts = {r["note_id"]: r for r in conn.execute(NOTES_SQL, {"ids": ids})} if ids else {}
        labels = conn.execute("""
            SELECT note_id, lang, category, claude_category, user_name, updated_at
            FROM ml.note_labels ORDER BY note_id, user_name""").fetchall()
    buf = io.StringIO()
    cols = ["note_id", "lang", "country", "app", "text", "category", "claude_category",
            "user_name", "updated_at"]
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for lb in labels:
        t = texts.get(lb["note_id"], {})
        w.writerow({**lb, "country": t.get("country"), "app": t.get("app"), "text": t.get("text")})
    return StreamingResponse(
        iter([buf.getvalue().encode("utf-8-sig")]), media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="labels.csv"'})
