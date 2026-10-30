"""Extract OSM notes (whole planet), detect language and write a sample for labeling.

Steps:
  1. Create/upgrade the `ml` schema (sql/ml_schema.sql).
  2. For every note: split the app template from the user's words
     (templates.py), detect the language of the user's words, guess the
     theme from the POI type, and store it all in ml.note_lang.
  3. Write, in OUTPUT_DIR:
     notes_<date>.csv.gz   all notes in the selected languages
     sample_<date>.csv     stratified sample of SAMPLE_SIZE notes in the
                           labeling CSV format (empty `category`)

Only the first comment (the one that opened the note) is used as text.
Later comments are left out because they often contain the answer
("fixed", "closed, it does not exist") and would leak the label.

Modes:
  extract.py            full run (recomputes every note)
  extract.py --watch    keep ml.note_lang up to date with new notes
"""

import datetime as dt
import os
import re
import sys
import time

import fasttext
import numpy as np
import pandas as pd
import psycopg
import psycopg.sql

from templates import split_note

# App that created the note, detected from tags/signatures in the text.
APP_PATTERNS = [
    ("StreetComplete", r"streetcomplete"),
    ("Organic Maps", r"organic ?maps|#organicmaps|omaps application"),
    ("CoMaps", r"comaps"),
    ("MAPS.ME", r"maps\.?me"),
    ("OnOSM", r"onosm"),
    ("Every Door", r"every ?door"),
    ("OsmAnd", r"osmand"),
    ("Go Map!!", r"go ?map"),
    ("Vespucci", r"vespucci"),
]

SAMPLE_SIZE = int(os.environ.get("SAMPLE_SIZE", "1000"))
LANGS = [x.strip() for x in os.environ.get("LANGS", "es,en,und").split(",") if x.strip()]
MIN_LANG_PROB = float(os.environ.get("MIN_LANG_PROB", "0.5"))
CHUNK_SIZE = int(os.environ.get("CHUNK_SIZE", "100000"))
SEED = int(os.environ.get("SEED", "42"))
OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "/output")
SCHEMA_SQL = os.environ.get("SCHEMA_SQL", "/sql/ml_schema.sql")
LABELER_DB_PASSWORD = os.environ.get("LABELER_DB_PASSWORD", "")
WATCH_INTERVAL = int(os.environ.get("WATCH_INTERVAL", "300"))

QUERY = """
SELECT n.note_id,
       COALESCE(c.country_name_en, c.country_name, 'Unknown') AS country,
       n.latitude   AS lat,
       n.longitude  AS lon,
       n.created_at,
       n.status,
       n.closed_at,
       t.body       AS text
FROM notes n
JOIN note_comments nc
  ON nc.note_id = n.note_id AND nc.event = 'opened'
JOIN note_comments_text t
  ON t.note_id = nc.note_id AND t.sequence_action = nc.sequence_action
LEFT JOIN countries c
  ON c.country_id = n.id_country
"""

# Notes that still have no language (new notes since the last run).
NEW_NOTES_QUERY = QUERY + """
WHERE NOT EXISTS (SELECT 1 FROM ml.note_lang l WHERE l.note_id = n.note_id)
"""

COLUMNS = ["note_id", "country", "lat", "lon", "created_at", "status", "closed_at", "text"]
# Columns stored in ml.note_lang.
LANG_COLUMNS = ["note_id", "lang", "lang_prob", "app", "template", "user_text",
                "poi_type", "theme_auto", "rule_category"]
OUT_COLUMNS = ["note_id", "country", "lang", "lang_prob", "app", "template", "poi_type",
               "theme_auto", "rule_category", "lat", "lon", "created_at", "status",
               "closed_at", "user_text", "text"]
# Same format as the labeling page export/import.
LABEL_CSV_COLUMNS = ["note_id", "lang", "country", "app", "template", "user_text", "text",
                     "category", "theme", "claude_category", "claude_theme"]
# Only these stay in memory for the sample; texts are read back at the end.
SAMPLE_KEYS = ["note_id", "lang", "app"]


def detect_app(text: str) -> str:
    low = text.lower()
    for name, pattern in APP_PATTERNS:
        if re.search(pattern, low):
            return name
    return "other"


def clean_for_lang(text: str) -> str:
    """Remove URLs and hashtags before language detection."""
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"#\S+", " ", text)
    return " ".join(text.split())


def add_features(df: pd.DataFrame, model) -> pd.DataFrame:
    df["text"] = df["text"].fillna("").str.replace("\r", " ").str.strip()
    df["app"] = df["text"].map(detect_app)
    parsed = pd.DataFrame([split_note(t) for t in df["text"]], index=df.index)
    df["template"] = parsed["template"]
    df["user_text"] = parsed["user_text"]
    df["poi_type"] = parsed["poi_type"]
    df["theme_auto"] = parsed["theme"]
    df["rule_category"] = parsed["rule_category"]
    # Language of the user's words only, never of the app template.
    cleaned = df["user_text"].map(clean_for_lang)
    labels, probs = model.predict(cleaned.str.replace("\n", " ").tolist(), k=1)
    df["lang"] = [lab[0].replace("__label__", "") for lab in labels]
    df["lang_prob"] = [round(float(p[0]), 3) for p in probs]
    df.loc[(df["lang_prob"] < MIN_LANG_PROB) | (cleaned.str.len() < 3), "lang"] = "und"
    return df


