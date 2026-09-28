"""Репозитории (DAO): преобразование сущностей в строки таблиц и обратно.

Только параметризованные запросы. Бизнес-правил здесь нет — только хранение
и агрегирующие выборки, необходимые для вычисляемых показателей.
"""
from __future__ import annotations

from datetime import datetime

from .database import Database, date_from_db, date_to_db, dt_from_db, dt_to_db
from .entities import (FuelBatch, FuelConsumption, Launch, LaunchPad, LaunchWindow, MaintenanceRecord,
                       PadUsage, Staff, TelemetryChannel, VehicleType)
from .enums import (ACTIVE_STATUSES, FuelComponent, LaunchStatus, MaintenanceKind, PostponeReason, Role,
                    VehicleClass)
from .errors import BusinessRuleError, NotFoundError
from .journal import Incident, Postponement
from .weather import WeatherObservation

_ACTIVE = tuple(s.value for s in ACTIVE_STATUSES)
_ACTIVE_SQL = ",".join("?" * len(_ACTIVE))      # только плейсхолдеры, значения передаются параметрами
_FINISHED = (LaunchStatus.LAUNCHED.value, LaunchStatus.FAILED.value)


class Repository:
    def __init__(self, db: Database):
        self._db = db

    #: Допустимые (таблица, колонка номера, колонка родителя) для составных ключей зависимых сущностей.
    _SEQ_KEYS = frozenset({("pad_maintenance", "maint_no", "pad_id"), ("stage_log", "seq_no", "launch_id"),
                           ("weather_observation", "obs_no", "launch_id"),
                           ("postponement", "postpone_no", "launch_id")})

    def _next_no(self, table_col: tuple[str, str, str], parent_id: int) -> int:
        """Следующий порядковый номер зависимой сущности (часть составного PK)."""
        if table_col not in self._SEQ_KEYS:        # имена нельзя передать параметром «?» — только белый список
            raise ValueError(f"Недопустимый ключ последовательности: {table_col}")
        table, col, parent_col = table_col
        value = self._db.scalar(f"SELECT COALESCE(MAX({col}), 0) + 1 FROM {table} WHERE {parent_col} = ?",
                                (parent_id,))
        return int(value)


# ---------------------------------------------------------------- персонал
class StaffRepository(Repository):
    def add(self, s: Staff) -> Staff:
        if self.find_by_login(s.login):
            raise BusinessRuleError(f"Логин «{s.login}» уже занят")
        cur = self._db.execute(
            "INSERT INTO staff(login, full_name, role, password_hash, password_salt, is_active) VALUES (?,?,?,?,?,?)",
            (s.login, s.full_name, s.role.value, s.password_hash, s.password_salt, int(s.is_active)))
        s.assign_id(cur.lastrowid)
        return s

    def update(self, s: Staff) -> None:
        self._db.execute("UPDATE staff SET full_name=?, role=?, is_active=? WHERE staff_id=?",
                         (s.full_name, s.role.value, int(s.is_active), s.id))

    def find_by_login(self, login: str) -> Staff | None:
        row = self._db.query_one("SELECT * FROM staff WHERE login = ?", (login.strip().lower(),))
        return self._row(row) if row else None

    def get(self, staff_id: int) -> Staff:
        row = self._db.query_one("SELECT * FROM staff WHERE staff_id = ?", (staff_id,))
        if not row:
            raise NotFoundError(f"Сотрудник №{staff_id} не найден")
        return self._row(row)

    def list_all(self) -> list[Staff]:
        return [self._row(r) for r in self._db.query("SELECT * FROM staff ORDER BY staff_id")]

    def count(self) -> int:
        return self._db.scalar("SELECT COUNT(*) FROM staff")

    @staticmethod
    def _row(r) -> Staff:
        return Staff(r["login"], r["full_name"], Role(r["role"]), r["password_hash"], r["password_salt"],
                     bool(r["is_active"]), r["staff_id"])


