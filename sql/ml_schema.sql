-- Our tables for labeling and training, kept apart from AngocA's `public` tables.
-- Idempotent: safe to run many times. Run with the DB owner (DB_USER).
-- No foreign keys or views into `public`: AngocA's scripts drop and recreate
-- those tables, and a dependent object would make them fail.

CREATE SCHEMA IF NOT EXISTS ml;

-- Fixed label set. The source of truth is this file: every run of the
-- extractor upserts these rows. Labelers cannot create categories.
-- Each category implies the action for the mapper.
-- Full rules and edge cases: docs/guia_etiquetado.md
CREATE TABLE IF NOT EXISTS ml.categories (
 code     TEXT PRIMARY KEY,
 grp      TEXT NOT NULL CHECK (grp IN ('map', 'close', 'unsure')),
 action   TEXT NOT NULL,
 name_es  TEXT NOT NULL,
 name_en  TEXT NOT NULL,
 example  TEXT,
 sort     INTEGER NOT NULL,
 active   BOOLEAN NOT NULL DEFAULT TRUE
);
ALTER TABLE ml.categories ADD COLUMN IF NOT EXISTS description TEXT;

INSERT INTO ml.categories (code, grp, action, name_es, name_en, example, sort, description) VALUES
 ('missing_place', 'map', 'add', 'Lugar faltante', 'Missing place',
  'Aquí hay una bodega nueva', 1,
  'Un lugar, negocio, edificio o punto de interés que existe y no está en el mapa.'),
 ('place_changed', 'map', 'update', 'Lugar cerrado o cambiado', 'Place closed or changed',
  'La farmacia cerró', 2,
  'Un lugar que ya está en el mapa cerró, se mudó o ahora es otra cosa.'),
 ('wrong_name', 'map', 'update', 'Nombre incorrecto', 'Wrong name',
  'Se llama Av. Grau, no Av. Brasil', 3,
  'El nombre de un lugar o calle está mal escrito, es otro o falta.'),
 ('wrong_road', 'map', 'update', 'Calle o camino faltante o incorrecto', 'Missing or wrong road',
  'Esta calle no tiene salida', 4,
  'Calles, caminos, senderos o vías que faltan, no existen o tienen mal la forma, el sentido, el acceso o la conexión.'),
 ('other_data', 'map', 'update', 'Otro dato incorrecto', 'Other wrong data',
  'El horario cambió', 5,
  'Algo del mapa necesita cambiar pero no es lugar, nombre ni calle: horario, dirección, número, teléfono, tipo, límites, agua, uso del suelo.'),
 ('spam', 'close', 'close', 'Spam / publicidad', 'Spam / advertising',
  'Mejor pollería, delivery 999…', 6,
  'Promociona un negocio o servicio, o tiene enlaces sin relación con el mapa.'),
 ('empty', 'close', 'close', 'Vacía o sin sentido', 'Empty or nonsense',
  'ok, asdf, hola', 7,
  'No dice nada que se pueda entender ni verificar: vacía, saludos, pruebas, letras al azar.'),
 ('personal_data', 'close', 'close_hide', 'Privacidad / datos personales', 'Privacy / personal data',
  'Esta es mi casa, aquí vive mi amigo Juan', 8,
  'Revela dónde vive o qué hace una persona privada, o sus datos: "esta es mi casa", "aquí vive mi amigo", nombre completo, teléfono, correo, placa. La nota se oculta. Gana sobre cualquier otra categoría.'),
 ('not_mappable', 'close', 'close', 'Opinión o no mapeable', 'Opinion or not mappable',
  'Zona peligrosa, buen servicio', 9,
  'Opiniones, quejas, preguntas o información que no va en OSM (seguridad, calidad, precios, tráfico del momento).'),
 ('unsure', 'unsure', 'review', 'No sé / ambigua', 'Unsure / ambiguous',
  NULL, 10,
  'No queda claro qué pide la nota o encaja igual en dos categorías. No fuerces una.')
ON CONFLICT (code) DO UPDATE SET
 grp = EXCLUDED.grp, action = EXCLUDED.action, name_es = EXCLUDED.name_es,
 name_en = EXCLUDED.name_en, example = EXCLUDED.example, sort = EXCLUDED.sort,
 description = EXCLUDED.description;