def lang_rows(df: pd.DataFrame):
    """Rows for ml.note_lang. user_text only for notes with a template."""
    for r in df[LANG_COLUMNS].itertuples(index=False):
        has_template = isinstance(r.template, str)
        yield (int(r.note_id), r.lang, float(r.lang_prob), r.app,
               r.template if has_template else None,
               r.user_text if has_template else None,
               r.poi_type if isinstance(r.poi_type, str) else None,
               r.theme_auto if isinstance(r.theme_auto, str) else None,
               r.rule_category if isinstance(r.rule_category, str) else None)


def stratified_sample(df: pd.DataFrame, n: int, keys: list[str]) -> pd.DataFrame:
    """Sample with sqrt-proportional allocation so small groups still show up."""
    sizes = df.groupby(keys).size()
    weights = np.sqrt(sizes)
    alloc = np.minimum(np.maximum(1, np.round(n * weights / weights.sum())).astype(int), sizes)
    parts = [g.sample(n=int(alloc[k]), random_state=SEED) for k, g in df.groupby(keys)]
    out = pd.concat(parts)
    # Adjust to exactly n rows.
    if len(out) > n:
        out = out.sample(n=n, random_state=SEED)
    elif len(out) < n:
        rest = df.drop(out.index)
        out = pd.concat([out, rest.sample(n=min(n - len(out), len(rest)), random_state=SEED)])
    return out.sample(frac=1, random_state=SEED)


def setup_schema() -> None:
    with psycopg.connect(autocommit=True) as conn:
        with open(SCHEMA_SQL, encoding="utf-8") as f:
            conn.execute(f.read())
        if LABELER_DB_PASSWORD:
            conn.execute(psycopg.sql.SQL("ALTER ROLE labeler PASSWORD {}").format(
                psycopg.sql.Literal(LABELER_DB_PASSWORD)))
    print("Schema ml ready")


def write_lang(conn, df: pd.DataFrame) -> None:
    """Upsert language rows. Safe to repeat."""
    with conn.cursor() as cur:
        cur.executemany("""
            INSERT INTO ml.note_lang (note_id, lang, lang_prob, app, template, user_text,
                                      poi_type, theme_auto, rule_category)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (note_id) DO UPDATE
            SET lang = EXCLUDED.lang, lang_prob = EXCLUDED.lang_prob, app = EXCLUDED.app,
                template = EXCLUDED.template, user_text = EXCLUDED.user_text,
                poi_type = EXCLUDED.poi_type, theme_auto = EXCLUDED.theme_auto,
                rule_category = EXCLUDED.rule_category, detected_at = now()""",
            list(lang_rows(df)))


def update_new_notes(model) -> int:
    """Process only notes not yet in ml.note_lang.
    Commits after each chunk, so a crash keeps the progress."""
    done = 0
    with psycopg.connect() as conn:
        while True:
            rows = conn.execute(NEW_NOTES_QUERY + f" LIMIT {CHUNK_SIZE}").fetchall()
            if not rows:
                break
            df = add_features(pd.DataFrame(rows, columns=COLUMNS), model)
            write_lang(conn, df)
            conn.commit()
            done += len(df)
            print(f"  language set for {done:,} new notes", flush=True)
    return done


def watch() -> None:
    """Keep ml.note_lang up to date with the notes the daemon adds."""
    setup_schema()
    model = fasttext.load_model("/models/lid.176.ftz")
    print(f"Watching for new notes every {WATCH_INTERVAL}s", flush=True)
    while True:
        try:
            n = update_new_notes(model)
            print(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} new notes: {n:,}", flush=True)
        except psycopg.Error as e:
            print(f"DB error, retrying later: {e}", flush=True)
        time.sleep(WATCH_INTERVAL)


def read_sample_texts(ids: list[int]) -> pd.DataFrame:
    """Read the full rows for the sampled notes."""
    with psycopg.connect() as conn:
        rows = conn.execute("""
            SELECT l.note_id, l.lang, l.app, l.template, l.poi_type, l.theme_auto,
                   COALESCE(c.country_name_en, c.country_name, 'Unknown') AS country,
                   t.body AS text, COALESCE(l.user_text, t.body) AS user_text
            FROM unnest(%s::int[]) AS p(note_id)
            JOIN ml.note_lang l ON l.note_id = p.note_id
            JOIN notes n ON n.note_id = p.note_id
            JOIN note_comments nc ON nc.note_id = p.note_id AND nc.event = 'opened'
            JOIN note_comments_text t
              ON t.note_id = nc.note_id AND t.sequence_action = nc.sequence_action
            LEFT JOIN countries c ON c.country_id = n.id_country""", (ids,)).fetchall()
    cols = ["note_id", "lang", "app", "template", "poi_type", "theme_auto", "country",
            "text", "user_text"]
    return pd.DataFrame(rows, columns=cols)