# ---------------------------------------------------------------- типы РН
class VehicleTypeRepository(Repository):
    def add(self, v: VehicleType) -> VehicleType:
        if self._db.scalar("SELECT 1 FROM vehicle_type WHERE name = ?", (v.name,)):
            raise BusinessRuleError(f"Тип РН «{v.name}» уже существует")
        cur = self._db.execute("INSERT INTO vehicle_type(name, vehicle_class, stages_count) VALUES (?,?,?)",
                               (v.name, v.vehicle_class.value, v.stages_count))
        v.assign_id(cur.lastrowid)
        return v

    def get(self, vehicle_type_id: int) -> VehicleType:
        row = self._db.query_one("SELECT * FROM vehicle_type WHERE vehicle_type_id = ?", (vehicle_type_id,))
        if not row:
            raise NotFoundError(f"Тип РН №{vehicle_type_id} не найден")
        return self._row(row)

    def list_all(self) -> list[VehicleType]:
        return [self._row(r) for r in self._db.query("SELECT * FROM vehicle_type ORDER BY name")]

    @staticmethod
    def _row(r) -> VehicleType:
        return VehicleType(r["name"], VehicleClass(r["vehicle_class"]), r["stages_count"], r["vehicle_type_id"])


# ---------------------------------------------------------------- площадки
class LaunchPadRepository(Repository):
    def add(self, p: LaunchPad) -> LaunchPad:
        if self._db.scalar("SELECT 1 FROM launch_pad WHERE code = ?", (p.code,)):
            raise BusinessRuleError(f"Площадка с кодом {p.code} уже существует")
        cur = self._db.execute(
            "INSERT INTO launch_pad(code, name, max_concurrent, cycle_limit, hours_limit, is_faulty) "
            "VALUES (?,?,?,?,?,?)",
            (p.code, p.name, p.max_concurrent, p.cycle_limit, p.hours_limit, int(p.is_faulty)))
        p.assign_id(cur.lastrowid)
        return p

    def update(self, p: LaunchPad) -> None:
        self._db.execute("UPDATE launch_pad SET is_faulty = ? WHERE pad_id = ?", (int(p.is_faulty), p.id))

    def get(self, pad_id: int) -> LaunchPad:
        row = self._db.query_one("SELECT * FROM launch_pad WHERE pad_id = ?", (pad_id,))
        if not row:
            raise NotFoundError(f"Площадка №{pad_id} не найдена")
        return self._row(row)

    def list_all(self) -> list[LaunchPad]:
        return [self._row(r) for r in self._db.query("SELECT * FROM launch_pad ORDER BY code")]

    def usage(self, pad_id: int) -> PadUsage:
        """Вычислить циклы и моточасы с последнего закрытого ТО, открытое ТО, активные подготовки."""
        last_closed = self._db.scalar(
            "SELECT MAX(closed_at) FROM pad_maintenance WHERE pad_id = ? AND closed_at IS NOT NULL", (pad_id,))
        row = self._db.query_one(
            "SELECT COUNT(*) AS cycles, COALESCE(SUM(l.pad_hours), 0) AS hours FROM launch l "
            "JOIN stage_log s ON s.launch_id = l.launch_id AND s.status = l.status "
            "WHERE l.pad_id = ? AND l.status IN (?, ?) AND (? IS NULL OR s.changed_at > ?)",
            (pad_id, *_FINISHED, last_closed, last_closed))
        has_open = bool(self._db.scalar(
            "SELECT 1 FROM pad_maintenance WHERE pad_id = ? AND closed_at IS NULL", (pad_id,)))
        active = self._db.scalar(
            f"SELECT COUNT(*) FROM launch WHERE pad_id = ? AND status IN ({_ACTIVE_SQL})", (pad_id, *_ACTIVE))
        return PadUsage(row["cycles"], float(row["hours"]), has_open, active)

    # --- обслуживание
    def open_maintenance(self, pad_id: int, kind: MaintenanceKind, opened_at: datetime, notes: str,
                         staff_id: int) -> MaintenanceRecord:
        no = self._next_no(("pad_maintenance", "maint_no", "pad_id"), pad_id)
        self._db.execute("INSERT INTO pad_maintenance(pad_id, maint_no, kind, opened_at, notes, staff_id) "
                         "VALUES (?,?,?,?,?,?)", (pad_id, no, kind.value, dt_to_db(opened_at), notes, staff_id))
        return MaintenanceRecord(pad_id, no, kind, opened_at, None, notes, staff_id)

    def close_maintenance(self, pad_id: int, closed_at: datetime) -> None:
        self._db.execute("UPDATE pad_maintenance SET closed_at = ? WHERE pad_id = ? AND closed_at IS NULL",
                         (dt_to_db(closed_at), pad_id))

    def open_maintenance_record(self, pad_id: int) -> MaintenanceRecord | None:
        r = self._db.query_one("SELECT * FROM pad_maintenance WHERE pad_id = ? AND closed_at IS NULL", (pad_id,))
        return self._maint(r) if r else None

    def maintenance_history(self, pad_id: int) -> list[MaintenanceRecord]:
        return [self._maint(r) for r in self._db.query(
            "SELECT * FROM pad_maintenance WHERE pad_id = ? ORDER BY maint_no", (pad_id,))]

    @staticmethod
    def _maint(r) -> MaintenanceRecord:
        return MaintenanceRecord(r["pad_id"], r["maint_no"], MaintenanceKind(r["kind"]), dt_from_db(r["opened_at"]),
                                 dt_from_db(r["closed_at"]), r["notes"], r["staff_id"])

    @staticmethod
    def _row(r) -> LaunchPad:
        return LaunchPad(r["code"], r["name"], r["max_concurrent"], r["cycle_limit"], r["hours_limit"],
                         bool(r["is_faulty"]), r["pad_id"])