-- English text for the labeling page and the map.
ALTER TABLE ml.categories ADD COLUMN IF NOT EXISTS description_en TEXT;
ALTER TABLE ml.categories ADD COLUMN IF NOT EXISTS example_en TEXT;
UPDATE ml.categories c SET description_en = v.d, example_en = v.e
FROM (VALUES
 ('missing_place', 'A place, business, building or point of interest that exists and is not on the map.',
  'There is a new grocery store here'),
 ('place_changed', 'A place already on the map closed, moved, or is now something else.',
  'The pharmacy closed'),
 ('wrong_name', 'The name of a place or street is misspelled, different, or missing.',
  'It is called Grau Ave, not Brasil Ave'),
 ('wrong_road', 'Streets, paths, tracks or ways that are missing, do not exist, or have the wrong shape, direction, access or connection.',
  'This street is a dead end'),
 ('other_data', 'Something on the map must change but it is not a place, name or road: opening hours, address, number, phone, type, borders, water, land use.',
  'The opening hours changed'),
 ('spam', 'Promotes a business or service, or has links that have nothing to do with the map.',
  'Best chicken in town, delivery 999…'),
 ('empty', 'Says nothing you can understand or check: empty, greetings, tests, random letters.',
  'ok, asdf, hello'),
 ('personal_data', 'Shows where a private person lives or what they do, or their data: "this is my house", "my friend lives here", full name, phone, email, plate. The note gets hidden. Wins over any other category.',
  'This is my house, my friend Juan lives here'),
 ('not_mappable', 'Opinions, complaints, questions or information that does not go in OSM (safety, quality, prices, live traffic).',
  'Dangerous area, good service'),
 ('unsure', 'It is not clear what the note asks, or it fits two categories equally. Do not force one.',
  NULL)
) AS v(code, d, e)
WHERE c.code = v.code;

-- Second label axis: what the note is about. Fixed list, like categories.
-- theme_auto comes from the POI type in app templates (MAPS.ME, Organic Maps...).
CREATE TABLE IF NOT EXISTS ml.themes (
 code    TEXT PRIMARY KEY,
 name_es TEXT NOT NULL,
 name_en TEXT NOT NULL,
 tags    TEXT,
 sort    INTEGER NOT NULL,
 active  BOOLEAN NOT NULL DEFAULT TRUE
);

INSERT INTO ml.themes (code, name_es, name_en, tags, sort) VALUES
 ('food',      'Comida y bebida',          'Food and drink',       'amenity=restaurant/cafe/bar/fast_food', 1),
 ('shop',      'Tiendas',                  'Shops',                'shop=*', 2),
 ('health',    'Salud',                    'Health',               'amenity=pharmacy/hospital/clinic/doctors', 3),
 ('education', 'Educación',                'Education',            'amenity=school/university/kindergarten', 4),
 ('transport', 'Transporte público',       'Public transport',     'highway=bus_stop, railway=*, public_transport=*', 5),
 ('roads',     'Calles y caminos',         'Roads and paths',      'highway=* (ways), barrier=*, parking', 6),
 ('buildings', 'Edificios y direcciones',  'Buildings and addresses', 'building=*, addr:*, entrance', 7),
 ('leisure',   'Turismo y ocio',           'Tourism and leisure',  'tourism=*, leisure=*, historic=*', 8),
 ('nature',    'Naturaleza y agua',        'Nature and water',     'natural=*, landuse=*, waterway=*', 9),
 ('services',  'Servicios y otros',        'Services and other',   'amenity=* (banks, post, toilets…), office=*', 10),
 ('none',      'No aplica',                'Not applicable',       'spam, empty, privacy', 11)
ON CONFLICT (code) DO UPDATE SET
 name_es = EXCLUDED.name_es, name_en = EXCLUDED.name_en, tags = EXCLUDED.tags, sort = EXCLUDED.sort;

