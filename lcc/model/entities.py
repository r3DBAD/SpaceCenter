"""Сущности предметной области (слой Model).

Состояние сущностей инкапсулировано: поля закрыты, доступ — через свойства,
изменение — через методы, которые проверяют инварианты. Сущность не знает
о хранилище и не формирует отчёты (принцип единой ответственности).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .enums import (FINAL_STATUSES, PREPARATION_ORDER, FuelComponent, LaunchStatus, MaintenanceKind, PadState,
                    PostponeReason, Role, VehicleClass)
from .errors import BusinessRuleError, ValidationError
from .validation import (optional_text, require_date, require_datetime, require_enum, require_int,
                         require_number, require_text)


class Entity:
    """Базовый класс: суррогатный идентификатор, присваиваемый хранилищем."""

    def __init__(self, entity_id: int | None = None):
        self._id = entity_id

    @property
    def id(self) -> int | None:
        return self._id

    def assign_id(self, entity_id: int) -> None:
        if self._id is not None:
            raise BusinessRuleError("Идентификатор уже присвоен")
        self._id = require_int(entity_id, "id", min_value=1)

    def __eq__(self, other):
        return type(self) is type(other) and self._id is not None and self._id == other._id

    def __hash__(self):
        return hash((type(self).__name__, self._id))


# ---------------------------------------------------------------- персонал
class Staff(Entity):
    """Сотрудник ЦУП. Хранит только хеш и соль пароля."""

    def __init__(self, login: str, full_name: str, role: Role, password_hash: str, password_salt: str,
                 is_active: bool = True, staff_id: int | None = None):
        super().__init__(staff_id)
        login = require_text(login, "Логин", 32).lower()
        if not login.replace("_", "").replace(".", "").isalnum() or not login.isascii():
            raise ValidationError("Логин может содержать только латинские буквы, цифры, «_» и «.»")
        self._login = login
        self._full_name = require_text(full_name, "ФИО", 120)
        self._role = require_enum(role, Role, "Роль")
        self._password_hash = require_text(password_hash, "Хеш пароля")
        self._password_salt = require_text(password_salt, "Соль")
        self._is_active = bool(is_active)

    login = property(lambda self: self._login)
    full_name = property(lambda self: self._full_name)
    role = property(lambda self: self._role)
    password_hash = property(lambda self: self._password_hash)
    password_salt = property(lambda self: self._password_salt)
    is_active = property(lambda self: self._is_active)

    def deactivate(self) -> None:
        self._is_active = False

    def __repr__(self):  # без хеша пароля — чтобы он не попал в логи
        return f"Staff(id={self.id}, login={self._login!r}, role={self._role.value})"


# ---------------------------------------------------------------- справочники
class VehicleType(Entity):
    """Тип ракеты-носителя."""

    def __init__(self, name: str, vehicle_class: VehicleClass, stages_count: int,
                 vehicle_type_id: int | None = None):
        super().__init__(vehicle_type_id)
        self._name = require_text(name, "Наименование типа РН", 60)
        self._vehicle_class = require_enum(vehicle_class, VehicleClass, "Класс РН")
        self._stages_count = require_int(stages_count, "Число ступеней", min_value=1, max_value=5)

    name = property(lambda self: self._name)
    vehicle_class = property(lambda self: self._vehicle_class)
    stages_count = property(lambda self: self._stages_count)


@dataclass(frozen=True)
class PadUsage:
    """Вычисляемые показатели площадки (не хранятся в БД, см. docs/04_idef1x.md)."""
    cycles_since_maintenance: int = 0
    hours_since_maintenance: float = 0.0
    has_open_maintenance: bool = False
    active_preparations: int = 0


class LaunchPad(Entity):
    """Стартовая площадка: лимит одновременных подготовок и нормативы межпускового ТО."""

    def __init__(self, code: str, name: str, max_concurrent: int, cycle_limit: int, hours_limit: float,
                 is_faulty: bool = False, pad_id: int | None = None):
        super().__init__(pad_id)
        self._code = require_text(code, "Код площадки", 16).upper()
        self._name = require_text(name, "Наименование площадки", 80)
        self._max_concurrent = require_int(max_concurrent, "Макс. одновременных подготовок", min_value=1, max_value=10)
        self._cycle_limit = require_int(cycle_limit, "Норматив циклов до ТО", min_value=1, max_value=100)
        self._hours_limit = require_number(hours_limit, "Норматив моточасов до ТО", min_value=0, strict_min=True)
        self._is_faulty = bool(is_faulty)

    code = property(lambda self: self._code)
    name = property(lambda self: self._name)
    max_concurrent = property(lambda self: self._max_concurrent)
    cycle_limit = property(lambda self: self._cycle_limit)
    hours_limit = property(lambda self: self._hours_limit)
    is_faulty = property(lambda self: self._is_faulty)

    def mark_faulty(self, faulty: bool = True) -> None:
        self._is_faulty = bool(faulty)

    def state(self, usage: PadUsage) -> PadState:
        """Состояние площадки вычисляется по флагу неисправности и показателям использования."""
        if self._is_faulty:
            return PadState.FAULTY
        if usage.has_open_maintenance:
            return PadState.IN_MAINTENANCE
        if (usage.cycles_since_maintenance >= self._cycle_limit
                or usage.hours_since_maintenance >= self._hours_limit):
            return PadState.MAINTENANCE_REQUIRED
        return PadState.READY

    def ensure_can_accept(self, usage: PadUsage) -> None:
        """Проверка перед назначением новой подготовки на площадку."""
        state = self.state(usage)
        if state is not PadState.READY:
            raise BusinessRuleError(f"Площадка {self._code} недоступна: состояние «{state.title}»")
        if usage.active_preparations >= self._max_concurrent:
            raise BusinessRuleError(
                f"Площадка {self._code}: исчерпан лимит одновременных подготовок "
                f"({usage.active_preparations} из {self._max_concurrent})")

    def ensure_operational(self, usage: PadUsage) -> None:
        """Площадка должна быть готова (для транспортировки РН, заправки и пуска)."""
        state = self.state(usage)
        if state is not PadState.READY:
            raise BusinessRuleError(f"Площадка {self._code} недоступна: состояние «{state.title}»")


# ---------------------------------------------------------------- пуск
@dataclass(frozen=True)
class LaunchWindow:
    """Стартовое окно: начало < конец, длительность от 1 минуты до 24 часов."""
    start: datetime
    end: datetime

    MIN = timedelta(minutes=1)
    MAX = timedelta(hours=24)

    def __post_init__(self):
        object.__setattr__(self, "start", require_datetime(self.start, "Начало стартового окна"))
        object.__setattr__(self, "end", require_datetime(self.end, "Конец стартового окна"))
        if self.start >= self.end:
            raise ValidationError("Начало стартового окна должно быть раньше конца")
        if not (self.MIN <= self.end - self.start <= self.MAX):
            raise ValidationError("Длительность стартового окна — от 1 минуты до 24 часов")

    def contains(self, moment: datetime) -> bool:
        return self.start <= moment <= self.end


class Launch(Entity):
    """Пуск ракеты-носителя.

    Статусная модель::

        Запланирован → Сборка на ТК → Транспортировка на СК → Заправка →
        Предстартовый контроль → Готов к пуску → Выполнен | Авария
        (любой нефинальный) → Отменён

    Проверки, специфичные для этапа (топливо, метео, площадка), выполняет
    :mod:`lcc.model.stages`; класс Launch отвечает только за порядок переходов.
    """

    def __init__(self, vehicle_type_id: int, pad_id: int, created_by: int, payload: str, target_orbit: str,
                 window: LaunchWindow, status: LaunchStatus = LaunchStatus.PLANNED,
                 pad_hours: float | None = None, cancel_reason: str = "", launch_id: int | None = None):
        super().__init__(launch_id)
        self._vehicle_type_id = require_int(vehicle_type_id, "Тип РН", min_value=1)
        self._pad_id = require_int(pad_id, "Стартовая площадка", min_value=1)
        self._created_by = require_int(created_by, "Автор", min_value=1)
        self._payload = require_text(payload, "Полезная нагрузка", 120)
        self._target_orbit = require_text(target_orbit, "Целевая орбита", 120)
        if not isinstance(window, LaunchWindow):
            raise ValidationError("Не задано стартовое окно")
        self._window = window
        self._status = require_enum(status, LaunchStatus, "Статус")
        self._pad_hours = None if pad_hours is None else require_number(pad_hours, "Моточасы", min_value=0)
        self._cancel_reason = optional_text(cancel_reason, "Причина отмены", 300)

    vehicle_type_id = property(lambda self: self._vehicle_type_id)
    pad_id = property(lambda self: self._pad_id)
    created_by = property(lambda self: self._created_by)
    payload = property(lambda self: self._payload)
    target_orbit = property(lambda self: self._target_orbit)
    window = property(lambda self: self._window)
    status = property(lambda self: self._status)
    pad_hours = property(lambda self: self._pad_hours)
    cancel_reason = property(lambda self: self._cancel_reason)

    def next_status(self) -> LaunchStatus:
        """Следующий этап подготовки; для «Готов к пуску» и финальных статусов — ошибка."""
        if self._status not in PREPARATION_ORDER or self._status is LaunchStatus.READY:
            raise BusinessRuleError(f"Для статуса «{self._status.title}» нет следующего этапа подготовки")
        return PREPARATION_ORDER[PREPARATION_ORDER.index(self._status) + 1]

    def advance(self) -> LaunchStatus:
        """Перейти строго на следующий этап (пропуск этапов невозможен)."""
        self._status = self.next_status()
        return self._status

    def complete(self, success: bool, pad_hours: float, moment: datetime) -> LaunchStatus:
        """Зафиксировать результат пуска: только из «Готов к пуску» и не раньше открытия окна."""
        if self._status is not LaunchStatus.READY:
            raise BusinessRuleError("Результат можно зафиксировать только для пуска в статусе «Готов к пуску»")
        moment = require_datetime(moment, "Время пуска")
        if moment < self._window.start:
            raise BusinessRuleError("Стартовое окно ещё не открыто")
        self._pad_hours = require_number(pad_hours, "Наработка площадки, моточасы", min_value=0, max_value=1000)
        self._status = LaunchStatus.LAUNCHED if success else LaunchStatus.FAILED
        return self._status

    def cancel(self, reason: str) -> None:
        if self._status in FINAL_STATUSES:
            raise BusinessRuleError(f"Нельзя отменить пуск в статусе «{self._status.title}»")
        self._cancel_reason = require_text(reason, "Причина отмены", 300)
        self._status = LaunchStatus.CANCELLED

    def postpone(self, new_window: LaunchWindow, reason: PostponeReason, comment: str,
                 staff_id: int, moment: datetime) -> "Postponement":
        """Перенести пуск на новое окно. Этап подготовки сохраняется."""
        from .journal import Postponement   # локальный импорт: journal зависит от entities
        if self._status in FINAL_STATUSES:
            raise BusinessRuleError(f"Нельзя перенести пуск в статусе «{self._status.title}»")
        if not isinstance(new_window, LaunchWindow):
            raise ValidationError("Не задано новое стартовое окно")
        if new_window.start <= self._window.start:
            raise BusinessRuleError("Новое стартовое окно должно начинаться позже текущего")
        record = Postponement(self.id, reason, self._window, new_window, comment, staff_id, moment)
        self._window = new_window
        return record


# ---------------------------------------------------------------- топливо
class FuelBatch(Entity):
    """Партия компонента топлива. Остаток не хранится: он передаётся как `used` из хранилища."""

    def __init__(self, batch_no: str, component: FuelComponent, grade: str, initial_volume: float,
                 produced_on: date, expires_on: date, batch_id: int | None = None):
        super().__init__(batch_id)
        self._batch_no = require_text(batch_no, "Номер партии", 32).upper()
        self._component = require_enum(component, FuelComponent, "Компонент")
        self._grade = require_text(grade, "Марка топлива", 40)
        self._initial_volume = require_number(initial_volume, "Объём партии, т", min_value=0, strict_min=True,
                                              max_value=5000)
        self._produced_on = require_date(produced_on, "Дата изготовления")
        self._expires_on = require_date(expires_on, "Срок годности")
        if self._expires_on <= self._produced_on:
            raise ValidationError("Срок годности должен быть позже даты изготовления")

    batch_no = property(lambda self: self._batch_no)
    component = property(lambda self: self._component)
    grade = property(lambda self: self._grade)
    initial_volume = property(lambda self: self._initial_volume)
    produced_on = property(lambda self: self._produced_on)
    expires_on = property(lambda self: self._expires_on)

    def is_expired(self, on: date) -> bool:
        return on > self._expires_on

    def remaining(self, used: float) -> float:
        return round(self._initial_volume - used, 3)

    def ensure_can_withdraw(self, volume: float, used: float, on: date, component: FuelComponent) -> float:
        """Проверить списание `volume` т при уже израсходованных `used` т; вернуть объём."""
        volume = require_number(volume, "Объём заправки, т", min_value=0, strict_min=True)
        if component is not self._component:
            raise BusinessRuleError(f"Партия {self._batch_no} — {self._component.title.lower()}, "
                                    f"а требуется {component.title.lower()}")
        if self.is_expired(on):
            raise BusinessRuleError(f"Партия {self._batch_no} просрочена (срок годности {self._expires_on:%d.%m.%Y})")
        if volume > self.remaining(used) + 1e-9:
            raise BusinessRuleError(f"В партии {self._batch_no} недостаточно топлива: "
                                    f"остаток {self.remaining(used):g} т, требуется {volume:g} т")
        return volume


@dataclass(frozen=True)
class FuelConsumption:
    """Факт расхода топлива на пуск (неизменяемая запись)."""
    launch_id: int
    batch_id: int
    volume: float
    consumed_at: datetime
    staff_id: int


# ---------------------------------------------------------------- телеметрия
class TelemetryChannel(Entity):
    """Канал телеметрии, закреплённый за системой РН."""

    def __init__(self, channel_no: str, vehicle_system: str, parameter: str, frequency_hz: float,
                 channel_id: int | None = None):
        super().__init__(channel_id)
        self._channel_no = require_text(channel_no, "Номер канала", 16).upper()
        self._vehicle_system = require_text(vehicle_system, "Система РН", 60)
        self._parameter = require_text(parameter, "Параметр", 80)
        self._frequency_hz = require_number(frequency_hz, "Частота опроса, Гц", min_value=0, strict_min=True,
                                            max_value=100_000)

    channel_no = property(lambda self: self._channel_no)
    vehicle_system = property(lambda self: self._vehicle_system)
    parameter = property(lambda self: self._parameter)
    frequency_hz = property(lambda self: self._frequency_hz)


@dataclass(frozen=True)
class MaintenanceRecord:
    """Межпусковое обслуживание площадки."""
    pad_id: int
    maint_no: int
    kind: MaintenanceKind
    opened_at: datetime
    closed_at: datetime | None
    notes: str
    staff_id: int

    @property
    def is_open(self) -> bool:
        return self.closed_at is None


def normalize_comment(text) -> str:
    """Общий помощник для необязательных комментариев журналов."""
    return optional_text(text, "Комментарий", 500)
