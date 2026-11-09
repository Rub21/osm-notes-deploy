#!/usr/bin/env bash
# Export what the model needs to classify notes, in all languages (run on the server).
# Usage: scripts/infer_export.sh            open notes only
#        STATUS=open,close scripts/infer_export.sh   all notes
# Output: output/infer_input.csv.gz. Copy it to Google Drive:
#   My Drive/osm-notes-training/data/infer_input.csv.gz
# Then run notebooks/infer_colab.ipynb.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p output

docker compose exec -T db sh -c "psql -q -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -v ON_ERROR_STOP=1 -v status=${STATUS:-open}" <<'SQL' \
  | gzip > output/infer_input.csv.gz
COPY (SELECT l.note_id, l.lang, l.template, l.poi_type, l.theme_auto, l.rule_category, l.context, n.status::text AS status, COALESCE(l.user_text, t.body) AS user_text FROM ml.note_lang l JOIN notes n ON n.note_id = l.note_id JOIN note_comments nc ON nc.note_id = l.note_id AND nc.event = 'opened' JOIN LATERAL (SELECT x.body FROM note_comments_text x WHERE x.note_id = nc.note_id AND x.sequence_action = nc.sequence_action LIMIT 1) t ON true WHERE n.status::text = ANY(string_to_array(:'status', ',')) ORDER BY l.note_id) TO STDOUT WITH (FORMAT csv, HEADER);
SQL

echo "Wrote output/infer_input.csv.gz ($(du -h output/infer_input.csv.gz | cut -f1))"
