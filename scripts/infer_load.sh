#!/usr/bin/env bash
# Load model predictions into ml.note_predictions (run on the server).
# Usage: scripts/infer_load.sh output/predictions_ft_20261109_all.csv.gz
# The file comes from notebooks/infer_colab.ipynb. Rows for the same note are replaced.
set -euo pipefail
cd "$(dirname "$0")/.."
FILE="${1:?Usage: scripts/infer_load.sh predictions.csv.gz}"
COLS="note_id, category, category_conf, category_2, category_2_conf, theme, theme_conf, source, model_version"

psql() {
  docker compose exec -T db sh -c 'psql -q -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 "$@"' -- "$@"
}

# Make sure the table exists (the schema file is safe to run again).
psql -f - < sql/ml_schema.sql

psql -c "DROP TABLE IF EXISTS ml.stage_predictions;
         CREATE UNLOGGED TABLE ml.stage_predictions (LIKE ml.note_predictions INCLUDING DEFAULTS);"
gunzip -c "$FILE" | psql -c "\copy ml.stage_predictions ($COLS) FROM STDIN WITH (FORMAT csv, HEADER)"
psql -c "INSERT INTO ml.note_predictions ($COLS)
         SELECT $COLS FROM ml.stage_predictions
         ON CONFLICT (note_id) DO UPDATE SET
           category = EXCLUDED.category, category_conf = EXCLUDED.category_conf,
           category_2 = EXCLUDED.category_2, category_2_conf = EXCLUDED.category_2_conf,
           theme = EXCLUDED.theme, theme_conf = EXCLUDED.theme_conf,
           source = EXCLUDED.source, model_version = EXCLUDED.model_version,
           predicted_at = now();
         DROP TABLE ml.stage_predictions;
         ANALYZE ml.note_predictions;"
psql -c "SELECT model_version, source, count(*) FROM ml.note_predictions GROUP BY 1, 2 ORDER BY 1, 2;"
