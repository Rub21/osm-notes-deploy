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
  extract.py            process what is missing: new notes and notes done with
                        an older EXTRACT_VERSION. Saves progress every range of
                        note ids, so after a crash it continues where it stopped.
  extract.py --force    recompute every note from scratch
  extract.py --watch    keep ml.note_lang up to date with new notes (lang-updater)
  extract.py --export   also write all notes in LANGS to notes_<date>.csv.gz
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
# Note ids per step. Each step is committed, so it is the unit of progress.
RANGE_SIZE = int(os.environ.get("RANGE_SIZE", "100000"))
# Bump when templates.py or the language logic changes: older rows are redone.
EXTRACT_VERSION = 2

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


UPSERT_SQL = """
INSERT INTO ml.note_lang (note_id, lang, lang_prob, app, template, user_text,
                          poi_type, theme_auto, rule_category, extract_version)
SELECT DISTINCT ON (note_id) note_id, lang, lang_prob, app, template, user_text,
       poi_type, theme_auto, rule_category, %(v)s
FROM stage_note_lang ORDER BY note_id
ON CONFLICT (note_id) DO UPDATE
SET lang = EXCLUDED.lang, lang_prob = EXCLUDED.lang_prob, app = EXCLUDED.app,
    template = EXCLUDED.template, user_text = EXCLUDED.user_text,
    poi_type = EXCLUDED.poi_type, theme_auto = EXCLUDED.theme_auto,
    rule_category = EXCLUDED.rule_category, extract_version = EXCLUDED.extract_version,
    detected_at = now()
"""


def write_lang(conn, df: pd.DataFrame) -> None:
    """Upsert rows through a temp table (COPY is fast). DISTINCT ON drops the
    duplicate note texts the ingestion sometimes stores. Safe to repeat."""
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS stage_note_lang "
                 "(LIKE ml.note_lang INCLUDING DEFAULTS) ON COMMIT DELETE ROWS")
    with conn.cursor().copy(
            "COPY stage_note_lang (" + ", ".join(LANG_COLUMNS) + ") FROM STDIN") as copy:
        for row in lang_rows(df):
            copy.write_row(row)
    conn.execute(UPSERT_SQL, {"v": EXTRACT_VERSION})


def update_new_notes(model) -> int:
    """Process only notes not yet in ml.note_lang (used by --watch)."""
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


def process_all(model, force: bool) -> int:
    """Walk note ids in ranges. Without force, only notes missing in
    ml.note_lang or done with an older EXTRACT_VERSION are processed, so a
    second run skips finished ranges and continues where the last one stopped."""
    pending = "" if force else """
      AND NOT EXISTS (SELECT 1 FROM ml.note_lang l
                      WHERE l.note_id = n.note_id AND l.extract_version >= %(v)s)"""
    sql = QUERY + " WHERE n.note_id >= %(lo)s AND n.note_id < %(hi)s" + pending
    done = 0
    with psycopg.connect() as conn:
        max_id = conn.execute("SELECT COALESCE(max(note_id), 0) FROM notes").fetchone()[0]
        for lo in range(0, max_id + 1, RANGE_SIZE):
            hi = lo + RANGE_SIZE
            rows = conn.execute(sql, {"lo": lo, "hi": hi, "v": EXTRACT_VERSION}).fetchall()
            if rows:
                df = add_features(pd.DataFrame(rows, columns=COLUMNS), model)
                write_lang(conn, df)
                done += len(df)
            conn.commit()
            pct = min(100, round(100 * hi / max(max_id, 1)))
            print(f"  ids {lo:>9,}-{hi - 1:<9,} {len(rows):>7,} processed "
                  f"| total {done:,} | {pct}%", flush=True)
    return done


def print_summary() -> None:
    with psycopg.connect() as conn:
        total, current = conn.execute(
            "SELECT count(*), count(*) FILTER (WHERE extract_version >= %s) FROM ml.note_lang",
            (EXTRACT_VERSION,)).fetchone()
        langs = conn.execute("SELECT lang, count(*) FROM ml.note_lang "
                             "GROUP BY 1 ORDER BY 2 DESC LIMIT 10").fetchall()
        tpls = conn.execute("SELECT COALESCE(template, 'none'), count(*) FROM ml.note_lang "
                            "GROUP BY 1 ORDER BY 2 DESC").fetchall()
    print(f"ml.note_lang: {total:,} notes, {current:,} with version {EXTRACT_VERSION}")
    print("Notes by language (top 10):")
    for lang, count in langs:
        print(f"  {lang:5} {count:>10,}")
    print("Notes by app template:")
    for tpl, count in tpls:
        print(f"  {tpl:15} {count:>10,}")


