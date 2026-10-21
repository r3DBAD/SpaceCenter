"""Отчёты руководителя центра (блок A5 IDEF0).

Все отчёты реализуют общий интерфейс :class:`Report` и возвращают
:class:`ReportTable` — независимую от способа отображения структуру.
Отображение (страница, CSV) — задача слоя View.

ЛР4: отчет строится по одному космодрому или по всем (сводка руководителя центра),
у каждого отчета свое право доступа (инспектору доступны отчеты по инцидентам).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from .audit import AuditLogger
from .database import date_from_db
from .enums import FuelComponent, LaunchStatus, Permission, PostponeReason, VehicleClass
from .errors import ValidationError
from .repositories import Repositories
from .security import Session
from .units import Mass
from .validation import require_date, require_enum


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
    """Базовый отчёт. Подклассы определяют ключ, название, права и способ построения."""

    key: str
    title: str
    permissions: tuple[Permission, ...] = (Permission.VIEW_REPORTS,)

    def __init__(self, repos: Repositories, now: datetime, spaceport_id: int | None = None,
                 reason: PostponeReason | None = None):
        self._repos = repos
        self._now = now
        self._spaceport = spaceport_id          # None — сводка по всем космодромам
        self._reason = reason

    def _scope_note(self, table: ReportTable) -> None:
        if self._spaceport is None:
            table.notes.append("Сводка по всем космодромам")
        else:
            sp = self._repos.spaceports.get(self._spaceport)
            table.notes.append(f"Космодром {sp.name} ({sp.code})")

    @abstractmethod
    def build(self, period: Period) -> ReportTable:
        """Построить отчёт за период."""


class LaunchesByVehicleReport(Report):
    key = "launches"
    title = "Количество пусков по типам носителей"

    def build(self, period: Period) -> ReportTable:
        per_type: dict[tuple[str, str], Counter] = defaultdict(Counter)
        for r in self._repos.launches.stats_by_vehicle(period.start, period.end, self._spaceport):
            per_type[(r["name"], r["vehicle_class"])][LaunchStatus(r["status"])] += r["n"]
        postponed = Counter(self._repos.journal.postponements_by_vehicle(period.start, period.end, self._spaceport))
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
        self._scope_note(table)
        return table


class FuelUsageReport(Report):
    key = "fuel"
    title = "Расход топлива по партиям"

    def build(self, period: Period) -> ReportTable:
        table = ReportTable(self.title, period, ["Космодром", "Партия", "Компонент", "Марка", "Масса", "Расход за период",
                                                 "Пусков", "Остаток", "Использовано, %", "Годна до"])
        by_component: Counter = Counter()
        today = self._now.date()
        for r in self._repos.fuel.batch_usage(period.start, period.end, self._spaceport):
            remaining = Mass(r["initial_mass_hg"] - r["used_total"])    # остаток вычисляется, не хранится
            expires = date_from_db(r["expires_on"])
            mark = " (просрочена)" if expires < today else ""
            table.rows.append([r["spaceport"], r["batch_no"], FuelComponent(r["component"]).title, r["grade"],
                               Mass(r["initial_mass_hg"]), Mass(r["used_period"]), r["launches"], remaining,
                               f"{100 * r['used_total'] / r['initial_mass_hg']:.1f}", f"{expires:%d.%m.%Y}{mark}"])
            by_component[FuelComponent(r["component"]).title] += r["used_period"]
        for comp, hg in by_component.items():
            table.notes.append(f"{comp}: израсходовано за период {Mass(hg)}")
        if not table.rows:
            table.notes.append("Партии топлива не зарегистрированы")
        table.notes.append("Масса учитывается с точностью 0,1 кг")
        self._scope_note(table)
        return table


class PadLoadReport(Report):
    key = "pads"
    title = "Загрузка стартовых площадок"

    def build(self, period: Period) -> ReportTable:
        codes = {sp.id: sp.code for sp in self._repos.spaceports.list_all()}
        table = ReportTable(self.title, period, ["Космодром", "Площадка", "Лимит подготовок", "Подготовок за период",
                                                 "Пусков за период", "Занятость, сут", "Загрузка, %",
                                                 "Циклов до ТО", "Моточасов до ТО", "Состояние"])
        for pad in self._repos.pads.list_all(self._spaceport):
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
            table.rows.append([codes.get(pad.spaceport_id, "?"), pad.code, pad.max_concurrent, prepared, launched, f"{busy_days:.1f}", f"{load:.1f}",
                               max(pad.cycle_limit - usage.cycles_since_maintenance, 0),
                               f"{max(pad.hours_limit - usage.hours_since_maintenance, 0):g}",
                               pad.state(usage).title])
        table.notes.append("Загрузка = суммарная занятость / (дней в периоде × лимит одновременных подготовок)")
        self._scope_note(table)
        return table


class IncidentsReport(Report):
    key = "incidents"
    title = "Нештатные ситуации и переносы"
    permissions = (Permission.VIEW_REPORTS, Permission.VIEW_INCIDENT_REPORTS)

    def build(self, period: Period) -> ReportTable:
        table = ReportTable(self.title, period, ["Категория", "Детализация", "Количество"])
        incidents = self._repos.journal.incidents_by_system(period.start, period.end, self._spaceport)
        for system, n in incidents:
            table.rows.append(["НС (потеря сигнала)", system, n])
        reasons = Counter(p.reason for p in self._repos.journal.postponements(
            date_from=period.start, date_to=period.end, spaceport_id=self._spaceport))
        for reason in PostponeReason:
            if reasons[reason]:
                table.rows.append(["Перенос пуска", reason.title, reasons[reason]])
        n_inc, n_post = sum(n for _, n in incidents), sum(reasons.values())
        table.notes.append(f"Всего НС: {n_inc}; всего переносов: {n_post}")
        self._scope_note(table)
        return table


class PostponementStatsReport(Report):
    """Статистика переносов по месяцам и причинам (ЛР4, Т6.3)."""
    key = "postponements"
    title = "Статистика переносов пусков"
    permissions = (Permission.VIEW_REPORTS, Permission.VIEW_INCIDENT_REPORTS)

    def build(self, period: Period) -> ReportTable:
        table = ReportTable(self.title, period, ["Месяц", "Причина", "Переносов", "Пусков", "Средний сдвиг окна, ч",
                                                 "Наибольший сдвиг, ч"])
        groups: dict[tuple[str, PostponeReason], list] = defaultdict(list)
        for p in self._repos.journal.postponements(date_from=period.start, date_to=period.end,
                                                   spaceport_id=self._spaceport, reason=self._reason):
            groups[(f"{p.recorded_at:%Y-%m}", p.reason)].append(p)
        order = list(PostponeReason)
        for (month, reason), items in sorted(groups.items(), key=lambda kv: (kv[0][0], order.index(kv[0][1]))):
            shifts = [(p.new_window.start - p.old_window.start).total_seconds() / 3600 for p in items]
            table.rows.append([month, reason.title, len(items), len({p.launch_id for p in items}),
                               f"{sum(shifts) / len(shifts):.1f}", f"{max(shifts):.1f}"])
        total = Counter(reason for (_, reason), items in groups.items() for _ in items)
        if total:
            table.notes.append("Всего: " + ", ".join(f"{r.title.lower()} {total[r]}" for r in order if total[r]))
        else:
            table.notes.append("За период переносов нет")
        if self._reason:
            table.notes.append(f"Фильтр по причине: {self._reason.title}")
        self._scope_note(table)
        return table


#: Реестр отчётов: ключ → класс (полиморфный выбор без условных операторов).
REPORTS: dict[str, type[Report]] = {cls.key: cls for cls in
                                    (LaunchesByVehicleReport, FuelUsageReport, PadLoadReport, IncidentsReport,
                                     PostponementStatsReport)}


class ReportService:
    def __init__(self, repos: Repositories, clock, audit: AuditLogger | None = None):
        self._repos = repos
        self._clock = clock
        self._audit = audit or AuditLogger(repos, clock)

    @staticmethod
    def available(session: Session) -> list[tuple[str, str]]:
        """Отчеты, доступные роли сотрудника."""
        return [(key, cls.title) for key, cls in REPORTS.items() if any(session.can(p) for p in cls.permissions)]

    def build(self, session: Session, key: str, period: Period, spaceport_id: int | None = None,
              reason: PostponeReason | str | None = None) -> ReportTable:
        if key not in REPORTS:
            raise ValidationError(f"Неизвестный отчёт «{key}»")
        cls = REPORTS[key]
        session.require(*cls.permissions)
        scope = session.scope(spaceport_id)
        reason = None if reason in (None, "") else require_enum(reason, PostponeReason, "Причина переноса")
        table = cls(self._repos, self._clock(), scope, reason).build(period)
        self._audit.record(session, "Формирование отчета", "отчет", key, f"«{cls.title}» за {period}")
        return table