# ---------------------------------------------------------------- пуски
class LaunchRepository(Repository):
    def add(self, launch: Launch) -> Launch:
        cur = self._db.execute(
            "INSERT INTO launch(vehicle_type_id, pad_id, created_by, payload, target_orbit, window_start, "
            "window_end, status, pad_hours, cancel_reason) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (launch.vehicle_type_id, launch.pad_id, launch.created_by, launch.payload, launch.target_orbit,
             dt_to_db(launch.window.start), dt_to_db(launch.window.end), launch.status.value, launch.pad_hours,
             launch.cancel_reason))
        launch.assign_id(cur.lastrowid)
        return launch

    def update(self, launch: Launch) -> None:
        self._db.execute("UPDATE launch SET window_start=?, window_end=?, status=?, pad_hours=?, cancel_reason=? WHERE launch_id=?",
                         (dt_to_db(launch.window.start), dt_to_db(launch.window.end), launch.status.value,
                          launch.pad_hours, launch.cancel_reason, launch.id))

    def get(self, launch_id: int) -> Launch:
        row = self._db.query_one("SELECT * FROM launch WHERE launch_id = ?", (launch_id,))
        if not row:
            raise NotFoundError(f"Пуск №{launch_id} не найден")
        return self._row(row)

    def list_all(self, statuses: tuple[LaunchStatus, ...] | None = None) -> list[Launch]:
        if not statuses:
            rows = self._db.query("SELECT * FROM launch ORDER BY window_start")
        else:
            marks = ",".join("?" * len(statuses))
            rows = self._db.query(f"SELECT * FROM launch WHERE status IN ({marks}) ORDER BY window_start",
                                  tuple(s.value for s in statuses))
        return [self._row(r) for r in rows]

    def count_active_on_pad(self, pad_id: int) -> int:
        return self._db.scalar(f"SELECT COUNT(*) FROM launch WHERE pad_id = ? AND status IN ({_ACTIVE_SQL})",
                               (pad_id, *_ACTIVE))

    # --- журнал статусов
    def log_status(self, launch_id: int, status: LaunchStatus, changed_at: datetime, staff_id: int) -> None:
        no = self._next_no(("stage_log", "seq_no", "launch_id"), launch_id)
        self._db.execute("INSERT INTO stage_log(launch_id, seq_no, status, changed_at, staff_id) VALUES (?,?,?,?,?)",
                         (launch_id, no, status.value, dt_to_db(changed_at), staff_id))

    def stage_log(self, launch_id: int) -> list[tuple[int, LaunchStatus, datetime, str]]:
        rows = self._db.query(
            "SELECT s.seq_no, s.status, s.changed_at, st.full_name FROM stage_log s "
            "JOIN staff st ON st.staff_id = s.staff_id WHERE s.launch_id = ? ORDER BY s.seq_no", (launch_id,))
        return [(r["seq_no"], LaunchStatus(r["status"]), dt_from_db(r["changed_at"]), r["full_name"]) for r in rows]

    # --- выборки для отчётов
    def stats_by_vehicle(self, date_from: datetime, date_to: datetime) -> list[dict]:
        """Количество пусков по типам РН и статусам (по началу стартового окна)."""
        rows = self._db.query(
            "SELECT v.name, v.vehicle_class, l.status, COUNT(*) AS n FROM launch l "
            "JOIN vehicle_type v ON v.vehicle_type_id = l.vehicle_type_id "
            "WHERE l.window_start BETWEEN ? AND ? GROUP BY v.name, v.vehicle_class, l.status ORDER BY v.name",
            (dt_to_db(date_from), dt_to_db(date_to)))
        return [dict(r) for r in rows]

    def occupancy(self, pad_id: int) -> list[dict]:
        """Интервалы занятости площадки: от начала сборки до финального статуса (или по сей день)."""
        rows = self._db.query(
            "SELECT l.launch_id, l.status, "
            "(SELECT MIN(s.changed_at) FROM stage_log s WHERE s.launch_id = l.launch_id AND s.status = ?) AS started, "
            "(SELECT MAX(s.changed_at) FROM stage_log s WHERE s.launch_id = l.launch_id AND s.status IN (?,?,?)) "
            "AS finished FROM launch l WHERE l.pad_id = ?",
            (LaunchStatus.ASSEMBLY.value, LaunchStatus.LAUNCHED.value, LaunchStatus.FAILED.value,
             LaunchStatus.CANCELLED.value, pad_id))
        return [{"launch_id": r["launch_id"], "status": LaunchStatus(r["status"]),
                 "started": dt_from_db(r["started"]), "finished": dt_from_db(r["finished"])} for r in rows]

    @staticmethod
    def _row(r) -> Launch:
        return Launch(r["vehicle_type_id"], r["pad_id"], r["created_by"], r["payload"], r["target_orbit"],
                      LaunchWindow(dt_from_db(r["window_start"]), dt_from_db(r["window_end"])),
                      LaunchStatus(r["status"]), r["pad_hours"], r["cancel_reason"], r["launch_id"])