def export_csv(path: str) -> None:
    """All notes in LANGS with their features, streamed to a gzip CSV."""
    sql = QUERY + " WHERE EXISTS (SELECT 1 FROM ml.note_lang l WHERE l.note_id = n.note_id " \
                  "AND l.lang = ANY(%(langs)s))"
    first = True
    with psycopg.connect() as conn, conn.cursor(name="export_cursor") as cur:
        cur.execute(sql, {"langs": LANGS})
        while rows := cur.fetchmany(CHUNK_SIZE):
            df = pd.DataFrame(rows, columns=COLUMNS)
            feats = pd.DataFrame(conn.execute(
                "SELECT note_id, lang, lang_prob, app, template, poi_type, theme_auto, "
                "rule_category, user_text FROM ml.note_lang WHERE note_id = ANY(%s)",
                (df["note_id"].tolist(),)).fetchall(),
                columns=["note_id", "lang", "lang_prob", "app", "template", "poi_type",
                         "theme_auto", "rule_category", "user_text"])
            df = df.merge(feats, on="note_id").drop_duplicates("note_id")
            df["user_text"] = df["user_text"].fillna(df["text"])
            df[OUT_COLUMNS].to_csv(path, mode="w" if first else "a", header=first,
                                   index=False, compression="gzip")
            first = False
    print(f"Wrote {path}")


def write_sample(stamp: str) -> None:
    """Stratified sample of notes in LANGS (not labeled by rule), for labeling."""
    with psycopg.connect() as conn:
        keys = pd.DataFrame(conn.execute(
            "SELECT note_id, lang, app FROM ml.note_lang "
            "WHERE lang = ANY(%s) AND rule_category IS NULL", (LANGS,)).fetchall(),
            columns=SAMPLE_KEYS)
    if keys.empty:
        print("No notes for the sample")
        return
    picked = stratified_sample(keys, SAMPLE_SIZE, ["lang", "app"])
    ids = [int(x) for x in picked["note_id"]]
    sample = read_sample_texts(ids).drop_duplicates("note_id")
    sample = sample.set_index("note_id").reindex(ids).dropna(subset=["text"]).reset_index()
    sample = sample.assign(category="", theme="", claude_category="",
                           claude_theme=sample["theme_auto"].fillna(""))
    path = f"{OUTPUT_DIR}/sample_{stamp}.csv"
    sample[LABEL_CSV_COLUMNS].to_csv(path, index=False, encoding="utf-8-sig")
    print(f"Wrote {path} ({len(sample)} rows)")
    print("Sample by lang/app:")
    print(sample.groupby(["lang", "app"]).size().to_string())


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
            JOIN LATERAL (
              SELECT x.body FROM note_comments_text x
              WHERE x.note_id = nc.note_id AND x.sequence_action = nc.sequence_action
              LIMIT 1) t ON true
            LEFT JOIN countries c ON c.country_id = n.id_country""", (ids,)).fetchall()
    cols = ["note_id", "lang", "app", "template", "poi_type", "theme_auto", "country",
            "text", "user_text"]
    return pd.DataFrame(rows, columns=cols)


def main() -> None:
    force, export = "--force" in sys.argv, "--export" in sys.argv
    print(f"Languages: {LANGS} | sample size: {SAMPLE_SIZE} | version {EXTRACT_VERSION} | "
          f"{'FORCE: recompute all notes' if force else 'only missing or outdated notes'}")
    setup_schema()
    model = fasttext.load_model("/models/lid.176.ftz")
    stamp = dt.date.today().strftime("%Y%m%d")
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    done = process_all(model, force)
    print(f"Processed {done:,} notes in this run")
    print_summary()
    if export:
        export_csv(f"{OUTPUT_DIR}/notes_{stamp}.csv.gz")
    write_sample(stamp)


if __name__ == "__main__":
    if "--watch" in sys.argv:
        watch()
    else:
        main()
