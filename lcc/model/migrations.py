"""Перевод базы ЛР3 на схему ЛР4 (docs/07_lab4_analysis.md, раздел 7.5).

Версия схемы хранится в ``PRAGMA user_version``: 0 — база ЛР3, 2 — текущая.
Таблицы ЛР3 переименовываются в ``v1_*``, создается новая схема, данные
копируются с преобразованием, старые таблицы удаляются. Все в одной транзакции:
при любой ошибке база остается в версии ЛР3.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 2

#: Таблицы ЛР3 в порядке копирования (родители раньше детей).
V1_TABLES = ("staff", "vehicle_type", "launch_pad", "launch", "stage_log", "pad_maintenance", "fuel_batch",
             "fuel_consumption", "weather_observation", "telemetry_channel", "incident", "postponement")

DEFAULT_SPACEPORT = (1, "MAIN", "Основной космодром", "")

#: Копирование данных. Ключ — таблица, значение — INSERT … SELECT из v1_*.
COPY_SQL = {
    "staff": "INSERT INTO staff(staff_id, login, full_name, role, password_hash, password_salt, is_active, "
             "spaceport_id) SELECT staff_id, login, full_name, role, password_hash, password_salt, is_active, "
             "CASE WHEN role = 'head' THEN NULL ELSE 1 END FROM v1_staff",
    "vehicle_type": "INSERT INTO vehicle_type(vehicle_type_id, name, vehicle_class, stages_count) "
                    "SELECT vehicle_type_id, name, vehicle_class, stages_count FROM v1_vehicle_type",
    "launch_pad": "INSERT INTO launch_pad(pad_id, spaceport_id, code, name, max_concurrent, cycle_limit, hours_limit, "
                  "is_faulty) SELECT pad_id, 1, code, name, max_concurrent, cycle_limit, hours_limit, is_faulty "
                  "FROM v1_launch_pad",
    "launch": "INSERT INTO launch(launch_id, vehicle_type_id, pad_id, created_by, payload, target_orbit, window_start, "
              "window_end, status, pad_hours, cancel_reason) SELECT launch_id, vehicle_type_id, pad_id, created_by, "
              "payload, target_orbit, window_start, window_end, status, pad_hours, cancel_reason FROM v1_launch",
    "stage_log": "INSERT INTO stage_log(launch_id, seq_no, status, changed_at, staff_id) "
                 "SELECT launch_id, seq_no, status, changed_at, staff_id FROM v1_stage_log",
    "pad_maintenance": "INSERT INTO pad_maintenance(pad_id, maint_no, kind, opened_at, closed_at, notes, staff_id) "
                       "SELECT pad_id, maint_no, kind, opened_at, closed_at, notes, staff_id FROM v1_pad_maintenance",
    # тонны → гектограммы: 1 т = 1000 кг = 10 000 гг
    "fuel_batch": "INSERT INTO fuel_batch(batch_id, spaceport_id, batch_no, component, grade, initial_mass_hg, "
                  "produced_on, expires_on) SELECT batch_id, 1, batch_no, component, grade, "
                  "CAST(ROUND(initial_volume * 10000) AS INTEGER), produced_on, expires_on FROM v1_fuel_batch",
    "fuel_consumption": "INSERT INTO fuel_consumption(consumption_id, launch_id, batch_id, mass_hg, consumed_at, "
                        "staff_id) SELECT consumption_id, launch_id, batch_id, CAST(ROUND(volume * 10000) AS INTEGER), "
                        "consumed_at, staff_id FROM v1_fuel_consumption",
    "weather_observation": "INSERT INTO weather_observation(launch_id, obs_no, observed_at, ground_wind, "
                           "altitude_wind, temperature, cloud_base, thunderstorm, staff_id) SELECT launch_id, obs_no, "
                           "observed_at, ground_wind, altitude_wind, temperature, cloud_base, thunderstorm, staff_id "
                           "FROM v1_weather_observation",
    "telemetry_channel": "INSERT INTO telemetry_channel(channel_id, channel_no, vehicle_system, parameter, "
                         "frequency_hz) SELECT channel_id, channel_no, vehicle_system, parameter, frequency_hz "
                         "FROM v1_telemetry_channel",
    "incident": "INSERT INTO incident(incident_id, launch_id, channel_id, occurred_at, description, measures, "
                "staff_id) SELECT incident_id, launch_id, channel_id, occurred_at, description, measures, staff_id "
                "FROM v1_incident",
    # П9: НС — техническая причина, «прочее» — организационная
    "postponement": "INSERT INTO postponement(launch_id, postpone_no, reason, old_start, old_end, new_start, new_end, "
                    "comment, staff_id, recorded_at) SELECT launch_id, postpone_no, "
                    "CASE reason WHEN 'incident' THEN 'technical' WHEN 'other' THEN 'organizational' ELSE reason END, "
                    "old_start, old_end, new_start, new_end, comment, staff_id, recorded_at FROM v1_postponement",
}


def split_statements(script: str) -> list[str]:
    """Разделить SQL-скрипт на команды (executescript нельзя: он сам делает COMMIT)."""
    statements, buf = [], ""
    for line in script.splitlines(keepends=True):
        if line.lstrip().startswith("--"):
            continue
        buf += line
        if sqlite3.complete_statement(buf):
            statements.append(buf.strip())
            buf = ""
    return [st for st in statements if st]


def is_lab3_database(conn: sqlite3.Connection) -> bool:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    has_launch = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='launch'").fetchone()
    return version == 0 and has_launch is not None


def migrate_v0_to_v2(conn: sqlite3.Connection, schema: Path) -> None:
    """Перевести базу ЛР3 на схему версии 2 в одной транзакции."""
    old_isolation = conn.isolation_level
    conn.isolation_level = None                     # транзакцией управляем сами
    conn.execute("PRAGMA foreign_keys = OFF")       # таблицы пересоздаются, проверим ключи в конце
    conn.execute("PRAGMA legacy_alter_table = ON")  # переименование не переписывает ссылки других таблиц
    try:
        conn.execute("BEGIN")
        conn.execute("DROP INDEX IF EXISTS ix_launch_pad_status")
        for table in V1_TABLES:
            conn.execute(f"ALTER TABLE {table} RENAME TO v1_{table}")
        for statement in split_statements(schema.read_text(encoding="utf-8")):
            conn.execute(statement)
        conn.execute("INSERT INTO spaceport(spaceport_id, code, name, region) VALUES (?,?,?,?)", DEFAULT_SPACEPORT)
        for table in V1_TABLES:
            conn.execute(COPY_SQL[table])
        for table in reversed(V1_TABLES):
            conn.execute(f"DROP TABLE v1_{table}")
        broken = conn.execute("PRAGMA foreign_key_check").fetchall()
        if broken:
            raise sqlite3.IntegrityError(f"Нарушены внешние ключи после миграции: {broken[:3]}")
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute("PRAGMA legacy_alter_table = OFF")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.isolation_level = old_isolation
