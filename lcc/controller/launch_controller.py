"""Сценарии пуска: расписание, регистрация, этапы подготовки, перенос, отмена, результат."""
from __future__ import annotations

from datetime import timedelta

from ..model.enums import FINAL_STATUSES, LaunchStatus, PostponeReason
from .base import DT, BaseController

_ACTIVE = tuple(s for s in LaunchStatus if s not in FINAL_STATUSES)


class LaunchController(BaseController):
    def show_schedule(self) -> None:
        launches = self.safe(lambda: self.services.launches.list_launches(self.session))
        if launches is None:
            return
        self.view.show_table("Расписание и статусы пусков",
                             ["№", "Тип РН", "Полезная нагрузка", "Орбита", "Площадка", "Окно", "Статус"],
                             self.launch_rows(launches))

    def show_card(self) -> None:
        launch_id = self.safe(self.pick_launch)
        if launch_id is None:
            return
        card = self.safe(lambda: self.services.launches.launch_card(self.session, launch_id))
        if card is None:
            return
        l = card.launch
        self.view.show_title(f"Пуск №{l.id}: {card.vehicle.name} / {l.payload}")
        self.view.show_table("Параметры", ["Параметр", "Значение"], [
            ["Тип РН", f"{card.vehicle.name} ({card.vehicle.vehicle_class.title})"],
            ["Полезная нагрузка", l.payload], ["Целевая орбита", l.target_orbit],
            ["Площадка", f"{card.pad.code} — {card.pad.name}"],
            ["Стартовое окно", f"{l.window.start:{DT}} – {l.window.end:{DT}}"],
            ["Статус", l.status.title],
            ["Заправлено", ", ".join(f"{c.title}: {v:g} т" for c, v in card.fuel_loaded.items()) or "—"],
            ["Последнее метео", f"{card.last_weather.observed_at:{DT}}" if card.last_weather else "—"],
        ])
        self.view.show_table("Журнал этапов", ["№", "Статус", "Время", "Сотрудник"],
                             [[n, s.title, f"{t:{DT}}", who] for n, s, t, who in card.stage_log])
        if card.journal:
            self.view.show_table("Журнал НС и переносов", ["Время", "Категория", "Описание"],
                                 [[f"{r.recorded_at:{DT}}", r.category, r.summary()] for r in card.journal])

    def register(self) -> None:
        self.view.show_title("Регистрация пуска")
        vts = self.safe(lambda: self.services.reference.list_vehicle_types(self.session))
        pads = self.safe(lambda: self.services.reference.list_pads(self.session))
        if not vts or not pads:
            self.view.show_error("Сначала заведите типы РН и стартовые площадки")
            return
        self.view.show_table("Типы РН", ["№", "Наименование", "Класс"],
                             [[v.id, v.name, v.vehicle_class.title] for v in vts])
        vt_id = self.view.ask_int("Тип РН (№)")
        self.view.show_table("Стартовые площадки", ["№", "Код", "Наименование", "Подготовок", "Состояние"],
                             [[p.id, p.code, p.name, f"{u.active_preparations}/{p.max_concurrent}", p.state(u).title]
                              for p, u in pads])
        pad_id = self.view.ask_int("Площадка (№)")
        payload = self.view.ask_text("Полезная нагрузка")
        orbit = self.view.ask_text("Целевая орбита")
        start = self.view.ask_datetime("Начало стартового окна")
        end = self.view.ask_datetime("Конец стартового окна", start + timedelta(hours=1))
        launch = self.safe(lambda: self.services.launches.register_launch(
            self.session, vt_id, pad_id, payload, orbit, start, end))
        if launch:
            self.view.show_message(f"Пуск №{launch.id} зарегистрирован, статус «{launch.status.title}»")

    def advance(self) -> None:
        prep = tuple(s for s in _ACTIVE if s is not LaunchStatus.READY)
        launch_id = self.safe(lambda: self.pick_launch(prep, "Пуски на подготовке"))
        if launch_id is None:
            return
        launch = self.safe(lambda: self.services.launches.launch_card(self.session, launch_id).launch)
        if launch is None:
            return
        title = self.safe(lambda: self.services.launches.next_stage_title(launch))
        if title is None or not self.view.confirm(f"{title}?"):
            return
        status = self.safe(lambda: self.services.launches.advance_stage(self.session, launch_id))
        if status:
            self.view.show_message(f"Пуск №{launch_id}: новый статус «{status.title}»")

    def postpone(self, reason: PostponeReason | None = None, launch_id: int | None = None,
                 comment: str | None = None) -> None:
        if launch_id is None:
            launch_id = self.safe(lambda: self.pick_launch(_ACTIVE, "Активные пуски"))
            if launch_id is None:
                return
        if reason is None:
            key = self.view.choose("Причина переноса", [(str(i), r.title) for i, r in enumerate(PostponeReason, 1)])
            reason = list(PostponeReason)[int(key) - 1]
        start = self.view.ask_datetime("Новое начало окна")
        end = self.view.ask_datetime("Новый конец окна", start + timedelta(hours=1))
        if comment is None:
            comment = self.view.ask_text("Комментарий", "")
        record = self.safe(lambda: self.services.launches.postpone(self.session, launch_id, reason, start, end,
                                                                    comment))
        if record:
            self.view.show_message(f"Пуск №{launch_id} перенесён: {record.summary()}")

    def cancel(self) -> None:
        launch_id = self.safe(lambda: self.pick_launch(_ACTIVE, "Активные пуски"))
        if launch_id is None:
            return
        reason = self.view.ask_text("Причина отмены")
        if not self.view.confirm(f"Отменить пуск №{launch_id}?"):
            return
        if self.safe(lambda: self.services.launches.cancel(self.session, launch_id, reason)):
            self.view.show_message(f"Пуск №{launch_id} отменён")

    def record_result(self) -> None:
        launch_id = self.safe(lambda: self.pick_launch((LaunchStatus.READY,), "Пуски, готовые к старту"))
        if launch_id is None:
            return
        success = self.view.confirm("Пуск успешен (штатное выведение)?")
        hours = self.view.ask_float("Наработка оборудования площадки, моточасов")
        status = self.safe(lambda: self.services.launches.record_result(self.session, launch_id, success, hours))
        if status:
            self.view.show_message(f"Пуск №{launch_id}: статус «{status.title}», цикл площадки учтён")
