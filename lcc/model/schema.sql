-- Физическая схема АИС ЦУП (SQLite). Соответствует модели IDEF1X (docs/04_idef1x.md).
-- Вычисляемые показатели (остаток топлива, циклы и состояние площадки) не хранятся.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS staff (
    staff_id       INTEGER PRIMARY KEY,
    login          TEXT NOT NULL UNIQUE,
    full_name      TEXT NOT NULL,
    role           TEXT NOT NULL CHECK (role IN ('head','pad_engineer','fuel_engineer','meteorologist','telemetry_operator')),
    password_hash  TEXT NOT NULL,
    password_salt  TEXT NOT NULL,
    is_active      INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1))
);

CREATE TABLE IF NOT EXISTS vehicle_type (
    vehicle_type_id INTEGER PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,
    vehicle_class   TEXT NOT NULL CHECK (vehicle_class IN ('light','medium','heavy')),
    stages_count    INTEGER NOT NULL CHECK (stages_count BETWEEN 1 AND 5)
);

CREATE TABLE IF NOT EXISTS launch_pad (
    pad_id          INTEGER PRIMARY KEY,
    code            TEXT NOT NULL UNIQUE,
    name            TEXT NOT NULL,
    max_concurrent  INTEGER NOT NULL CHECK (max_concurrent >= 1),
    cycle_limit     INTEGER NOT NULL CHECK (cycle_limit >= 1),
    hours_limit     REAL NOT NULL CHECK (hours_limit > 0),
    is_faulty       INTEGER NOT NULL DEFAULT 0 CHECK (is_faulty IN (0,1))
);

CREATE TABLE IF NOT EXISTS launch (
    launch_id        INTEGER PRIMARY KEY,
    vehicle_type_id  INTEGER NOT NULL REFERENCES vehicle_type(vehicle_type_id),
    pad_id           INTEGER NOT NULL REFERENCES launch_pad(pad_id),
    created_by       INTEGER NOT NULL REFERENCES staff(staff_id),
    payload          TEXT NOT NULL,
    target_orbit     TEXT NOT NULL,
    window_start     TEXT NOT NULL,
    window_end       TEXT NOT NULL,
    status           TEXT NOT NULL,
    pad_hours        REAL CHECK (pad_hours IS NULL OR pad_hours >= 0),
    cancel_reason    TEXT NOT NULL DEFAULT '',
    CHECK (window_start < window_end)
);
CREATE INDEX IF NOT EXISTS ix_launch_pad_status ON launch(pad_id, status);

CREATE TABLE IF NOT EXISTS stage_log (
    launch_id   INTEGER NOT NULL REFERENCES launch(launch_id),
    seq_no      INTEGER NOT NULL,
    status      TEXT NOT NULL,
    changed_at  TEXT NOT NULL,
    staff_id    INTEGER NOT NULL REFERENCES staff(staff_id),
    PRIMARY KEY (launch_id, seq_no)
);

CREATE TABLE IF NOT EXISTS pad_maintenance (
    pad_id     INTEGER NOT NULL REFERENCES launch_pad(pad_id),
    maint_no   INTEGER NOT NULL,
    kind       TEXT NOT NULL CHECK (kind IN ('inspection','repair','replacement')),
    opened_at  TEXT NOT NULL,
    closed_at  TEXT,
    notes      TEXT NOT NULL DEFAULT '',
    staff_id   INTEGER NOT NULL REFERENCES staff(staff_id),
    PRIMARY KEY (pad_id, maint_no)
);

CREATE TABLE IF NOT EXISTS fuel_batch (
    batch_id        INTEGER PRIMARY KEY,
    batch_no        TEXT NOT NULL UNIQUE,
    component       TEXT NOT NULL CHECK (component IN ('fuel','oxidizer')),
    grade           TEXT NOT NULL,
    initial_volume  REAL NOT NULL CHECK (initial_volume > 0),
    produced_on     TEXT NOT NULL,
    expires_on      TEXT NOT NULL,
    CHECK (produced_on < expires_on)
);

CREATE TABLE IF NOT EXISTS fuel_consumption (
    consumption_id  INTEGER PRIMARY KEY,
    launch_id       INTEGER NOT NULL REFERENCES launch(launch_id),
    batch_id        INTEGER NOT NULL REFERENCES fuel_batch(batch_id),
    volume          REAL NOT NULL CHECK (volume > 0),
    consumed_at     TEXT NOT NULL,
    staff_id        INTEGER NOT NULL REFERENCES staff(staff_id)
);

CREATE TABLE IF NOT EXISTS weather_observation (
    launch_id      INTEGER NOT NULL REFERENCES launch(launch_id),
    obs_no         INTEGER NOT NULL,
    observed_at    TEXT NOT NULL,
    ground_wind    REAL NOT NULL CHECK (ground_wind >= 0),
    altitude_wind  REAL NOT NULL CHECK (altitude_wind >= 0),
    temperature    REAL NOT NULL,
    cloud_base     REAL NOT NULL CHECK (cloud_base >= 0),
    thunderstorm   INTEGER NOT NULL CHECK (thunderstorm IN (0,1)),
    staff_id       INTEGER NOT NULL REFERENCES staff(staff_id),
    PRIMARY KEY (launch_id, obs_no)
);

CREATE TABLE IF NOT EXISTS telemetry_channel (
    channel_id      INTEGER PRIMARY KEY,
    channel_no      TEXT NOT NULL UNIQUE,
    vehicle_system  TEXT NOT NULL,
    parameter       TEXT NOT NULL,
    frequency_hz    REAL NOT NULL CHECK (frequency_hz > 0)
);

CREATE TABLE IF NOT EXISTS incident (
    incident_id  INTEGER PRIMARY KEY,
    launch_id    INTEGER NOT NULL REFERENCES launch(launch_id),
    channel_id   INTEGER REFERENCES telemetry_channel(channel_id),
    occurred_at  TEXT NOT NULL,
    description  TEXT NOT NULL,
    measures     TEXT NOT NULL,
    staff_id     INTEGER NOT NULL REFERENCES staff(staff_id)
);

CREATE TABLE IF NOT EXISTS postponement (
    launch_id    INTEGER NOT NULL REFERENCES launch(launch_id),
    postpone_no  INTEGER NOT NULL,
    reason       TEXT NOT NULL CHECK (reason IN ('weather','technical','incident','other')),
    old_start    TEXT NOT NULL,
    old_end      TEXT NOT NULL,
    new_start    TEXT NOT NULL,
    new_end      TEXT NOT NULL,
    comment      TEXT NOT NULL DEFAULT '',
    staff_id     INTEGER NOT NULL REFERENCES staff(staff_id),
    recorded_at  TEXT NOT NULL,
    PRIMARY KEY (launch_id, postpone_no)
);
