"""Метеообеспечение пуска: наблюдение и предельные значения (управляющая стрелка C2 IDEF0)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .errors import ValidationError
from .validation import require_datetime, require_int, require_number


@dataclass(frozen=True)
class WeatherLimits:
    """Предельные метеоусловия пуска. Значения по умолчанию — из раздела 2.4 README."""
    max_ground_wind: float = 15.0      # м/с
    max_altitude_wind: float = 30.0    # м/с
    min_temperature: float = -35.0     # °C
    max_temperature: float = 40.0      # °C
    min_cloud_base: float = 300.0      # м
    thunderstorm_allowed: bool = False

    def __post_init__(self):
        if self.min_temperature >= self.max_temperature:
            raise ValidationError("Минимальная температура должна быть ниже максимальной")

    def violations(self, obs: "WeatherObservation") -> list[str]:
        """Список нарушений; пустой список — метеоусловия допускают пуск."""
        result = []
        if obs.ground_wind > self.max_ground_wind:
            result.append(f"ветер у земли {obs.ground_wind:g} м/с > {self.max_ground_wind:g} м/с")
        if obs.altitude_wind > self.max_altitude_wind:
            result.append(f"ветер на высотах {obs.altitude_wind:g} м/с > {self.max_altitude_wind:g} м/с")
        if not (self.min_temperature <= obs.temperature <= self.max_temperature):
            result.append(f"температура {obs.temperature:g} °C вне диапазона "
                          f"[{self.min_temperature:g}; {self.max_temperature:g}] °C")
        if obs.cloud_base < self.min_cloud_base:
            result.append(f"нижняя граница облачности {obs.cloud_base:g} м < {self.min_cloud_base:g} м")
        if obs.thunderstorm and not self.thunderstorm_allowed:
            result.append("грозовая активность в районе старта")
        return result


class WeatherObservation:
    """Метеонаблюдение для пуска. Проверяет физическую допустимость значений."""

    def __init__(self, launch_id: int, observed_at: datetime, ground_wind: float, altitude_wind: float,
                 temperature: float, cloud_base: float, thunderstorm: bool, staff_id: int,
                 obs_no: int | None = None):
        self._launch_id = require_int(launch_id, "Пуск", min_value=1)
        self._observed_at = require_datetime(observed_at, "Время наблюдения")
        self._ground_wind = require_number(ground_wind, "Ветер у земли, м/с", min_value=0, max_value=100)
        self._altitude_wind = require_number(altitude_wind, "Ветер на высотах, м/с", min_value=0, max_value=200)
        self._temperature = require_number(temperature, "Температура, °C", min_value=-90, max_value=60)
        self._cloud_base = require_number(cloud_base, "Нижняя граница облачности, м", min_value=0, max_value=20000)
        if not isinstance(thunderstorm, bool):
            raise ValidationError("Признак грозы должен быть да/нет")
        self._thunderstorm = thunderstorm
        self._staff_id = require_int(staff_id, "Сотрудник", min_value=1)
        self._obs_no = obs_no

    launch_id = property(lambda self: self._launch_id)
    observed_at = property(lambda self: self._observed_at)
    ground_wind = property(lambda self: self._ground_wind)
    altitude_wind = property(lambda self: self._altitude_wind)
    temperature = property(lambda self: self._temperature)
    cloud_base = property(lambda self: self._cloud_base)
    thunderstorm = property(lambda self: self._thunderstorm)
    staff_id = property(lambda self: self._staff_id)
    obs_no = property(lambda self: self._obs_no)


@dataclass(frozen=True)
class WeatherDecision:
    """Результат метеоконтроля."""
    allowed: bool
    reasons: tuple[str, ...] = ()
