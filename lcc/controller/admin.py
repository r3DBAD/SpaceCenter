"""Контроллеры руководителя и инженера ТК: площадки, справочники, персонал, отчёты."""
from __future__ import annotations

from datetime import date

from ..model.enums import MaintenanceKind, Role, VehicleClass
from ..model.reports import Period
from ..view.csv_export import CsvExporter
from .base import BaseController


class PadController(BaseController):
    def list_pads(self) -> None:
        if not self.show_pads():
            self.view.show_message("Стартовые площадки не заведены")

    def add_pad(self) -> None:
        code = self.view.ask_text("Код площадки")
        name = self.view.ask_text("Наименование")
        mc = self.view.ask_int("Макс. одновременных подготовок", 1)
        cycles = self.view.ask_int("Норматив циклов до ТО", 5)
        hours = self.view.ask_float("Норматив моточасов до ТО", 250.0)
        pad = self.safe(lambda: self.services.reference.add_pad(self.session, code, name, mc, cycles, hours))
        if pad:
            self.view.show_message(f"Площадка {pad.code} добавлена")

    def add_vehicle_type(self) -> None:
        name = self.view.ask_text("Наименование типа РН")
        key = self.view.choose("Класс", [(str(i), c.title) for i, c in enumerate(VehicleClass, 1)])
        stages = self.view.ask_int("Число ступеней", 2)
        vt = self.safe(lambda: self.services.reference.add_vehicle_type(
            self.session, name, list(VehicleClass)[int(key) - 1], stages))
        if vt:
            self.view.show_message(f"Тип РН «{vt.name}» добавлен")

    def open_maintenance(self) -> None:
        self.list_pads()
        pad_id = self.view.ask_int("Площадка (№)")
        key = self.view.choose("Вид работ", [(str(i), k.title) for i, k in enumerate(MaintenanceKind, 1)])
        notes = self.view.ask_text("Примечание", "")
        rec = self.safe(lambda: self.services.pads.open_maintenance(
            self.session, pad_id, list(MaintenanceKind)[int(key) - 1], notes))
        if rec:
            self.view.show_message(f"ТО №{rec.maint_no} открыто, площадка на обслуживании")

    def close_maintenance(self) -> None:
        self.list_pads()
        pad_id = self.view.ask_int("Площадка (№)")
        done = self.safe(lambda: self.services.pads.close_maintenance(self.session, pad_id) or True)
        if done:
            self.view.show_message("ТО закрыто, счётчики циклов и моточасов обнулены")

    def mark_faulty(self) -> None:
        self.list_pads()
        pad_id = self.view.ask_int("Площадка (№)")
        if self.view.confirm("Перевести площадку в состояние «Неисправна»?"):
            if self.safe(lambda: self.services.pads.mark_faulty(self.session, pad_id) or True):
                self.view.show_message("Площадка помечена неисправной")


class StaffController(BaseController):
    def list_staff(self) -> None:
        staff = self.safe(lambda: self.services.staff.list_staff(self.session))
        if staff is not None:
            self.view.show_table("Персонал", ["№", "Логин", "ФИО", "Роль", "Активен"],
                                 [[s.id, s.login, s.full_name, s.role.title, "да" if s.is_active else "нет"]
                                  for s in staff])

    def add_staff(self) -> None:
        login = self.view.ask_text("Логин")
        name = self.view.ask_text("ФИО")
        key = self.view.choose("Роль", [(str(i), r.title) for i, r in enumerate(Role, 1)])
        password = self.view.ask_password("Пароль (≥ 8 символов, буквы и цифры)")
        s = self.safe(lambda: self.services.staff.create_staff(self.session, login, name,
                                                               list(Role)[int(key) - 1], password))
        if s:
            self.view.show_message(f"Сотрудник {s.login} ({s.role.title}) добавлен")

    def deactivate(self) -> None:
        self.list_staff()
        staff_id = self.view.ask_int("Сотрудник (№)")
        if self.safe(lambda: self.services.staff.deactivate(self.session, staff_id) or True):
            self.view.show_message("Учётная запись заблокирована")


class ReportController(BaseController):
    """Сценарий S6: выбор отчёта, период, вывод в консоль, экспорт в CSV."""

    def __init__(self, view, services, session, exporter: CsvExporter | None = None):
        super().__init__(view, services, session)
        self._exporter = exporter or CsvExporter()

    def run(self) -> None:
        options = [(str(i), title) for i, (_, title) in enumerate(self.services.reports.available(), 1)]
        keys = [k for k, _ in self.services.reports.available()]
        choice = self.view.choose("Отчёты", options + [("0", "Назад")])
        if choice == "0":
            return
        today = date.today()
        date_from = self.view.ask_date("Начало периода", today.replace(day=1))
        date_to = self.view.ask_date("Конец периода", today)
        report = self.safe(lambda: self.services.reports.build(self.session, keys[int(choice) - 1],
                                                               Period(date_from, date_to)))
        if report is None:
            return
        self.view.show_table(f"{report.title} за {report.period}", report.columns, report.rows, report.notes)
        if self.view.confirm("Сохранить отчёт в CSV?"):
            path = self._exporter.export(report)
            self.view.show_message(f"Отчёт сохранён: {path}")
