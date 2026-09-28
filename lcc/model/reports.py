"""Отчёты руководителя центра (блок A5 IDEF0).

Все отчёты реализуют общий интерфейс :class:`Report` и возвращают
:class:`ReportTable` — независимую от способа отображения структуру.
Форматирование (консоль, CSV) — задача слоя View.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from .database import date_from_db
from .enums import FuelComponent, LaunchStatus, Permission, PostponeReason, VehicleClass
from .errors import ValidationError
from .repositories import Repositories
from .security import Session
from .validation import require_date


@dataclass(frozen=True)
class Period:
    """Отчётный период [date_from; date_to] включительно."""
    date_from: date
    date_to: date

    MAX_DAYS = 3660

    def __post_init__(self):
        require_date(self.date_from, "Начало периода")
        require_date(self.date_to, "Конец периода")
        if self.date_from > self.date_to:
            raise ValidationError("Начало периода должно быть не позже конца")
        if self.days > self.MAX_DAYS:
            raise ValidationError("Период отчёта не может превышать 10 лет")

    @property
    def start(self) -> datetime:
        return datetime.combine(self.date_from, time.min)

    @property
    def end(self) -> datetime:
        return datetime.combine(self.date_to, time(23, 59))

    @property
    def days(self) -> int:
        return (self.date_to - self.date_from).days + 1

    def __str__(self) -> str:
        return f"{self.date_from:%d.%m.%Y} – {self.date_to:%d.%m.%Y}"


@dataclass
class ReportTable:
    title: str
    period: Period
    columns: list[str]
    rows: list[list] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


class Report(ABC):
    """Базовый отчёт. Подклассы определяют ключ, название и способ построения."""

    key: str
    title: str

    def __init__(self, repos: Repositories, now: datetime):
        self._repos = repos
        self._now = now

    @abstractmethod
    def build(self, period: Period) -> ReportTable:
        """Построить отчёт за период."""


class LaunchesByVehicleReport(Report):
    key = "launches"
    title = "Количество пусков по типам носителей"

    def build(self, period: Period) -> ReportTable:
        per_type: dict[tuple[str, str], Counter] = defaultdict(Counter)
        for r in self._repos.launches.stats_by_vehicle(period.start, period.end):
            per_type[(r["name"], r["vehicle_class"])][LaunchStatus(r["status"])] += r["n"]
        postponed = Counter()
        for p in self._repos.journal.postponements(date_from=period.start, date_to=period.end):
            postponed[self._repos.vehicle_types.get(self._repos.launches.get(p.launch_id).vehicle_type_id).name] += 1
        table = ReportTable(self.title, period, ["Тип РН", "Класс", "Всего", "Выполнено", "Авария",
                                                 "Отменено", "В подготовке", "Переносов"])
        total = Counter()
        for (name, cls), c in sorted(per_type.items()):
            finished = {LaunchStatus.LAUNCHED, LaunchStatus.FAILED, LaunchStatus.CANCELLED}
            row = [name, VehicleClass(cls).title, sum(c.values()), c[LaunchStatus.LAUNCHED], c[LaunchStatus.FAILED],
                   c[LaunchStatus.CANCELLED], sum(v for s, v in c.items() if s not in finished), postponed[name]]
            table.rows.append(row)
            total.update(dict(zip(table.columns[2:], row[2:])))
        if table.rows:
            table.rows.append(["ИТОГО", ""] + [total[c] for c in table.columns[2:]])
            done = total["Выполнено"] + total["Авария"]
            if done:
                table.notes.append(f"Доля успешных пусков: {total['Выполнено'] / done:.0%}")
        else:
            table.notes.append("За период пусков нет")
        return table


class FuelUsageReport(Report):
    key = "fuel"
    title = "Расход топлива по партиям"

    def build(self, period: Period) -> ReportTable:
        table = ReportTable(self.title, period, ["Партия", "Компонент", "Марка", "Объём, т", "Расход за период, т",
                                                 "Пусков", "Остаток, т", "Использовано, %", "Годна до"])
        by_component = Counter()
        today = self._now.date()
        for r in self._repos.fuel.batch_usage(period.start, period.end):
            remaining = round(r["initial_volume"] - r["used_total"], 3)    # остаток вычисляется, не хранится
            expires = date_from_db(r["expires_on"])
            mark = " (просрочена)" if expires < today else ""
            table.rows.append([r["batch_no"], FuelComponent(r["component"]).title, r["grade"],
                               f"{r['initial_volume']:g}", f"{r['used_period']:g}", r["launches"], f"{remaining:g}",
                               f"{100 * r['used_total'] / r['initial_volume']:.1f}", f"{expires:%d.%m.%Y}{mark}"])
            by_component[FuelComponent(r["component"]).title] += r["used_period"]
        for comp, v in by_component.items():
            table.notes.append(f"{comp}: израсходовано за период {v:g} т")
        if not table.rows:
            table.notes.append("Партии топлива не зарегистрированы")
        return table


class PadLoadReport(Report):
    key = "pads"
    title = "Загрузка стартовых площадок"

    def build(self, period: Period) -> ReportTable:
        table = ReportTable(self.title, period, ["Площадка", "Лимит подготовок", "Подготовок за период",
                                                 "Пусков за период", "Занятость, сут", "Загрузка, %",
                                                 "Циклов до ТО", "Моточасов до ТО", "Состояние"])
        for pad in self._repos.pads.list():
            usage = self._repos.pads.usage(pad.id)
            prepared = launched = 0
            busy = timedelta()
            for occ in self._repos.launches.occupancy(pad.id):
                if occ["started"] is None:
                    continue
                start = max(occ["started"], period.start)
                end = min(occ["finished"] or self._now, period.end)
                if end > start:
                    busy += end - start
                    prepared += 1
                if occ["finished"] and occ["status"] in (LaunchStatus.LAUNCHED, LaunchStatus.FAILED) \
                        and period.start <= occ["finished"] <= period.end:
                    launched += 1
            busy_days = busy.total_seconds() / 86400
            load = 100 * busy_days / (period.days * pad.max_concurrent)
            table.rows.append([pad.code, pad.max_concurrent, prepared, launched, f"{busy_days:.1f}", f"{load:.1f}",
                               max(pad.cycle_limit - usage.cycles_since_maintenance, 0),
                               f"{max(pad.hours_limit - usage.hours_since_maintenance, 0):g}",
                               pad.state(usage).title])
        table.notes.append("Загрузка = суммарная занятость / (дней в периоде × лимит одновременных подготовок)")
        return table


class IncidentsReport(Report):
    key = "incidents"
    title = "Нештатные ситуации и переносы"

    def build(self, period: Period) -> ReportTable:
        table = ReportTable(self.title, period, ["Категория", "Детализация", "Количество"])
        incidents = self._repos.journal.incidents_by_system(period.start, period.end)
        for system, n in incidents:
            table.rows.append(["НС (потеря сигнала)", system, n])
        reasons = Counter(p.reason for p in self._repos.journal.postponements(date_from=period.start,
                                                                              date_to=period.end))
        for reason in PostponeReason:
            if reasons[reason]:
                table.rows.append(["Перенос пуска", reason.title, reasons[reason]])
        n_inc, n_post = sum(n for _, n in incidents), sum(reasons.values())
        table.notes.append(f"Всего НС: {n_inc}; всего переносов: {n_post}")
        return table


#: Реестр отчётов: ключ → класс (полиморфный выбор без условных операторов).
REPORTS: dict[str, type[Report]] = {cls.key: cls for cls in
                                    (LaunchesByVehicleReport, FuelUsageReport, PadLoadReport, IncidentsReport)}


class ReportService:
    def __init__(self, repos: Repositories, clock):
        self._repos = repos
        self._clock = clock

    @staticmethod
    def available() -> list[tuple[str, str]]:
        return [(key, cls.title) for key, cls in REPORTS.items()]

    def build(self, session: Session, key: str, period: Period) -> ReportTable:
        session.require(Permission.VIEW_REPORTS)
        if key not in REPORTS:
            raise ValidationError(f"Неизвестный отчёт «{key}»")
        return REPORTS[key](self._repos, self._clock()).build(period)