# ---------------------------------------------------------------- топливо
class FuelRepository(Repository):
    def add_batch(self, b: FuelBatch) -> FuelBatch:
        if self._db.scalar("SELECT 1 FROM fuel_batch WHERE batch_no = ?", (b.batch_no,)):
            raise BusinessRuleError(f"Партия {b.batch_no} уже зарегистрирована")
        cur = self._db.execute(
            "INSERT INTO fuel_batch(batch_no, component, grade, initial_volume, produced_on, expires_on) "
            "VALUES (?,?,?,?,?,?)", (b.batch_no, b.component.value, b.grade, b.initial_volume,
                                     date_to_db(b.produced_on), date_to_db(b.expires_on)))
        b.assign_id(cur.lastrowid)
        return b

    def get_batch(self, batch_id: int) -> FuelBatch:
        row = self._db.query_one("SELECT * FROM fuel_batch WHERE batch_id = ?", (batch_id,))
        if not row:
            raise NotFoundError(f"Партия №{batch_id} не найдена")
        return self._batch(row)

    def list_batches(self) -> list[FuelBatch]:
        return [self._batch(r) for r in self._db.query("SELECT * FROM fuel_batch ORDER BY expires_on")]

    def used_volume(self, batch_id: int) -> float:
        return float(self._db.scalar("SELECT COALESCE(SUM(volume), 0) FROM fuel_consumption WHERE batch_id = ?",
                                     (batch_id,)))

    def add_consumption(self, c: FuelConsumption) -> None:
        self._db.execute("INSERT INTO fuel_consumption(launch_id, batch_id, volume, consumed_at, staff_id) "
                         "VALUES (?,?,?,?,?)",
                         (c.launch_id, c.batch_id, c.volume, dt_to_db(c.consumed_at), c.staff_id))

    def loaded_by_component(self, launch_id: int) -> dict[FuelComponent, float]:
        rows = self._db.query(
            "SELECT b.component, SUM(c.volume) AS v FROM fuel_consumption c JOIN fuel_batch b "
            "ON b.batch_id = c.batch_id WHERE c.launch_id = ? GROUP BY b.component", (launch_id,))
        return {FuelComponent(r["component"]): float(r["v"]) for r in rows}

    def batch_usage(self, date_from: datetime, date_to: datetime) -> list[dict]:
        """Для отчёта: расход по каждой партии за период и всего."""
        rows = self._db.query(
            "SELECT b.batch_id, b.batch_no, b.component, b.grade, b.initial_volume, b.expires_on, "
            "COALESCE(SUM(CASE WHEN c.consumed_at BETWEEN ? AND ? THEN c.volume END), 0) AS used_period, "
            "COALESCE(SUM(c.volume), 0) AS used_total, "
            "COUNT(DISTINCT CASE WHEN c.consumed_at BETWEEN ? AND ? THEN c.launch_id END) AS launches "
            "FROM fuel_batch b LEFT JOIN fuel_consumption c ON c.batch_id = b.batch_id "
            "GROUP BY b.batch_id ORDER BY b.component, b.batch_no",
            (dt_to_db(date_from), dt_to_db(date_to), dt_to_db(date_from), dt_to_db(date_to)))
        return [dict(r) for r in rows]

    @staticmethod
    def _batch(r) -> FuelBatch:
        return FuelBatch(r["batch_no"], FuelComponent(r["component"]), r["grade"], r["initial_volume"],
                         date_from_db(r["produced_on"]), date_from_db(r["expires_on"]), r["batch_id"])


