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

## Extract notes for labeling

After the ingestion finishes, run the extractor:

```bash
docker compose run --rm extractor            # only missing or outdated notes
docker compose run --rm extractor --force    # recompute every note
docker compose run --rm extractor --export   # also write notes_<date>.csv.gz
```

It saves progress every 100,000 note ids. If it stops, run it again and it
continues where it stopped. Bump `EXTRACT_VERSION` in `extract.py` when
`templates.py` or the language detection changes, so old rows are redone.

It writes to `./output`:

- `sample_<date>.csv`: 1000 notes from the whole planet (Spanish, English and
  short/undetected text), spread by language and app, with empty `category`
  and `theme` columns to label.
- `notes_<date>.csv.gz` (only with `--export`): all notes in those languages.

Only the text that opened the note is used. Closing comments are left out so
they do not leak the answer. Change `SAMPLE_SIZE` and `LANGS` in `.env`.

About a third of the notes come from apps (MAPS.ME, Organic Maps, CoMaps,
StreetComplete, OnOSM) with long fixed template text.
`images/extractor/templates.py` splits it from the words the user wrote.
`ml.note_lang` stores, for each note:

- `lang`: the language of the user's words only, not the template.
- `template`, `user_text`, `poi_type`: the app template, the user's words
  and the OSM type the app reported (e.g. `amenity-pharmacy`).
- `theme_auto`: a theme guessed from `poi_type`.
- `rule_category`: set when the label is certain without a person. For
  example, "place has gone" with no user text becomes `place_changed`.
  These notes are not sent for labeling.

`lang-updater` keeps all of this up to date for new notes every 5 minutes.

## Labeling page

`labeler` serves a page and API to label notes by language, one by one or
by CSV (download, fill `category`, upload). Labels go to `ml.note_labels`,
in the same DB but in our own `ml` schema (see `sql/ml_schema.sql`).

1. Run the extractor once: it creates the `ml` schema, the limited
   `labeler` DB role and the language of every note (`ml.note_lang`).
2. `docker compose up -d labeler`
3. From your laptop: `ssh -L 8090:127.0.0.1:8090 root@<server>` and open
   http://localhost:8090

Each note gets two labels: a category (what to do) and a theme (what it is
about). Categories and themes are defined in `sql/ml_schema.sql` (re-applied
on every extractor run) and explained in `docs/guia_etiquetado.md`. Claude only fills
the CSV; a person reviews it and uploads it. `GET /api/labels.csv` exports
all labels with the note text for training.
