"""Сервисы слоя Model — прикладные сценарии системы.

Каждый публичный метод:
1. проверяет права текущей сессии (:meth:`Session.require`);
2. проверяет, что объект относится к космодрому сотрудника (:meth:`Session.ensure_spaceport`, ЛР4);
3. создаёт/загружает сущности (они сами валидируют данные);
4. проверяет бизнес-правила и сохраняет изменения в одной транзакции.

Сервисы не зависят от View и Controller и могут вызываться напрямую
(например, из тестов) — обойти проверки, минуя интерфейс, нельзя.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Callable

from .audit import AuditLogger
from .entities import (FuelBatch, FuelConsumption, Launch, LaunchPad, LaunchWindow, PadUsage, Spaceport, Staff,
                       TelemetryChannel, VehicleType, normalize_comment)
from .enums import FuelComponent, LaunchStatus, MaintenanceKind, Permission, PostponeReason, Role, VehicleClass
from .errors import AuthenticationError, BusinessRuleError, ValidationError
from .forecast import ForecastRecord, ForecastService
from .journal import Incident, Postponement
from .meteo_client import MeteoProvider
from .repositories import Repositories
from .security import PasswordHasher, Session
from .stages import StageContext, ensure_fuel_not_expired, stage_for
from .units import Mass
from .validation import require_enum, require_int, require_text
from .weather import WeatherDecision, WeatherLimits, WeatherObservation

Clock = Callable[[], datetime]


def _now_default() -> datetime:
    return datetime.now().replace(second=0, microsecond=0)


class Service:
    def __init__(self, repos: Repositories, clock: Clock = _now_default):
        self._repos = repos
        self._clock = clock

    def _now(self) -> datetime:
        return self._clock().replace(second=0, microsecond=0)

    def _pad_in_scope(self, session: Session, pad_id: int) -> LaunchPad:
        pad = self._repos.pads.get(pad_id)
        session.ensure_spaceport(pad.spaceport_id)
        return pad

    def _launch_in_scope(self, session: Session, launch_id: int) -> tuple[Launch, LaunchPad]:
        """Пуск и его площадка; пуск относится к космодрому площадки (ЛР4, вариант A)."""
        launch = self._repos.launches.get(launch_id)
        return launch, self._pad_in_scope(session, launch.pad_id)


# ======================================================================= авторизация
class AuthService(Service):
    """Вход в систему с блокировкой после серии неудачных попыток."""

    MAX_FAILED = 5
    LOCK_TIME = timedelta(minutes=15)
    PURGE_AT = 256          # ЛР5, утечка №2: записи старше LOCK_TIME вычищаются, когда их больше PURGE_AT

    def __init__(self, repos: Repositories, clock: Clock = _now_default, audit: AuditLogger | None = None):
        super().__init__(repos, clock)
        self._audit = audit or AuditLogger(repos, clock)
        self._failed: dict[str, tuple[int, datetime]] = {}     # логин → (число ошибок, время последней)

    def login(self, login: str, password: str) -> Session:
        key = (login or "").strip().lower()
        count, last = self._failed.get(key, (0, self._now()))
        if count >= self.MAX_FAILED:
            if self._now() - last < self.LOCK_TIME:
                raise AuthenticationError("Учётная запись временно заблокирована на 15 минут: "
                                          "слишком много неудачных попыток")
            count = 0                                          # блокировка истекла
        staff = self._repos.staff.find_by_login(key) if key else None
        # одинаковое сообщение для «нет логина» и «неверный пароль» — не раскрываем существование логина
        if staff is None or not staff.is_active or not PasswordHasher.verify(password or "", staff.password_hash,
                                                                           staff.password_salt):
            self._failed[key] = (count + 1, self._now())
            if len(self._failed) > self.PURGE_AT:
                self._purge_failed()
            raise AuthenticationError("Неверный логин или пароль")
        self._failed.pop(key, None)
        session = Session(staff, on_denied=self._audit.denied)
        self._audit.record(session, "Вход в систему", "сеанс")
        return session


    def _purge_failed(self) -> None:
        """Счетчики, у которых последняя ошибка старше времени блокировки, уже ни на что не влияют."""
        now = self._now()
        for key in [k for k, (_, last) in self._failed.items() if now - last >= self.LOCK_TIME]:
            del self._failed[key]


class StaffService(Service):
    def create_staff(self, session: Session | None, login: str, full_name: str, role: Role | str,
                     password: str, spaceport_id: int | None = None) -> Staff:
        """Создать сотрудника. Первый сотрудник (руководитель) создаётся без сессии — при инициализации БД."""
        role = require_enum(role, Role, "Роль")
        if session is None:
            if self._repos.staff.count() > 0:
                raise BusinessRuleError("Первичная настройка уже выполнена — войдите в систему")
            if role is not Role.HEAD:
                raise BusinessRuleError("Первым создаётся руководитель центра")
        else:
            session.require(Permission.MANAGE_STAFF)
        PasswordHasher.validate_strength(password)
        pw_hash, salt = PasswordHasher.hash(password)
        staff = Staff(login, full_name, role, pw_hash, salt, spaceport_id=spaceport_id)
        if staff.spaceport_id is not None:
            self._repos.spaceports.get(staff.spaceport_id)
        return self._repos.staff.add(staff)

    def list_staff(self, session: Session) -> list[Staff]:
        session.require(Permission.MANAGE_STAFF)
        return self._repos.staff.list_all()

    def deactivate(self, session: Session, staff_id: int) -> None:
        session.require(Permission.MANAGE_STAFF)
        if staff_id == session.staff_id:
            raise BusinessRuleError("Нельзя заблокировать собственную учётную запись")
        staff = self._repos.staff.get(staff_id)
        staff.deactivate()
        self._repos.staff.update(staff)


# ======================================================================= справочники
class ReferenceService(Service):
    """Космодромы, типы РН, стартовые площадки, каналы телеметрии."""

    def add_spaceport(self, session: Session, code: str, name: str, region: str = "") -> Spaceport:
        session.require(Permission.MANAGE_SPACEPORTS)
        return self._repos.spaceports.add(Spaceport(code, name, region))

    def list_spaceports(self, session: Session) -> list[Spaceport]:
        """Сотрудник космодрома видит только свой космодром, роли центра — все."""
        return [sp for sp in self._repos.spaceports.list_all() if session.is_global or sp.id == session.spaceport_id]

    def add_vehicle_type(self, session: Session, name: str, vehicle_class: VehicleClass | str,
                         stages_count: int) -> VehicleType:
        session.require(Permission.MANAGE_PADS)
        return self._repos.vehicle_types.add(VehicleType(name, vehicle_class, stages_count))

    def list_vehicle_types(self, session: Session) -> list[VehicleType]:
        session.require(Permission.VIEW_SCHEDULE)
        return self._repos.vehicle_types.list_all()

    def add_pad(self, session: Session, code: str, name: str, max_concurrent: int, cycle_limit: int,
                hours_limit: float, spaceport_id: int | None = None) -> LaunchPad:
        session.require(Permission.MANAGE_PADS)
        spaceport_id = session.scope(spaceport_id)
        pad = LaunchPad(code, name, max_concurrent, cycle_limit, hours_limit, spaceport_id=spaceport_id)
        self._repos.spaceports.get(pad.spaceport_id)
        return self._repos.pads.add(pad)

    def list_pads_brief(self, session: Session, spaceport_id: int | None = None) -> list[LaunchPad]:
        """Площадки без показателей загрузки — для подписей в списках (ЛР5: usage не нужен, это 3 запроса на площадку)."""
        session.require(Permission.VIEW_SCHEDULE)
        return self._repos.pads.list_all(session.scope(spaceport_id))

    def list_pads(self, session: Session, spaceport_id: int | None = None) -> list[tuple[LaunchPad, PadUsage]]:
        session.require(Permission.VIEW_SCHEDULE)
        return [(p, self._repos.pads.usage(p.id)) for p in self._repos.pads.list_all(session.scope(spaceport_id))]

    def add_channel(self, session: Session, channel_no: str, vehicle_system: str, parameter: str,
                    frequency_hz: float) -> TelemetryChannel:
        session.require(Permission.MANAGE_CHANNELS)
        return self._repos.telemetry.add_channel(TelemetryChannel(channel_no, vehicle_system, parameter,
                                                                  frequency_hz))

    def list_channels(self, session: Session) -> list[TelemetryChannel]:
        session.require(Permission.MANAGE_CHANNELS, Permission.RECORD_INCIDENT)
        return self._repos.telemetry.list_channels()


# ======================================================================= площадки: ТО
class PadService(Service):
    """Межпусковое обслуживание и неисправности стартовых площадок."""

    def open_maintenance(self, session: Session, pad_id: int, kind: MaintenanceKind | str, notes: str = ""):
        session.require(Permission.MAINTAIN_PADS)
        kind = require_enum(kind, MaintenanceKind, "Вид работ")
        with self._repos.db.transaction():
            self._pad_in_scope(session, pad_id)
            usage = self._repos.pads.usage(pad_id)
            if usage.has_open_maintenance:
                raise BusinessRuleError("На площадке уже идёт обслуживание")
            if usage.active_preparations:
                raise BusinessRuleError(f"На площадке идёт подготовка {usage.active_preparations} пуск(ов) — "
                                        "ТО можно начать только на свободной площадке")
            return self._repos.pads.open_maintenance(pad_id, kind, self._now(), normalize_comment(notes),
                                                     session.staff_id)

    def close_maintenance(self, session: Session, pad_id: int, repaired: bool = True) -> None:
        """Закрыть ТО: счётчики циклов и моточасов обнуляются (вычисляются от даты закрытия)."""
        session.require(Permission.MAINTAIN_PADS)
        with self._repos.db.transaction():
            pad = self._pad_in_scope(session, pad_id)
            if not self._repos.pads.usage(pad_id).has_open_maintenance:
                raise BusinessRuleError("На площадке нет открытого обслуживания")
            self._repos.pads.close_maintenance(pad_id, self._now())
            if repaired and pad.is_faulty:
                pad.mark_faulty(False)
                self._repos.pads.update(pad)

    def mark_faulty(self, session: Session, pad_id: int) -> None:
        session.require(Permission.MAINTAIN_PADS)
        pad = self._pad_in_scope(session, pad_id)
        pad.mark_faulty(True)
        self._repos.pads.update(pad)


# ======================================================================= пуски
@dataclass(frozen=True)
class LaunchCard:
    """Сводка по пуску для отображения (DTO: View не обращается к хранилищу)."""
    launch: Launch
    vehicle: VehicleType
    pad: LaunchPad
    stage_log: list
    fuel_loaded: dict
    journal: list
    last_weather: WeatherObservation | None
    spaceport: Spaceport | None = None
    forecast: ForecastRecord | None = None
    fuel_warnings: tuple[str, ...] = ()


class LaunchService(Service):
    """Регистрация, подготовка по этапам, перенос, отмена, фиксация результата пуска."""

    def __init__(self, repos: Repositories, clock: Clock = _now_default, weather_limits: WeatherLimits | None = None):
        super().__init__(repos, clock)
        self._limits = weather_limits or WeatherLimits()

    def register_launch(self, session: Session, vehicle_type_id: int, pad_id: int, payload: str,
                        target_orbit: str, window_start: datetime, window_end: datetime) -> Launch:
        session.require(Permission.MANAGE_LAUNCHES)
        window = LaunchWindow(window_start, window_end)
        if window.start < self._now():
            raise ValidationError("Стартовое окно не может начинаться в прошлом")
        launch = Launch(vehicle_type_id, pad_id, session.staff_id, payload, target_orbit, window)
        with self._repos.db.transaction():
            self._repos.vehicle_types.get(vehicle_type_id)
            pad = self._pad_in_scope(session, pad_id)
            pad.ensure_can_accept(self._repos.pads.usage(pad_id))
            self._repos.launches.add(launch)
            self._repos.launches.log_status(launch.id, launch.status, self._now(), session.staff_id)
        return launch

    def list_launches(self, session: Session, statuses: tuple[LaunchStatus, ...] | None = None,
                      spaceport_id: int | None = None, vehicle_type_id: int | None = None) -> list[Launch]:
        """Расписание и поиск пусков: фильтры по космодрому, типу носителя и статусу выполняет SQL."""
        session.require(Permission.VIEW_SCHEDULE)
        if vehicle_type_id is not None:
            vehicle_type_id = require_int(vehicle_type_id, "Тип РН", min_value=1)
        if statuses:
            statuses = tuple(require_enum(s, LaunchStatus, "Статус") for s in statuses)
        return self._repos.launches.list_all(statuses, session.scope(spaceport_id), vehicle_type_id)

    def launch_card(self, session: Session, launch_id: int) -> LaunchCard:
        session.require(Permission.VIEW_SCHEDULE)
        r = self._repos
        launch, pad = self._launch_in_scope(session, launch_id)
        return LaunchCard(launch, r.vehicle_types.get(launch.vehicle_type_id), pad,
                          r.launches.stage_log(launch_id), r.fuel.loaded_by_component(launch_id),
                          r.journal.for_launch(launch_id), r.weather.last_for_launch(launch_id),
                          r.spaceports.get(pad.spaceport_id), r.forecasts.latest(launch.id, launch.window.start),
                          tuple(self._fuel_warnings(launch)))

    def _fuel_warnings(self, launch: Launch) -> list[str]:
        """Партии в баке, срок которых истекает до конца стартового окна (П8)."""
        end = launch.window.end.date()
        return [f"Партия {b.batch_no} годна до {b.expires_on:%d.%m.%Y}, окно заканчивается {end:%d.%m.%Y}"
                for b in self._repos.fuel.loaded_batches(launch.id) if b.is_expired(end)]

    def fuel_warnings(self, session: Session, launch_id: int) -> list[str]:
        session.require(Permission.VIEW_SCHEDULE, Permission.POSTPONE_LAUNCH)
        launch, _ = self._launch_in_scope(session, launch_id)
        return self._fuel_warnings(launch)

    def next_stage_title(self, launch: Launch) -> str:
        return stage_for(launch.status).title

    def advance_stage(self, session: Session, launch_id: int) -> LaunchStatus:
        """Завершить текущий этап подготовки и перейти к следующему."""
        with self._repos.db.transaction():
            launch, pad = self._launch_in_scope(session, launch_id)
            stage = stage_for(launch.status)
            session.require(stage.permission)
            ctx = StageContext(
                launch=launch, pad=pad,
                pad_usage=self._repos.pads.usage(launch.pad_id), now=self._now(),
                fuel_loaded=self._repos.fuel.loaded_by_component(launch_id),
                last_weather=self._repos.weather.last_for_launch(launch_id), weather_limits=self._limits,
                loaded_batches=self._repos.fuel.loaded_batches(launch_id))
            stage.check_completion(ctx)
            new_status = launch.advance()
            self._repos.launches.update(launch)
            self._repos.launches.log_status(launch_id, new_status, self._now(), session.staff_id)
        return new_status

    def postpone(self, session: Session, launch_id: int, reason: PostponeReason | str, new_start: datetime,
                 new_end: datetime, comment: str = "") -> Postponement:
        reason = require_enum(reason, PostponeReason, "Причина переноса")
        # метеоролог вправе переносить только по метеоусловиям
        if reason is PostponeReason.WEATHER:
            session.require(Permission.POSTPONE_LAUNCH)
        else:
            session.require(Permission.MANAGE_LAUNCHES)
        window = LaunchWindow(new_start, new_end)
        if window.start < self._now():
            raise ValidationError("Новое стартовое окно не может начинаться в прошлом")
        with self._repos.db.transaction():
            launch, _ = self._launch_in_scope(session, launch_id)
            record = launch.postpone(window, reason, comment, session.staff_id, self._now())
            self._repos.launches.update(launch)
            self._repos.journal.add_postponement(record)
        return record

    def cancel(self, session: Session, launch_id: int, comment: str) -> LaunchStatus:
        session.require(Permission.MANAGE_LAUNCHES)
        with self._repos.db.transaction():
            launch, _ = self._launch_in_scope(session, launch_id)
            launch.cancel(comment)
            self._repos.launches.update(launch)
            self._repos.launches.log_status(launch_id, launch.status, self._now(), session.staff_id)
        return launch.status

    def record_result(self, session: Session, launch_id: int, success: bool, pad_hours: float) -> LaunchStatus:
        """Зафиксировать результат: +1 цикл площадки и моточасы (учитываются при расчёте ТО)."""
        session.require(Permission.RECORD_RESULT)
        with self._repos.db.transaction():
            launch, pad = self._launch_in_scope(session, launch_id)
            pad.ensure_operational(self._repos.pads.usage(pad.id))
            ensure_fuel_not_expired(self._repos.fuel.loaded_batches(launch_id), self._now().date())
            status = launch.complete(bool(success), pad_hours, self._now())
            self._repos.launches.update(launch)
            self._repos.launches.log_status(launch_id, status, self._now(), session.staff_id)
        return status


# ======================================================================= топливо
class FuelService(Service):
    """Учет топлива на складах космодромов, масса с точностью 0,1 кг (ЛР4, Т5)."""

    def receive_batch(self, session: Session, batch_no: str, component: FuelComponent | str, grade: str,
                      mass_kg, produced_on: date, expires_on: date, spaceport_id: int | None = None) -> FuelBatch:
        session.require(Permission.MANAGE_FUEL)
        batch = FuelBatch(batch_no, component, grade, Mass.from_kg(mass_kg, "Масса партии, кг"), produced_on,
                          expires_on, spaceport_id=session.scope(spaceport_id))
        if batch.is_expired(self._now().date()):
            raise BusinessRuleError("Нельзя принять партию с истёкшим сроком годности")
        if produced_on > self._now().date():
            raise ValidationError("Дата изготовления не может быть в будущем")
        self._repos.spaceports.get(batch.spaceport_id)
        return self._repos.fuel.add_batch(batch)

    def list_batches(self, session: Session, spaceport_id: int | None = None) -> list[tuple[FuelBatch, Mass]]:
        """Партии с вычисленным остатком."""
        session.require(Permission.MANAGE_FUEL, Permission.VIEW_REPORTS)
        used = self._repos.fuel.used_by_batch()            # один запрос вместо запроса на каждую партию
        return [(b, b.remaining(used.get(b.id, Mass.zero())))
                for b in self._repos.fuel.list_batches(session.scope(spaceport_id))]

    def refuel(self, session: Session, launch_id: int, batch_id: int, mass_kg) -> Mass:
        """Заправка РН из партии: только на этапе «Заправка»; возвращает остаток партии."""
        session.require(Permission.MANAGE_FUEL)
        require_int(batch_id, "Партия", min_value=1)
        mass = Mass.from_kg(mass_kg, "Масса заправки, кг")
        with self._repos.db.transaction():
            launch, pad = self._launch_in_scope(session, launch_id)
            if launch.status is not LaunchStatus.FUELING:
                raise BusinessRuleError(f"Заправка возможна только на этапе «Заправка» "
                                        f"(текущий статус — «{launch.status.title}»)")
            pad.ensure_operational(self._repos.pads.usage(pad.id))
            batch = self._repos.fuel.get_batch(batch_id)
            if batch.spaceport_id != pad.spaceport_id:
                raise BusinessRuleError(f"Партия {batch.batch_no} хранится на складе другого космодрома")
            used = self._repos.fuel.used_mass(batch_id)
            mass = batch.ensure_can_withdraw(mass, used, self._now().date(), batch.component,
                                             valid_until=launch.window.end.date())
            self._repos.fuel.add_consumption(
                FuelConsumption(launch_id, batch_id, mass, self._now(), session.staff_id))
        return batch.remaining(used + mass)


# ======================================================================= метео
class WeatherService(Service):
    def __init__(self, repos: Repositories, clock: Clock = _now_default, limits: WeatherLimits | None = None):
        super().__init__(repos, clock)
        self._limits = limits or WeatherLimits()

    @property
    def limits(self) -> WeatherLimits:
        return self._limits

    def record_observation(self, session: Session, launch_id: int, ground_wind: float, altitude_wind: float,
                           temperature: float, cloud_base: float, thunderstorm: bool) -> WeatherDecision:
        session.require(Permission.RECORD_WEATHER)
        obs = WeatherObservation(launch_id, self._now(), ground_wind, altitude_wind, temperature, cloud_base,
                                 thunderstorm, session.staff_id)
        launch, _ = self._launch_in_scope(session, launch_id)
        if launch.status.is_final:
            raise BusinessRuleError(f"Пуск в статусе «{launch.status.title}» — метеоконтроль не требуется")
        self._repos.weather.add(obs)
        reasons = tuple(self._limits.violations(obs))
        return WeatherDecision(allowed=not reasons, reasons=reasons)


# ======================================================================= телеметрия
class TelemetryService(Service):
    def register_incident(self, session: Session, launch_id: int, channel_id: int | None, description: str,
                          measures: str) -> Incident:
        """Зафиксировать НС (потерю сигнала) — только для пуска на этапе подготовки или пуска."""
        session.require(Permission.RECORD_INCIDENT)
        launch, _ = self._launch_in_scope(session, launch_id)
        if launch.status in (LaunchStatus.PLANNED, LaunchStatus.CANCELLED):
            raise BusinessRuleError("Телеметрия не ведётся для пуска в статусе " f"«{launch.status.title}»")
        if channel_id is not None:
            self._repos.telemetry.get_channel(channel_id)
        incident = Incident(launch_id, channel_id, self._now(), description, measures, session.staff_id)
        self._repos.journal.add_incident(incident)
        return incident


# ======================================================================= журналы (ЛР4)
@dataclass(frozen=True)
class JournalRow:
    """Запись журнала для просмотра: только номер пуска, тип РН и космодром (П11)."""
    record: Incident | Postponement
    launch_id: int
    vehicle: str
    spaceport: str


class JournalService(Service):
    """Журналы НС и переносов: руководители и инспектор безопасности (Т3.1, Т6.2)."""

    def __init__(self, repos: Repositories, clock: Clock = _now_default, audit: AuditLogger | None = None):
        super().__init__(repos, clock)
        self._audit = audit or AuditLogger(repos, clock)

    def _rows(self, records) -> list[JournalRow]:
        labels = self._repos.launches.labels()
        return [JournalRow(r, r.launch_id, *labels.get(r.launch_id, ("?", "?"))) for r in records]

    @staticmethod
    def _filter_text(date_from, date_to, spaceport_id, extra: str = "") -> str:
        parts = [f"период {date_from:%d.%m.%Y}–{date_to:%d.%m.%Y}",
                 f"космодром №{spaceport_id}" if spaceport_id else "все космодромы"]
        return ", ".join(parts + ([extra] if extra else []))

    def incidents(self, session: Session, date_from: datetime, date_to: datetime,
                  spaceport_id: int | None = None) -> list[JournalRow]:
        session.require(Permission.VIEW_JOURNALS)
        scope = session.scope(spaceport_id)
        rows = self._rows(self._repos.journal.incidents(date_from=date_from, date_to=date_to, spaceport_id=scope))
        self._audit.record(session, "Просмотр журнала", "журнал НС", "",
                           self._filter_text(date_from, date_to, scope, f"записей {len(rows)}"))
        return rows

    def postponements(self, session: Session, date_from: datetime, date_to: datetime,
                      reason: PostponeReason | str | None = None, spaceport_id: int | None = None) -> list[JournalRow]:
        session.require(Permission.VIEW_JOURNALS)
        reason = None if reason in (None, "") else require_enum(reason, PostponeReason, "Причина переноса")
        scope = session.scope(spaceport_id)
        rows = self._rows(self._repos.journal.postponements(date_from=date_from, date_to=date_to, spaceport_id=scope,
                                                            reason=reason))
        extra = f"причина «{reason.title}», " if reason else ""
        self._audit.record(session, "Просмотр журнала", "журнал переносов", "",
                           self._filter_text(date_from, date_to, scope, f"{extra}записей {len(rows)}"))
        return rows


class Services:
    """Фасад всех сервисов — единая точка входа для Controller."""

    def __init__(self, repos: Repositories, clock: Clock = _now_default, weather_limits: WeatherLimits | None = None,
                 meteo: MeteoProvider | None = None):
        from .reports import ReportService   # reports зависят от services только через Repositories
        limits = weather_limits or WeatherLimits()
        self.audit = AuditLogger(repos, clock)
        self.auth = AuthService(repos, clock, self.audit)
        self.staff = StaffService(repos, clock)
        self.reference = ReferenceService(repos, clock)
        self.pads = PadService(repos, clock)
        self.launches = LaunchService(repos, clock, limits)
        self.fuel = FuelService(repos, clock)
        self.weather = WeatherService(repos, clock, limits)
        self.forecasts = ForecastService(repos, clock, limits, meteo)
        self.telemetry = TelemetryService(repos, clock)
        self.journal = JournalService(repos, clock, self.audit)
        self.reports = ReportService(repos, clock, self.audit)