-- Language of the opening comment of every note (filled by the extractor).
-- rnd gives cheap random batches without ORDER BY random() over millions of rows.
CREATE TABLE IF NOT EXISTS ml.note_lang (
 note_id     INTEGER PRIMARY KEY,
 lang        TEXT NOT NULL,
 lang_prob   REAL,
 app         TEXT,
 rnd         DOUBLE PRECISION NOT NULL DEFAULT random(),
 detected_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS note_lang_lang_rnd ON ml.note_lang (lang, rnd);
-- App template split (images/extractor/templates.py). user_text is only stored
-- for notes with a template; for the rest it is the same as the note text.
ALTER TABLE ml.note_lang ADD COLUMN IF NOT EXISTS template TEXT;
ALTER TABLE ml.note_lang ADD COLUMN IF NOT EXISTS user_text TEXT;
ALTER TABLE ml.note_lang ADD COLUMN IF NOT EXISTS poi_type TEXT;
ALTER TABLE ml.note_lang ADD COLUMN IF NOT EXISTS theme_auto TEXT;
-- Category set by rule (e.g. template-only "place has gone" -> place_changed).
-- These notes are not sent to people for labeling.
ALTER TABLE ml.note_lang ADD COLUMN IF NOT EXISTS rule_category TEXT;
-- Short app context the model also reads: StreetComplete question and element,
-- POI name and extra POI types (templates.py). No URLs, versions or dates.
ALTER TABLE ml.note_lang ADD COLUMN IF NOT EXISTS context TEXT;
-- Extractor version that produced the row (EXTRACT_VERSION in extract.py).
-- Rows with an older version are redone on the next extractor run.
ALTER TABLE ml.note_lang ADD COLUMN IF NOT EXISTS extract_version SMALLINT NOT NULL DEFAULT 0;

-- Human-reviewed labels only. One row per note and user (re-labeling updates it).
-- claude_category keeps Claude's suggestion, to measure how often it was right.
CREATE TABLE IF NOT EXISTS ml.note_labels (
 id              BIGSERIAL PRIMARY KEY,
 note_id         INTEGER NOT NULL,
 lang            TEXT,
 category        TEXT NOT NULL REFERENCES ml.categories (code),
 claude_category TEXT,
 user_name       TEXT NOT NULL,
 created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
 updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE (note_id, user_name)
);
CREATE INDEX IF NOT EXISTS note_labels_note ON ml.note_labels (note_id);
ALTER TABLE ml.note_labels ADD COLUMN IF NOT EXISTS theme TEXT REFERENCES ml.themes (code);
ALTER TABLE ml.note_labels ADD COLUMN IF NOT EXISTS claude_theme TEXT;

-- AI suggestions waiting for human review (uploaded CSV with only
-- claude_category filled). Approving one writes a row in ml.note_labels.
CREATE TABLE IF NOT EXISTS ml.note_suggestions (
 note_id     INTEGER PRIMARY KEY,
 lang        TEXT,
 category    TEXT NOT NULL REFERENCES ml.categories (code),
 imported_by TEXT,
 created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS note_suggestions_lang ON ml.note_suggestions (lang);
ALTER TABLE ml.note_suggestions ADD COLUMN IF NOT EXISTS theme TEXT REFERENCES ml.themes (code);

-- Model predictions for every note (notebooks/infer_colab.ipynb, loaded with
-- scripts/infer_load.sh). One row per note: the latest run wins.
-- source = model, or rule (template with no user text, see templates.py).
CREATE TABLE IF NOT EXISTS ml.note_predictions (
 note_id         INTEGER PRIMARY KEY,
 category        TEXT NOT NULL REFERENCES ml.categories (code),
 category_conf   REAL NOT NULL,
 category_2      TEXT REFERENCES ml.categories (code),
 category_2_conf REAL,
 theme           TEXT REFERENCES ml.themes (code),
 theme_conf      REAL,
 source          TEXT NOT NULL,
 model_version   TEXT NOT NULL,
 predicted_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS note_predictions_category ON ml.note_predictions (category, category_conf);
CREATE INDEX IF NOT EXISTS note_predictions_theme ON ml.note_predictions (theme);
CREATE INDEX IF NOT EXISTS note_predictions_at ON ml.note_predictions (predicted_at);

-- Open notes with their prediction, for the map viewer (/map). The classifier
-- service fills it from ml.note_predictions and removes closed notes. Notes with
-- personal data are never stored here (see ml.private_notes).
-- It is a copy because `notes` has no spatial index and is recreated by reloads.
CREATE TABLE IF NOT EXISTS ml.map_notes (
 note_id         INTEGER PRIMARY KEY,
 geom            geometry(Point, 4326) NOT NULL,
 created_at      TIMESTAMPTZ,
 country         TEXT,
 lang            TEXT,
 app             TEXT,
 template        TEXT,
 category        TEXT NOT NULL,
 category_conf   REAL NOT NULL,
 category_2      TEXT,
 category_2_conf REAL,
 theme           TEXT,
 theme_conf      REAL,
 source          TEXT,
 model_version   TEXT,
 predicted_at    TIMESTAMPTZ,
 text            TEXT
);
CREATE INDEX IF NOT EXISTS map_notes_geom ON ml.map_notes USING gist (geom);
CREATE INDEX IF NOT EXISTS map_notes_predicted ON ml.map_notes (predicted_at);

-- Open notes with personal data (predicted, or labeled by a person), same columns
-- as ml.map_notes. Only OSM moderators see them in the map; the public gets a count.
-- The classifier service rebuilds it on every run (a few thousand rows).
-- It was a view first: too slow (4 s), so drop it if it is still there.
DO $$
BEGIN
 IF EXISTS (SELECT FROM pg_views WHERE schemaname = 'ml' AND viewname = 'private_notes') THEN
  DROP VIEW ml.private_notes;
 END IF;
END
$$;
CREATE TABLE IF NOT EXISTS ml.private_notes (LIKE ml.map_notes INCLUDING ALL);

-- Limited role for the labeling API: reads public, writes only ml.note_labels.
DO $$
BEGIN
 IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'labeler') THEN
  CREATE ROLE labeler LOGIN;
 END IF;
END
$$;

GRANT USAGE ON SCHEMA ml TO labeler;
GRANT SELECT ON ml.categories, ml.themes, ml.note_lang, ml.note_predictions, ml.map_notes,
                ml.private_notes TO labeler;
-- A label moves a note at once between the public map and the private list
-- (personal_data or not), without waiting for the classifier.
-- A person's fix (👎) also changes the note's category on the map at once.
GRANT INSERT, DELETE, UPDATE ON ml.map_notes, ml.private_notes TO labeler;
GRANT SELECT, INSERT, UPDATE ON ml.note_labels TO labeler;
GRANT USAGE ON SEQUENCE ml.note_labels_id_seq TO labeler;
GRANT SELECT, INSERT, UPDATE ON ml.note_suggestions TO labeler;

GRANT USAGE ON SCHEMA public TO labeler;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO labeler;
-- AngocA's reloads recreate tables; keep read access on the new ones too.
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO labeler;
