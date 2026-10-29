-- Our tables for labeling and training, kept apart from AngocA's `public` tables.
-- Idempotent: safe to run many times. Run with the DB owner (DB_USER).
-- No foreign keys or views into `public`: AngocA's scripts drop and recreate
-- those tables, and a dependent object would make them fail.

CREATE SCHEMA IF NOT EXISTS ml;

-- Fixed label set. Edit only with SQL (admin), not from the labeling page.
-- Each category implies the action for the mapper.
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

INSERT INTO ml.categories (code, grp, action, name_es, name_en, example, sort) VALUES
 ('missing_place',  'map',    'add',          'Lugar faltante',            'Missing place',           'Aquí hay una bodega nueva', 1),
 ('place_changed',  'map',    'update',       'Lugar cerrado o cambiado',  'Place closed or changed', 'La farmacia cerró', 2),
 ('wrong_name',     'map',    'update',       'Nombre incorrecto',         'Wrong name',              'Se llama Av. Grau, no Av. Brasil', 3),
 ('wrong_road',     'map',    'update',       'Calle o camino incorrecto', 'Wrong road or path',      'Esta calle no tiene salida', 4),
 ('other_data',     'map',    'update',       'Otro dato incorrecto',      'Other wrong data',        'El horario cambió', 5),
 ('spam',           'close',  'close',        'Spam / publicidad',         'Spam / advertising',      'Mejor pollería, delivery 999…', 6),
 ('empty',          'close',  'close',        'Vacía o sin sentido',       'Empty or nonsense',       'ok, asdf, hola', 7),
 ('personal_data',  'close',  'close_hide',   'Datos personales',          'Personal data',           'Casa de mi abuela, teléfonos', 8),
 ('not_mappable',   'close',  'close',        'Opinión o no mapeable',     'Opinion or not mappable', 'Zona peligrosa, buen servicio', 9),
 ('unsure',         'unsure', 'review',       'No sé / ambigua',           'Unsure / ambiguous',      NULL, 10)
ON CONFLICT (code) DO NOTHING;

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

-- Limited role for the labeling API: reads public, writes only ml.note_labels.
DO $$
BEGIN
 IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'labeler') THEN
  CREATE ROLE labeler LOGIN;
 END IF;
END
$$;

GRANT USAGE ON SCHEMA ml TO labeler;
GRANT SELECT ON ml.categories, ml.note_lang TO labeler;
GRANT SELECT, INSERT, UPDATE ON ml.note_labels TO labeler;
GRANT USAGE ON SEQUENCE ml.note_labels_id_seq TO labeler;

GRANT USAGE ON SCHEMA public TO labeler;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO labeler;
-- AngocA's reloads recreate tables; keep read access on the new ones too.
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO labeler;
