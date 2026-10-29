"""Labeling API for OSM notes.

- GET  /api/categories            fixed label set (ml.categories)
- GET  /api/langs                 languages with note and label counts
- GET  /api/batch?lang=&size=     random unlabeled notes in a language (JSON)
- GET  /api/batch.csv?lang=&size= same batch as CSV to label offline
- POST /api/labels                save labels from the page
- POST /api/import                upload a labeled CSV
- GET  /api/stats                 labels per user/language/category
- GET  /api/labels.csv            all labels with note text (for training)

CSV format (export and import):
  note_id,lang,country,app,text,category,claude_category
Only note_id and category are required on import. Blank category = skipped.
"""

import csv
import io
import os
import random
import re
import time

from fastapi import FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from pydantic import BaseModel

LABELER_TOKEN = os.environ.get("LABELER_TOKEN", "")
MAX_BATCH = 1000
CSV_COLUMNS = ["note_id", "lang", "country", "app", "text", "category", "claude_category"]
USER_RE = re.compile(r"^[\w .@-]{2,40}$")

# Uses PGHOST, PGUSER (labeler), PGPASSWORD, PGDATABASE.
pool = ConnectionPool(kwargs={"row_factory": dict_row}, min_size=1, max_size=5, open=True)
app = FastAPI(title="OSM notes labeler")

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


def check_user(user: str) -> str:
    user = (user or "").strip()
    if not USER_RE.match(user):
        raise HTTPException(400, "Nombre de usuario inválido (2-40 letras, números, . @ -)")
    return user


def check_token(token: str | None) -> None:
    if LABELER_TOKEN and token != LABELER_TOKEN:
        raise HTTPException(401, "Token inválido")


def categories() -> dict[str, dict]:
    with pool.connection() as conn:
        rows = conn.execute(
            "SELECT code, grp, action, name_es, name_en, example, sort "
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
    user: str
    labels: list[Label]


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
def post_labels(body: LabelsIn, x_token: str | None = Header(None)):
    check_token(x_token)
    user = check_user(body.user)
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
async def import_csv(file: UploadFile = File(...), user: str = Form(...),
                     x_token: str | None = Header(None)):
    check_token(x_token)
    user = check_user(user)
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

    items, errors, skipped = [], [], 0
    for line, r in enumerate(rows, start=2):  # line 1 is the header
        nid, cat = r.get("note_id", ""), r.get("category", "").lower()
        if not cat:
            skipped += 1
            continue
        cat = cat if cat in cats else by_name.get(cat, cat)
        cc = r.get("claude_category", "").lower() or None
        cc = (cc if cc in cats else by_name.get(cc, cc)) if cc else None
        if not nid.isdigit():
            errors.append({"line": line, "note_id": nid, "error": "note_id inválido"})
        elif int(nid) not in known:
            errors.append({"line": line, "note_id": nid, "error": "nota no existe"})
        elif cat not in cats:
            errors.append({"line": line, "note_id": nid, "error": f"categoría desconocida: {cat}"})
        else:
            items.append((int(nid), cat, cc))
    result = save_labels(user, items)
    return {"rows": len(rows), "saved": result["saved"], "skipped_blank": skipped,
            "errors": errors[:200], "error_count": len(errors)}


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
def labels_csv(x_token: str | None = Header(None), token: str | None = None):
    check_token(x_token or token)
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
