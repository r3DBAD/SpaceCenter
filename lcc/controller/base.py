"""Базовый контроллер: связь View ↔ Model и единая обработка ошибок Model."""
from __future__ import annotations

from typing import Callable, TypeVar

from ..model.enums import LaunchStatus
from ..model.errors import DomainError
from ..model.services import Services
from ..view.base import View

T = TypeVar("T")
DT = "%d.%m.%Y %H:%M"


class BaseController:
    def __init__(self, view: View, services: Services, session):
        self.view = view
        self.services = services
        self.session = session

    def safe(self, action: Callable[[], T]) -> T | None:
        """Выполнить действие; ошибку Model показать пользователю понятным сообщением (не подавлять)."""
        try:
            return action()
        except DomainError as e:
            self.view.show_error(str(e))
            return None

    # ------------------------------------------------------------------ общие помощники
    def launch_rows(self, launches) -> list[list]:
        vt = {v.id: v.name for v in self.services.reference.list_vehicle_types(self.session)}
        pads = {p.id: p.code for p, _ in self.services.reference.list_pads(self.session)}
        return [[l.id, vt.get(l.vehicle_type_id, "?"), l.payload, l.target_orbit, pads.get(l.pad_id, "?"),
                 f"{l.window.start:{DT}} – {l.window.end:%H:%M}", l.status.title] for l in launches]

    def pick_launch(self, statuses: tuple[LaunchStatus, ...] | None = None, title: str = "Пуски") -> int | None:
        launches = self.services.launches.list_launches(self.session, statuses)
        if not launches:
            self.view.show_message("Подходящих пусков нет")
            return None
        self.view.show_table(title, ["№", "Тип РН", "Полезная нагрузка", "Орбита", "Площадка", "Окно", "Статус"],
                             self.launch_rows(launches))
        return self.view.ask_int("Номер пуска")
