#!/bin/sh
# Daily backup of the tables that can't be rebuilt (people's labels and actions) plus
# the predictions (rebuilding them needs a Colab run). Everything else comes back on
# its own: public.* from ingestion, ml.note_lang from the extractor, ml.map_notes
# from the classifier. Runs in the `backup` service (docker-compose.yml).
#
# One file per day in /backups (./backups on the server): ml_YYYY-MM-DD.dump,
# ~25 MB. Keeps BACKUP_KEEP_DAYS days. Checks every hour, so a restart doesn't
# make a second file for the same day.
#
# Restore one table (example: labels), from the repo folder:
#   docker compose exec -T db pg_restore -U notes -d notes --clean --if-exists \
#     -n ml -t note_labels < backups/ml_2026-11-27.dump
# List what a file has: docker compose exec -T db pg_restore -l < backups/ml_....dump
set -eu

KEEP_DAYS="${BACKUP_KEEP_DAYS:-30}"
DIR=/backups
TABLES="ml.note_labels ml.note_actions ml.note_suggestions ml.note_predictions
        ml.categories ml.themes"

while true; do
  day=$(date -u +%F)
  out="$DIR/ml_$day.dump"
  if [ ! -s "$out" ]; then
    args=""
    for t in $TABLES; do args="$args -t $t"; done
    # Write to .tmp first: a half-written file never looks like a good backup.
    # shellcheck disable=SC2086
    if pg_dump -Fc $args -f "$out.tmp"; then
      mv "$out.tmp" "$out"
      echo "$(date -u +%FT%TZ) backup ok: $out ($(du -h "$out" | cut -f1))"
    else
      rm -f "$out.tmp"
      echo "$(date -u +%FT%TZ) backup FAILED, retry in 1 h" >&2
    fi
    find "$DIR" -name 'ml_*.dump' -mtime +"$KEEP_DAYS" -print -delete
  fi
  sleep 3600
done