# ---------------------------------------------------------------- метео
class WeatherRepository(Repository):
    def add(self, o: WeatherObservation) -> int:
        no = self._next_no(("weather_observation", "obs_no", "launch_id"), o.launch_id)
        self._db.execute(
            "INSERT INTO weather_observation(launch_id, obs_no, observed_at, ground_wind, altitude_wind, "
            "temperature, cloud_base, thunderstorm, staff_id) VALUES (?,?,?,?,?,?,?,?,?)",
            (o.launch_id, no, dt_to_db(o.observed_at), o.ground_wind, o.altitude_wind, o.temperature,
             o.cloud_base, int(o.thunderstorm), o.staff_id))
        return no

    def last_for_launch(self, launch_id: int) -> WeatherObservation | None:
        r = self._db.query_one("SELECT * FROM weather_observation WHERE launch_id = ? "
                               "ORDER BY observed_at DESC, obs_no DESC LIMIT 1", (launch_id,))
        if not r:
            return None
        return WeatherObservation(r["launch_id"], dt_from_db(r["observed_at"]), r["ground_wind"], r["altitude_wind"],
                                  r["temperature"], r["cloud_base"], bool(r["thunderstorm"]), r["staff_id"],
                                  r["obs_no"])


# ---------------------------------------------------------------- телеметрия
class TelemetryRepository(Repository):
    def add_channel(self, ch: TelemetryChannel) -> TelemetryChannel:
        if self._db.scalar("SELECT 1 FROM telemetry_channel WHERE channel_no = ?", (ch.channel_no,)):
            raise BusinessRuleError(f"Канал {ch.channel_no} уже существует")
        cur = self._db.execute(
            "INSERT INTO telemetry_channel(channel_no, vehicle_system, parameter, frequency_hz) VALUES (?,?,?,?)",
            (ch.channel_no, ch.vehicle_system, ch.parameter, ch.frequency_hz))
        ch.assign_id(cur.lastrowid)
        return ch

    def get_channel(self, channel_id: int) -> TelemetryChannel:
        r = self._db.query_one("SELECT * FROM telemetry_channel WHERE channel_id = ?", (channel_id,))
        if not r:
            raise NotFoundError(f"Канал №{channel_id} не найден")
        return self._channel(r)

    def list_channels(self) -> list[TelemetryChannel]:
        return [self._channel(r) for r in self._db.query("SELECT * FROM telemetry_channel ORDER BY channel_no")]

    @staticmethod
    def _channel(r) -> TelemetryChannel:
        return TelemetryChannel(r["channel_no"], r["vehicle_system"], r["parameter"], r["frequency_hz"],
                                r["channel_id"])


