"""Контроллеры рабочих мест: заправка, метеоконтроль, телеметрия."""
from __future__ import annotations

from datetime import timedelta

from ..model.enums import FINAL_STATUSES, FuelComponent, LaunchStatus, PostponeReason
from .base import DT, BaseController
from .launch_controller import LaunchController


class FuelController(BaseController):
    def list_batches(self) -> None:
        batches = self.safe(lambda: self.services.fuel.list_batches(self.session))
        if batches is None:
            return
        self.view.show_table("Партии топлива", ["№", "Партия", "Компонент", "Марка", "Объём, т", "Остаток, т",
                                                "Годна до"],
                             [[b.id, b.batch_no, b.component.title, b.grade, f"{b.initial_volume:g}", f"{rest:g}",
                               f"{b.expires_on:%d.%m.%Y}"] for b, rest in batches])

    def receive_batch(self) -> None:
        self.view.show_title("Приём партии топлива")
        number = self.view.ask_text("Номер партии")
        key = self.view.choose("Компонент", [("1", FuelComponent.FUEL.title), ("2", FuelComponent.OXIDIZER.title)])
        component = FuelComponent.FUEL if key == "1" else FuelComponent.OXIDIZER
        grade = self.view.ask_text("Марка (напр. РГ-1, АТ, жидкий кислород)")
        volume = self.view.ask_float("Объём, т")
        produced = self.view.ask_date("Дата изготовления")
        expires = self.view.ask_date("Срок годности")
        batch = self.safe(lambda: self.services.fuel.receive_batch(self.session, number, component, grade, volume,
                                                                    produced, expires))
        if batch:
            self.view.show_message(f"Партия {batch.batch_no} принята ({batch.initial_volume:g} т)")

    def refuel(self) -> None:
        launch_id = self.safe(lambda: self.pick_launch((LaunchStatus.FUELING,), "Пуски на этапе заправки"))
        if launch_id is None:
            return
        self.list_batches()
        batch_id = self.view.ask_int("Партия (№)")
        volume = self.view.ask_float("Объём заправки, т")
        rest = self.safe(lambda: self.services.fuel.refuel(self.session, launch_id, batch_id, volume))
        if rest is not None:
            self.view.show_message(f"Расход списан, остаток партии {rest:g} т")


class WeatherController(BaseController):
    """Сценарий S4: ввод метеоданных → допуск или перенос пуска."""

    def record(self) -> None:
        active = tuple(s for s in LaunchStatus if s not in FINAL_STATUSES)
        launch_id = self.safe(lambda: self.pick_launch(active, "Пуски, требующие метеоконтроля"))
        if launch_id is None:
            return
        lim = self.services.weather.limits
        self.view.show_message(f"Пределы: ветер у земли ≤ {lim.max_ground_wind:g} м/с, на высотах ≤ "
                               f"{lim.max_altitude_wind:g} м/с, t° {lim.min_temperature:g}…{lim.max_temperature:g} °C, "
                               f"облачность ≥ {lim.min_cloud_base:g} м, без грозы")
        ground = self.view.ask_float("Ветер у земли, м/с")
        altitude = self.view.ask_float("Ветер на высотах, м/с")
        temp = self.view.ask_float("Температура, °C")
        cloud = self.view.ask_float("Нижняя граница облачности, м")
        storm = self.view.confirm("Грозовая активность в радиусе 10 км?")
        decision = self.safe(lambda: self.services.weather.record_observation(
            self.session, launch_id, ground, altitude, temp, cloud, storm))
        if decision is None:
            return
        if decision.allowed:
            self.view.show_message("Метеоусловия в норме — допуск к пуску по метео разрешён")
            return
        self.view.show_error("Метеоусловия не допускают пуск: " + "; ".join(decision.reasons))
        if self.view.confirm("Перенести пуск на новое окно?"):
            LaunchController(self.view, self.services, self.session).postpone(
                PostponeReason.WEATHER, launch_id, "; ".join(decision.reasons))


class TelemetryController(BaseController):
    def list_channels(self) -> None:
        channels = self.safe(lambda: self.services.reference.list_channels(self.session))
        if channels is not None:
            self.view.show_table("Каналы телеметрии", ["№", "Канал", "Система РН", "Параметр", "Частота, Гц"],
                                 [[c.id, c.channel_no, c.vehicle_system, c.parameter, f"{c.frequency_hz:g}"]
                                  for c in channels])

    def add_channel(self) -> None:
        no = self.view.ask_text("Номер канала")
        system = self.view.ask_text("Система РН (ДУ, СУ, СЭС, ТР…)")
        param = self.view.ask_text("Контролируемый параметр")
        freq = self.view.ask_float("Частота опроса, Гц")
        ch = self.safe(lambda: self.services.reference.add_channel(self.session, no, system, param, freq))
        if ch:
            self.view.show_message(f"Канал {ch.channel_no} добавлен")

    def register_incident(self) -> None:
        statuses = (LaunchStatus.ASSEMBLY, LaunchStatus.TRANSPORT, LaunchStatus.FUELING,
                    LaunchStatus.PRELAUNCH_CHECK, LaunchStatus.READY, LaunchStatus.LAUNCHED, LaunchStatus.FAILED)
        launch_id = self.safe(lambda: self.pick_launch(statuses, "Пуски под телеметрическим контролем"))
        if launch_id is None:
            return
        self.list_channels()
        raw = self.view.ask_text("Канал (№, пусто — не определён)", "").strip()
        if raw and not raw.isdigit():
            self.view.show_error("Номер канала должен быть целым числом (или оставьте поле пустым)")
            return
        channel_id = int(raw) if raw else None
        description = self.view.ask_text("Описание (что произошло)", "Потеря сигнала")
        measures = self.view.ask_text("Принятые меры")
        incident = self.safe(lambda: self.services.telemetry.register_incident(
            self.session, launch_id, channel_id, description, measures))
        if incident:
            self.view.show_message(f"НС зарегистрирована в {incident.recorded_at:{DT}}")
