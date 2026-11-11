"""Classify open notes on CPU and keep ml.note_predictions up to date.

Every INTERVAL seconds it looks for open notes that have no prediction, or a
prediction from another model version, classifies them with the ONNX int8
models and saves the result. Newest notes go first. Then it copies the new
predictions to ml.map_notes (the map viewer) and removes closed notes there.

It is made for the ~1,500 new notes a day (~3-8 notes/s on CPU). To classify
all open notes with a new model, use Colab (notebooks/infer_colab.ipynb) and
scripts/infer_load.sh first, then point MODEL_DIR to the new model: CPU would
take more than a day for ~450k notes.

Models (from notebooks/export_onnx_colab.ipynb) live in MODEL_DIR:
  MODEL_DIR/category/{model.onnx, tokenizer.json, labels.json}
  MODEL_DIR/theme/{model.onnx, tokenizer.json, labels.json}

Modes:
  classify.py             loop forever (classifier service)
  classify.py --once      classify what is pending, update the map table and exit
  classify.py --map-full  also rebuild ml.map_notes from all predictions at start
"""

import json
import os
import sys
import time

import numpy as np
import onnxruntime as ort
import psycopg
from tokenizers import Tokenizer

MODEL_DIR = os.environ.get("MODEL_DIR", "/models/ft_v2_context")
INTERVAL = int(os.environ.get("INTERVAL", "600"))
THREADS = int(os.environ.get("THREADS", "2"))
LIMIT = int(os.environ.get("LIMIT", "1000"))     # notes per database round
BATCH = 16                                       # notes per model call
NO_THEME = {"spam", "empty", "personal_data"}    # theme is always "none" for these

PENDING_SQL = """
SELECT l.note_id, COALESCE(l.template, ''), COALESCE(l.poi_type, ''),
       COALESCE(l.context, ''), COALESCE(l.rule_category, ''),
       COALESCE(l.user_text, t.body, '')
FROM ml.note_lang l
JOIN notes n ON n.note_id = l.note_id AND n.status::text = 'open'
LEFT JOIN ml.note_predictions p ON p.note_id = l.note_id
JOIN note_comments nc ON nc.note_id = l.note_id AND nc.event = 'opened'
JOIN LATERAL (SELECT x.body FROM note_comments_text x
              WHERE x.note_id = nc.note_id AND x.sequence_action = nc.sequence_action
              LIMIT 1) t ON true
WHERE p.note_id IS NULL OR p.model_version <> %(v)s
ORDER BY l.note_id DESC
LIMIT %(n)s
"""

UPSERT_SQL = """
INSERT INTO ml.note_predictions (note_id, category, category_conf, category_2, category_2_conf,
                                 theme, theme_conf, source, model_version)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (note_id) DO UPDATE SET
  category = EXCLUDED.category, category_conf = EXCLUDED.category_conf,
  category_2 = EXCLUDED.category_2, category_2_conf = EXCLUDED.category_2_conf,
  theme = EXCLUDED.theme, theme_conf = EXCLUDED.theme_conf,
  source = EXCLUDED.source, model_version = EXCLUDED.model_version,
  predicted_at = now()
"""


# Copy new predictions of open notes to ml.map_notes (what the map viewer reads).
MAP_UPSERT_SQL = """
INSERT INTO ml.map_notes (note_id, geom, created_at, country, lang, app, template,
                          category, category_conf, category_2, category_2_conf,
                          theme, theme_conf, source, model_version, predicted_at, text)
SELECT p.note_id,
       ST_SetSRID(ST_MakePoint(n.longitude::float8, n.latitude::float8), 4326),
       n.created_at, COALESCE(c.country_name_en, c.country_name), l.lang, l.app, l.template,
       p.category, p.category_conf, p.category_2, p.category_2_conf,
       p.theme, p.theme_conf, p.source, p.model_version, p.predicted_at,
       left(COALESCE(l.user_text, t.body, ''), 300)
FROM ml.note_predictions p
JOIN notes n ON n.note_id = p.note_id AND n.status::text = 'open'
JOIN ml.note_lang l ON l.note_id = p.note_id
LEFT JOIN countries c ON c.country_id = n.id_country
LEFT JOIN LATERAL (
  SELECT x.body FROM note_comments nc
  JOIN note_comments_text x ON x.note_id = nc.note_id AND x.sequence_action = nc.sequence_action
  WHERE nc.note_id = p.note_id AND nc.event = 'opened' LIMIT 1) t ON true
WHERE p.predicted_at > COALESCE(%(since)s::timestamptz, '-infinity')
  AND p.category <> 'personal_data'
  AND NOT EXISTS (SELECT 1 FROM ml.note_labels h
                  WHERE h.note_id = p.note_id AND h.category = 'personal_data')
ON CONFLICT (note_id) DO UPDATE SET
  geom = EXCLUDED.geom, created_at = EXCLUDED.created_at, country = EXCLUDED.country,
  lang = EXCLUDED.lang, app = EXCLUDED.app, template = EXCLUDED.template,
  category = EXCLUDED.category, category_conf = EXCLUDED.category_conf,
  category_2 = EXCLUDED.category_2, category_2_conf = EXCLUDED.category_2_conf,
  theme = EXCLUDED.theme, theme_conf = EXCLUDED.theme_conf, source = EXCLUDED.source,
  model_version = EXCLUDED.model_version, predicted_at = EXCLUDED.predicted_at,
  text = EXCLUDED.text
"""