def main() -> None:
    print(f"Languages: {LANGS} | sample size: {SAMPLE_SIZE}")
    setup_schema()
    model = fasttext.load_model("/models/lid.176.ftz")
    stamp = dt.date.today().strftime("%Y%m%d")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    full_path = f"{OUTPUT_DIR}/notes_{stamp}.csv.gz"

    total = 0
    lang_counts: dict[str, int] = {}
    template_counts: dict[str, int] = {}
    kept = []
    # Server-side cursor: read in chunks so the whole planet does not sit in memory.
    # A second connection writes a new table, ml.note_lang_new, which replaces
    # ml.note_lang at the end in one short transaction. The labeler keeps
    # working during the run, and if the run fails the old data stays.
    with psycopg.connect() as conn, psycopg.connect() as wconn, \
            conn.cursor(name="notes_cursor") as cur:
        wconn.execute("DROP TABLE IF EXISTS ml.note_lang_new")
        wconn.execute("CREATE TABLE ml.note_lang_new (LIKE ml.note_lang INCLUDING DEFAULTS)")
        cur.itersize = CHUNK_SIZE
        cur.execute(QUERY)
        first = True
        while rows := cur.fetchmany(CHUNK_SIZE):
            df = add_features(pd.DataFrame(rows, columns=COLUMNS), model)
            total += len(df)
            with wconn.cursor().copy(
                    "COPY ml.note_lang_new (" + ", ".join(LANG_COLUMNS) + ") FROM STDIN") as copy:
                for row in lang_rows(df):
                    copy.write_row(row)
            for lang, count in df["lang"].value_counts().items():
                lang_counts[lang] = lang_counts.get(lang, 0) + int(count)
            for tpl, count in df["template"].fillna("none").value_counts().items():
                template_counts[tpl] = template_counts.get(tpl, 0) + int(count)
            df = df[df["lang"].isin(LANGS)]
            df[OUT_COLUMNS].to_csv(full_path, mode="w" if first else "a", header=first,
                                   index=False, compression="gzip")
            first = False
            # Notes labeled by rule are not part of the sample.
            kept.append(df[df["rule_category"].isna()][SAMPLE_KEYS])
            print(f"  read {total:,} notes, kept {sum(len(k) for k in kept):,}", flush=True)
        print("Building indexes and swapping ml.note_lang", flush=True)
        wconn.execute("ALTER TABLE ml.note_lang_new ADD CONSTRAINT note_lang_new_pkey "
                      "PRIMARY KEY (note_id)")
        wconn.execute("CREATE INDEX note_lang_new_lang_rnd ON ml.note_lang_new (lang, rnd)")
        wconn.commit()
        # Short swap. Notes added meanwhile by lang-updater are picked up again
        # on its next cycle.
        wconn.execute("SET lock_timeout = '60s'")
        wconn.execute("DROP TABLE ml.note_lang")
        wconn.execute("ALTER TABLE ml.note_lang_new RENAME TO note_lang")
        wconn.execute("ALTER INDEX ml.note_lang_new_pkey RENAME TO note_lang_pkey")
        wconn.execute("ALTER INDEX ml.note_lang_new_lang_rnd RENAME TO note_lang_lang_rnd")
        wconn.execute("GRANT SELECT ON ml.note_lang TO labeler")
        wconn.commit()
    print(f"ml.note_lang: {total:,} notes")

    if total == 0:
        raise SystemExit("No notes found. Has the ingestion finished?")

    print("Notes by language (top 10):")
    for lang, count in sorted(lang_counts.items(), key=lambda x: -x[1])[:10]:
        print(f"  {lang:5} {count:>10,}")
    print("Notes by app template:")
    for tpl, count in sorted(template_counts.items(), key=lambda x: -x[1]):
        print(f"  {tpl:15} {count:>10,}")

    keys = pd.concat(kept, ignore_index=True)
    print(f"Notes in {LANGS}: {len(keys):,} -> {full_path}")

    picked = stratified_sample(keys, SAMPLE_SIZE, ["lang", "app"])
    sample = read_sample_texts([int(x) for x in picked["note_id"]])
    sample = sample.set_index("note_id").loc[[int(x) for x in picked["note_id"]]].reset_index()
    sample = sample.assign(category="", theme="", claude_category="",
                           claude_theme=sample["theme_auto"].fillna(""))
    sample_path = f"{OUTPUT_DIR}/sample_{stamp}.csv"
    sample[LABEL_CSV_COLUMNS].to_csv(sample_path, index=False, encoding="utf-8-sig")
    print(f"Wrote {sample_path} ({len(sample)} rows)")
    print("Sample by lang/app:")
    print(sample.groupby(["lang", "app"]).size().to_string())


if __name__ == "__main__":
    if "--watch" in sys.argv:
        watch()
    else:
        main()
