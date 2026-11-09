#!/usr/bin/env bash
# Export what the model needs to classify every note, in all languages (run on the server).
# Output: output/infer_input.csv.gz. Copy it to Google Drive:
#   My Drive/osm-notes-training/data/infer_input.csv.gz
# Then run notebooks/infer_colab.ipynb.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p output

docker compose exec -T db sh -c 'psql -q -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1' <<'SQL' \
  | gzip > output/infer_input.csv.gz
\copy (SELECT l.note_id, l.lang, l.template, l.poi_type, l.theme_auto, l.rule_category, n.status::text AS status, l.user_text FROM ml.note_lang l JOIN notes n ON n.note_id = l.note_id ORDER BY l.note_id) TO STDOUT WITH (FORMAT csv, HEADER)
SQL

echo "Wrote output/infer_input.csv.gz ($(du -h output/infer_input.csv.gz | cut -f1))"