# ---------------------------------------------------------------- журналы
class JournalRepository(Repository):
    def add_incident(self, i: Incident) -> None:
        self._db.execute("INSERT INTO incident(launch_id, channel_id, occurred_at, description, measures, staff_id) "
                         "VALUES (?,?,?,?,?,?)", (i.launch_id, i.channel_id, dt_to_db(i.recorded_at),
                                                  i.description, i.measures, i.staff_id))

    def add_postponement(self, p: Postponement) -> None:
        no = self._next_no(("postponement", "postpone_no", "launch_id"), p.launch_id)
        self._db.execute(
            "INSERT INTO postponement(launch_id, postpone_no, reason, old_start, old_end, new_start, new_end, "
            "comment, staff_id, recorded_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (p.launch_id, no, p.reason.value, dt_to_db(p.old_window.start), dt_to_db(p.old_window.end),
             dt_to_db(p.new_window.start), dt_to_db(p.new_window.end), p.comment, p.staff_id,
             dt_to_db(p.recorded_at)))

    def for_launch(self, launch_id: int) -> list:
        """Все записи журнала пуска (НС и переносы), по времени."""
        return sorted(self.incidents(launch_id=launch_id) + self.postponements(launch_id=launch_id),
                      key=lambda rec: rec.recorded_at)

    def incidents(self, launch_id: int | None = None, date_from: datetime | None = None,
                  date_to: datetime | None = None) -> list[Incident]:
        rows = self._db.query(
            "SELECT * FROM incident WHERE (? IS NULL OR launch_id = ?) AND (? IS NULL OR occurred_at >= ?) "
            "AND (? IS NULL OR occurred_at <= ?) ORDER BY occurred_at",
            (launch_id, launch_id, dt_to_db(date_from), dt_to_db(date_from), dt_to_db(date_to), dt_to_db(date_to)))
        return [Incident(r["launch_id"], r["channel_id"], dt_from_db(r["occurred_at"]), r["description"],
                         r["measures"], r["staff_id"]) for r in rows]

    def postponements(self, launch_id: int | None = None, date_from: datetime | None = None,
                      date_to: datetime | None = None) -> list[Postponement]:
        rows = self._db.query(
            "SELECT * FROM postponement WHERE (? IS NULL OR launch_id = ?) AND (? IS NULL OR recorded_at >= ?) "
            "AND (? IS NULL OR recorded_at <= ?) ORDER BY recorded_at",
            (launch_id, launch_id, dt_to_db(date_from), dt_to_db(date_from), dt_to_db(date_to), dt_to_db(date_to)))
        return [Postponement(r["launch_id"], PostponeReason(r["reason"]),
                             LaunchWindow(dt_from_db(r["old_start"]), dt_from_db(r["old_end"])),
                             LaunchWindow(dt_from_db(r["new_start"]), dt_from_db(r["new_end"])),
                             r["comment"], r["staff_id"], dt_from_db(r["recorded_at"])) for r in rows]

    def postponements_by_vehicle(self, date_from: datetime, date_to: datetime) -> dict[str, int]:
        rows = self._db.query(
            "SELECT v.name, COUNT(*) AS n FROM postponement p JOIN launch l ON l.launch_id = p.launch_id "
            "JOIN vehicle_type v ON v.vehicle_type_id = l.vehicle_type_id "
            "WHERE p.recorded_at BETWEEN ? AND ? GROUP BY v.name", (dt_to_db(date_from), dt_to_db(date_to)))
        return {r["name"]: r["n"] for r in rows}

    def incidents_by_system(self, date_from: datetime, date_to: datetime) -> list[tuple[str, int]]:
        rows = self._db.query(
            "SELECT COALESCE(ch.vehicle_system, 'не определена') AS sys, COUNT(*) AS n FROM incident i "
            "LEFT JOIN telemetry_channel ch ON ch.channel_id = i.channel_id "
            "WHERE i.occurred_at BETWEEN ? AND ? GROUP BY sys ORDER BY n DESC, sys",
            (dt_to_db(date_from), dt_to_db(date_to)))
        return [(r["sys"], r["n"]) for r in rows]


class Repositories:
    """Набор репозиториев над одной БД — передаётся в сервисы."""

    def __init__(self, db: Database):
        self.db = db
        self.staff = StaffRepository(db)
        self.vehicle_types = VehicleTypeRepository(db)
        self.pads = LaunchPadRepository(db)
        self.launches = LaunchRepository(db)
        self.fuel = FuelRepository(db)
        self.weather = WeatherRepository(db)
        self.telemetry = TelemetryRepository(db)
        self.journal = JournalRepository(db)
