# osm-notes-deploy

Docker Compose stack that keeps a PostgreSQL copy of all OSM notes up to date,
using [OSM-Notes-Ingestion](https://github.com/OSM-Notes/OSM-Notes-Ingestion).

- `db`: PostgreSQL 18 + PostGIS (own image, amd64 and arm64).
- `ingestion`: AngocA's daemon, built from our fork
  ([Rub21/OSM-Notes-Ingestion](https://github.com/Rub21/OSM-Notes-Ingestion)).
  On an empty DB it loads the full Planet notes history (1-3 hours), then
  syncs with the OSM API every minute.

## Run

```bash
cp .env.example .env   # set DB_PASSWORD and DOWNLOAD_USER_AGENT
docker compose up -d --build
docker compose logs -f ingestion
docker compose exec ingestion bash -c 'tail -f /var/log/osm-notes-ingestion/*/*.log'
```

Postgres is only published on `127.0.0.1:${DB_PUBLIC_PORT}`. From your laptop use an SSH tunnel:
`ssh -L 5433:127.0.0.1:5432 user@server`.

## Server sizing (Hetzner)

The base load needs ~4 cores, 8 GB RAM and 80+ GB disk (e.g. CX32/CX42 or CPX31).

## Text of the notes

```sql
SELECT n.note_id, t.body, n.created_at, n.status
FROM notes n
JOIN note_comments_text t ON t.note_id = n.note_id AND t.sequence_action = 1;
```
