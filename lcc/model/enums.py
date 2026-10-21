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
    """Причины переноса по требованию Т6.2 (ЛР4). Старые «НС» и «прочее» переведены миграцией."""
    WEATHER = ("weather", "Погода")
    TECHNICAL = ("technical", "Техническая")
    ORGANIZATIONAL = ("organizational", "Организационная")


class ForecastSource(_TitledEnum):
    AUTO = ("auto", "Внешняя метеослужба")
    MANUAL = ("manual", "Ручной ввод метеоролога")


class ForecastStatus(_TitledEnum):
    OK = ("ok", "В пределах нормы")
    WARNING = ("warning", "Возможен перенос")
    UNAVAILABLE = ("unavailable", "Служба недоступна")


class Role(_TitledEnum):
    HEAD = ("head", "Руководитель центра")
    PAD_ENGINEER = ("pad_engineer", "Инженер ТК")
    FUEL_ENGINEER = ("fuel_engineer", "Инженер по заправке")
    METEOROLOGIST = ("meteorologist", "Метеоролог")
    TELEMETRY_OPERATOR = ("telemetry_operator", "Оператор телеметрии")
    LAUNCH_DIRECTOR = ("launch_director", "Руководитель пуска")
    SAFETY_INSPECTOR = ("safety_inspector", "Инспектор безопасности")


#: Роли уровня центра: без привязки к космодрому, видят все космодромы (П1, П13).
GLOBAL_ROLES = frozenset({Role.HEAD, Role.SAFETY_INSPECTOR})


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
    MANAGE_SPACEPORTS = "manage_spaceports"
    VIEW_JOURNALS = "view_journals"                  # журналы НС и переносов
    VIEW_INCIDENT_REPORTS = "view_incident_reports"  # отчеты по инцидентам и переносам
    VIEW_AUDIT = "view_audit"
    REQUEST_FORECAST = "request_forecast"


#: Матрица прав (раздел 2.3 README). Руководитель имеет все права.
ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.HEAD: frozenset(Permission),
    Role.PAD_ENGINEER: frozenset({Permission.VIEW_SCHEDULE, Permission.MARK_PAD_STAGES, Permission.MAINTAIN_PADS}),
    Role.FUEL_ENGINEER: frozenset({Permission.VIEW_SCHEDULE, Permission.MANAGE_FUEL}),
    Role.METEOROLOGIST: frozenset({Permission.VIEW_SCHEDULE, Permission.RECORD_WEATHER, Permission.POSTPONE_LAUNCH,
                                   Permission.REQUEST_FORECAST}),
    Role.TELEMETRY_OPERATOR: frozenset({Permission.VIEW_SCHEDULE, Permission.MANAGE_CHANNELS,
                                        Permission.RECORD_INCIDENT}),
    Role.LAUNCH_DIRECTOR: frozenset({Permission.VIEW_SCHEDULE, Permission.MANAGE_LAUNCHES, Permission.POSTPONE_LAUNCH,
                                     Permission.RECORD_RESULT, Permission.VIEW_REPORTS, Permission.VIEW_JOURNALS,
                                     Permission.VIEW_INCIDENT_REPORTS, Permission.REQUEST_FORECAST}),
    # инспектор: только журналы НС и переносов и отчеты по ним (Т3.1, Т3.2)
    Role.SAFETY_INSPECTOR: frozenset({Permission.VIEW_JOURNALS, Permission.VIEW_INCIDENT_REPORTS}),
}
