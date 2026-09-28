"""Главный контроллер: вход в систему и главное меню по правам роли."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..model.enums import Permission
from ..model.errors import DomainError
from ..model.services import Services
from ..view.base import View
from ..view.csv_export import CsvExporter
from .admin import PadController, ReportController, StaffController
from .launch_controller import LaunchController
from .operations import FuelController, TelemetryController, WeatherController


@dataclass(frozen=True)
class MenuItem:
    title: str
    permission: Permission
    action: Callable[[], None]


class AppController:
    MAX_LOGIN_ATTEMPTS = 3

    def __init__(self, view: View, services: Services, exporter: CsvExporter | None = None):
        self.view = view
        self.services = services
        self.exporter = exporter

    # ------------------------------------------------------------------ вход
    def login(self):
        self.view.show_title("АИС «Центр управления космическими пусками» — вход")
        for _ in range(self.MAX_LOGIN_ATTEMPTS):
            login = self.view.ask_text("Логин")
            password = self.view.ask_password("Пароль")
            try:
                session = self.services.auth.login(login, password)
            except DomainError as e:
                self.view.show_error(str(e))
                continue
            self.view.show_message(f"Здравствуйте, {session.staff.full_name} ({session.staff.role.title})")
            return session
        self.view.show_error("Превышено число попыток входа")
        return None

    # ------------------------------------------------------------------ меню
    def build_menu(self, session) -> list[MenuItem]:
        args = (self.view, self.services, session)
        lc, fc, wc = LaunchController(*args), FuelController(*args), WeatherController(*args)
        tc, pc, sc = TelemetryController(*args), PadController(*args), StaffController(*args)
        rc = ReportController(*args, exporter=self.exporter)
        P = Permission
        items = [
            MenuItem("Расписание и статусы пусков", P.VIEW_SCHEDULE, lc.show_schedule),
            MenuItem("Карточка пуска", P.VIEW_SCHEDULE, lc.show_card),
            MenuItem("Зарегистрировать пуск", P.MANAGE_LAUNCHES, lc.register),
            MenuItem("Завершить этап подготовки (сборка, транспортировка, контроль)", P.MARK_PAD_STAGES, lc.advance),
            MenuItem("Принять партию топлива", P.MANAGE_FUEL, fc.receive_batch),
            MenuItem("Партии топлива и остатки", P.MANAGE_FUEL, fc.list_batches),
            MenuItem("Заправить РН (списать расход)", P.MANAGE_FUEL, fc.refuel),
            MenuItem("Завершить заправку", P.MANAGE_FUEL, lc.advance),
            MenuItem("Ввести метеоданные", P.RECORD_WEATHER, wc.record),
            MenuItem("Каналы телеметрии", P.MANAGE_CHANNELS, tc.list_channels),
            MenuItem("Добавить канал телеметрии", P.MANAGE_CHANNELS, tc.add_channel),
            MenuItem("Зарегистрировать нештатную ситуацию", P.RECORD_INCIDENT, tc.register_incident),
            MenuItem("Зафиксировать результат пуска", P.RECORD_RESULT, lc.record_result),
            MenuItem("Перенести пуск", P.MANAGE_LAUNCHES, lc.postpone),
            MenuItem("Отменить пуск", P.MANAGE_LAUNCHES, lc.cancel),
            MenuItem("Стартовые площадки", P.VIEW_SCHEDULE, pc.list_pads),
            MenuItem("Начать ТО площадки", P.MAINTAIN_PADS, pc.open_maintenance),
            MenuItem("Закрыть ТО площадки", P.MAINTAIN_PADS, pc.close_maintenance),
            MenuItem("Отметить неисправность площадки", P.MAINTAIN_PADS, pc.mark_faulty),
            MenuItem("Добавить площадку", P.MANAGE_PADS, pc.add_pad),
            MenuItem("Добавить тип РН", P.MANAGE_PADS, pc.add_vehicle_type),
            MenuItem("Персонал", P.MANAGE_STAFF, sc.list_staff),
            MenuItem("Добавить сотрудника", P.MANAGE_STAFF, sc.add_staff),
            MenuItem("Заблокировать сотрудника", P.MANAGE_STAFF, sc.deactivate),
            MenuItem("Отчёты", P.VIEW_REPORTS, rc.run),
        ]
        # меню лишь скрывает недоступное; реальная проверка прав — в сервисах Model
        return [i for i in items if session.can(i.permission)]

    def run(self) -> None:
        session = self.login()
        if session is None:
            return
        menu = self.build_menu(session)
        while True:
            options = [(str(n), item.title) for n, item in enumerate(menu, 1)] + [("0", "Выход")]
            choice = self.view.choose("Главное меню", options)
            if choice == "0":
                self.view.show_message("Сеанс завершён")
                return
            menu[int(choice) - 1].action()
