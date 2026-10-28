#!/bin/bash
set -euo pipefail

APP_DIR=/opt/osm-notes-ingestion

# DATA_DIR is a volume: seed it with the static files shipped in the repo.
mkdir -p "${DATA_DIR}"
cp -rn "${APP_DIR}/data/." "${DATA_DIR}/" 2> /dev/null || true

# Lock dir lives in the container (not a volume), but clear stale locks anyway.
rm -f /var/run/osm-notes-ingestion/*.lock 2> /dev/null || true

# libpq variables, used by psql and ogr2ogr calls that do not pass flags.
export PGHOST="${DB_HOST}" PGPORT="${DB_PORT}" PGUSER="${DB_USER}" \
       PGPASSWORD="${DB_PASSWORD}" PGDATABASE="${DBNAME}"

until pg_isready -q; do
 echo "Waiting for database ${DB_HOST}:${DB_PORT}..."
 sleep 3
done

exec "$@"
