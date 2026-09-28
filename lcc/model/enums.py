"""Перечисления предметной области: статусы, роли, права, справочные значения."""
from __future__ import annotations

from enum import Enum


class _TitledEnum(Enum):
    """Перечисление с человекочитаемым названием на русском языке."""

    def __new__(cls, value: str, title: str):
        obj = object.__new__(cls)
        obj._value_ = value
        obj.title = title
        return obj

    def __str__(self) -> str:
        return self.title


class LaunchStatus(_TitledEnum):
    """Статусы пуска. Порядок подготовки задаётся :data:`PREPARATION_ORDER`."""
    PLANNED = ("planned", "Запланирован")
    ASSEMBLY = ("assembly", "Сборка на ТК")
    TRANSPORT = ("transport", "Транспортировка на СК")
    FUELING = ("fueling", "Заправка")
    PRELAUNCH_CHECK = ("prelaunch_check", "Предстартовый контроль")
    READY = ("ready", "Готов к пуску")
    LAUNCHED = ("launched", "Выполнен")
    FAILED = ("failed", "Авария")
    CANCELLED = ("cancelled", "Отменён")

    @property
    def is_final(self) -> bool:
        return self in FINAL_STATUSES

    @property
    def is_active_preparation(self) -> bool:
        """Пуск занимает площадку (от сборки до готовности к пуску)."""
        return self in ACTIVE_STATUSES


PREPARATION_ORDER = (
    LaunchStatus.PLANNED, LaunchStatus.ASSEMBLY, LaunchStatus.TRANSPORT,
    LaunchStatus.FUELING, LaunchStatus.PRELAUNCH_CHECK, LaunchStatus.READY,
)
ACTIVE_STATUSES = frozenset(PREPARATION_ORDER[1:])
FINAL_STATUSES = frozenset({LaunchStatus.LAUNCHED, LaunchStatus.FAILED, LaunchStatus.CANCELLED})


class VehicleClass(_TitledEnum):
    LIGHT = ("light", "лёгкий")
    MEDIUM = ("medium", "средний")
    HEAVY = ("heavy", "тяжёлый")


class FuelComponent(_TitledEnum):
    FUEL = ("fuel", "Горючее")
    OXIDIZER = ("oxidizer", "Окислитель")


class PadState(_TitledEnum):
    READY = ("ready", "Готова")
    MAINTENANCE_REQUIRED = ("maintenance_required", "Требует ТО")
    IN_MAINTENANCE = ("in_maintenance", "На обслуживании")
    FAULTY = ("faulty", "Неисправна")


class MaintenanceKind(_TitledEnum):
    INSPECTION = ("inspection", "Осмотр")
    REPAIR = ("repair", "Ремонт")
    REPLACEMENT = ("replacement", "Замена агрегатов")


class PostponeReason(_TitledEnum):
    WEATHER = ("weather", "Метеоусловия")
    TECHNICAL = ("technical", "Техническая неисправность")
    INCIDENT = ("incident", "Нештатная ситуация")
    OTHER = ("other", "Прочее")


class Role(_TitledEnum):
    HEAD = ("head", "Руководитель центра")
    PAD_ENGINEER = ("pad_engineer", "Инженер ТК")
    FUEL_ENGINEER = ("fuel_engineer", "Инженер по заправке")
    METEOROLOGIST = ("meteorologist", "Метеоролог")
    TELEMETRY_OPERATOR = ("telemetry_operator", "Оператор телеметрии")


class Permission(Enum):
    VIEW_SCHEDULE = "view_schedule"
    MANAGE_LAUNCHES = "manage_launches"
    MARK_PAD_STAGES = "mark_pad_stages"
    MANAGE_FUEL = "manage_fuel"
    RECORD_WEATHER = "record_weather"
    POSTPONE_LAUNCH = "postpone_launch"
    MANAGE_CHANNELS = "manage_channels"
    RECORD_INCIDENT = "record_incident"
    RECORD_RESULT = "record_result"
    MANAGE_PADS = "manage_pads"
    MAINTAIN_PADS = "maintain_pads"
    VIEW_REPORTS = "view_reports"
    MANAGE_STAFF = "manage_staff"


#: Матрица прав (раздел 2.3 README). Руководитель имеет все права.
ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.HEAD: frozenset(Permission),
    Role.PAD_ENGINEER: frozenset({Permission.VIEW_SCHEDULE, Permission.MARK_PAD_STAGES, Permission.MAINTAIN_PADS}),
    Role.FUEL_ENGINEER: frozenset({Permission.VIEW_SCHEDULE, Permission.MANAGE_FUEL}),
    Role.METEOROLOGIST: frozenset({Permission.VIEW_SCHEDULE, Permission.RECORD_WEATHER, Permission.POSTPONE_LAUNCH}),
    Role.TELEMETRY_OPERATOR: frozenset({Permission.VIEW_SCHEDULE, Permission.MANAGE_CHANNELS,
                                        Permission.RECORD_INCIDENT}),
}
