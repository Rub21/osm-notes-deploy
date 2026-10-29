"""Extract OSM notes (whole planet), detect language and write a sample for labeling.

Outputs (in OUTPUT_DIR):
  notes_<date>.csv.gz   all notes in the selected languages
  sample_<date>.csv     stratified sample of SAMPLE_SIZE notes, with empty
                        `category` and `action` columns to label

Only the first comment (the one that opened the note) is used as text.
Later comments are left out because they often contain the answer
("fixed", "closed, it does not exist") and would leak the label.
"""

import datetime as dt
import os
import re

import fasttext
import numpy as np
import pandas as pd
import psycopg

# App that created the note, detected from tags/signatures in the text.
APP_PATTERNS = [
    ("StreetComplete", r"streetcomplete"),
    ("Organic Maps", r"organic ?maps"),
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

COLUMNS = ["note_id", "country", "lat", "lon", "created_at", "status", "closed_at", "text"]
OUT_COLUMNS = ["note_id", "country", "lang", "lang_prob", "app", "lat", "lon",
               "created_at", "status", "closed_at", "text"]


def detect_app(text: str) -> str:
    low = text.lower()
    for name, pattern in APP_PATTERNS:
        if re.search(pattern, low):
            return name
    return "other"


def clean_for_lang(text: str) -> str:
    """Remove URLs, hashtags and app signatures before language detection."""
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"#\S+", " ", text)
    text = re.sub(r"\bvia \S+.*$", " ", text, flags=re.I)
    return " ".join(text.split())


def add_lang_and_app(df: pd.DataFrame, model) -> pd.DataFrame:
    df["text"] = df["text"].fillna("").str.replace("\r", " ").str.strip()
    df["app"] = df["text"].map(detect_app)
    cleaned = df["text"].map(clean_for_lang)
    labels, probs = model.predict(cleaned.str.replace("\n", " ").tolist(), k=1)
    df["lang"] = [lab[0].replace("__label__", "") for lab in labels]
    df["lang_prob"] = [round(float(p[0]), 3) for p in probs]
    df.loc[(df["lang_prob"] < MIN_LANG_PROB) | (cleaned.str.len() < 3), "lang"] = "und"
    return df


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


def main() -> None:
    print(f"Languages: {LANGS} | sample size: {SAMPLE_SIZE}")
    model = fasttext.load_model("/models/lid.176.ftz")
    stamp = dt.date.today().strftime("%Y%m%d")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    full_path = f"{OUTPUT_DIR}/notes_{stamp}.csv.gz"

    total = 0
    lang_counts: dict[str, int] = {}
    kept = []
    # Server-side cursor: read in chunks so the whole planet does not sit in memory.
    with psycopg.connect() as conn, conn.cursor(name="notes_cursor") as cur:
        cur.itersize = CHUNK_SIZE
        cur.execute(QUERY)
        first = True
        while rows := cur.fetchmany(CHUNK_SIZE):
            df = add_lang_and_app(pd.DataFrame(rows, columns=COLUMNS), model)
            total += len(df)
            for lang, count in df["lang"].value_counts().items():
                lang_counts[lang] = lang_counts.get(lang, 0) + int(count)
            df = df[df["lang"].isin(LANGS)][OUT_COLUMNS]
            df.to_csv(full_path, mode="w" if first else "a", header=first,
                      index=False, compression="gzip")
            first = False
            kept.append(df)
            print(f"  read {total:,} notes, kept {sum(len(k) for k in kept):,}", flush=True)

    if total == 0:
        raise SystemExit("No notes found. Has the ingestion finished?")

    print("Notes by language (top 10):")
    for lang, count in sorted(lang_counts.items(), key=lambda x: -x[1])[:10]:
        print(f"  {lang:5} {count:>10,}")

    df = pd.concat(kept, ignore_index=True)
    print(f"Notes in {LANGS}: {len(df):,} -> {full_path}")

    sample = stratified_sample(df, SAMPLE_SIZE, ["lang", "app"])
    sample = sample.assign(category="", action="")
    sample_path = f"{OUTPUT_DIR}/sample_{stamp}.csv"
    sample.to_csv(sample_path, index=False)
    print(f"Wrote {sample_path} ({len(sample)} rows)")
    print("Sample by lang/app:")
    print(sample.groupby(["lang", "app"]).size().to_string())
    print("Sample top countries:")
    print(sample["country"].value_counts().head(10).to_string())


if __name__ == "__main__":
    main()