# Closed notes, new personal_data predictions, and notes a person labeled personal_data.
MAP_DELETE_SQL = """
DELETE FROM ml.map_notes m
WHERE m.category = 'personal_data'
   OR NOT EXISTS (SELECT 1 FROM notes n WHERE n.note_id = m.note_id AND n.status::text = 'open')
   OR EXISTS (SELECT 1 FROM ml.note_predictions p
              WHERE p.note_id = m.note_id AND p.category = 'personal_data')
   OR EXISTS (SELECT 1 FROM ml.note_labels h
              WHERE h.note_id = m.note_id AND h.category = 'personal_data')
"""


def sync_map(full: bool = False) -> None:
    """Keep ml.map_notes in step with the predictions and the note status."""
    t0 = time.time()
    with psycopg.connect() as conn:
        since = None if full else conn.execute(   # None = all predictions
            "SELECT max(predicted_at) FROM ml.map_notes").fetchone()[0]
        added = conn.execute(MAP_UPSERT_SQL, {"since": since}).rowcount
        removed = conn.execute(MAP_DELETE_SQL).rowcount
        conn.commit()
        if added > 10_000:
            conn.execute("ANALYZE ml.map_notes")
    if added or removed:
        print(f"  map: {added:,} added or updated, {removed:,} removed "
              f"in {time.time() - t0:.0f} s", flush=True)


def model_input(template: str, poi_type: str, context: str, user_text: str) -> str:
    """Same input as training: template tags + app context + the user's words."""
    tags = "".join(f"[{v}] " for v in (template, poi_type, context) if v)
    return (tags + " ".join(user_text.split())).strip()


class Model:
    """One ONNX classifier with its tokenizer and labels."""

    def __init__(self, path: str):
        meta = json.load(open(f"{path}/labels.json"))
        self.labels = np.array(meta["labels"])
        self.version = meta["run_name"]
        self.tok = Tokenizer.from_file(f"{path}/tokenizer.json")
        self.tok.enable_truncation(max_length=meta["max_len"])
        self.pad = self.tok.token_to_id("<pad>")
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = THREADS
        opts.inter_op_num_threads = 1
        self.sess = ort.InferenceSession(f"{path}/model.onnx", opts,
                                         providers=["CPUExecutionProvider"])

    def probs(self, texts: list[str]) -> np.ndarray:
        """Class probabilities. Texts are sorted by length so batches need little padding."""
        ids = [e.ids for e in self.tok.encode_batch(texts)]
        out = np.zeros((len(texts), len(self.labels)), dtype=np.float32)
        order = np.argsort([len(x) for x in ids])
        for start in range(0, len(ids), BATCH):
            idx = order[start:start + BATCH]
            width = max(len(ids[i]) for i in idx)
            input_ids = np.full((len(idx), width), self.pad, dtype=np.int64)
            mask = np.zeros((len(idx), width), dtype=np.int64)
            for row, i in enumerate(idx):
                input_ids[row, :len(ids[i])] = ids[i]
                mask[row, :len(ids[i])] = 1
            logits = self.sess.run(["logits"], {"input_ids": input_ids, "attention_mask": mask})[0]
            e = np.exp(logits - logits.max(1, keepdims=True))
            out[idx] = e / e.sum(1, keepdims=True)
        return out


def classify(rows, cat: Model, theme: Model) -> list[tuple]:
    texts = [model_input(*r[1:4], r[5]) for r in rows]
    pc, pt = cat.probs(texts), theme.probs(texts)
    top = np.argsort(-pc, axis=1)
    result = []
    for k, r in enumerate(rows):
        rule = r[4]
        if rule:   # template with no user text: category from a rule (templates.py)
            category, conf, cat2, conf2, source = rule, 1.0, None, None, "rule"
        else:
            a, b = top[k, 0], top[k, 1]
            category, conf, source = str(cat.labels[a]), float(pc[k, a]), "model"
            cat2, conf2 = str(cat.labels[b]), round(float(pc[k, b]), 4)
        if category in NO_THEME:   # spam, empty and private notes are not about a feature
            th, th_conf = "none", conf
        else:
            j = pt[k].argmax()
            th, th_conf = str(theme.labels[j]), float(pt[k, j])
        result.append((r[0], category, round(conf, 4), cat2, conf2, th, round(th_conf, 4),
                       source, cat.version))
    return result


def run_once(cat: Model, theme: Model) -> int:
    done = 0
    with psycopg.connect() as conn:
        while True:
            rows = conn.execute(PENDING_SQL, {"v": cat.version, "n": LIMIT}).fetchall()
            if not rows:
                return done
            t0 = time.time()
            preds = classify(rows, cat, theme)
            with conn.cursor() as cur:
                cur.executemany(UPSERT_SQL, preds)
            conn.commit()
            done += len(rows)
            secs = time.time() - t0
            print(f"  {len(rows):,} notes in {secs:.0f} s ({len(rows) / max(secs, 0.001):.1f}/s) "
                  f"| total {done:,}", flush=True)


def main() -> None:
    cat, theme = Model(f"{MODEL_DIR}/category"), Model(f"{MODEL_DIR}/theme")
    if cat.version != theme.version:
        sys.exit(f"Model versions differ: {cat.version} vs {theme.version}")
    print(f"Models {cat.version} loaded from {MODEL_DIR} ({THREADS} threads)", flush=True)
    if "--once" in sys.argv:
        print(f"classified {run_once(cat, theme):,} notes", flush=True)
        sync_map(full="--map-full" in sys.argv)
        return
    full = "--map-full" in sys.argv
    while True:
        try:
            n = run_once(cat, theme)
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} classified {n:,} notes", flush=True)
            sync_map(full)
            full = False
        except psycopg.Error as e:
            print(f"DB error, retrying later: {e}", flush=True)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
