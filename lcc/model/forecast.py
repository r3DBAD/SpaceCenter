"""Прогноз погоды за 24 часа до стартового окна (ЛР4, Т4.1–Т4.4).

* Таймер веб-сервера раз в минуту вызывает :meth:`ForecastService.run_auto`.
  Для пусков, у которых до окна меньше 24 часов, запрашивается внешняя служба.
* Прогноз сравнивается с теми же пределами WeatherLimits, что и фактическое
  наблюдение (П5). Превышение дает предупреждение и не блокирует подготовку (П6).
* Если служба недоступна, записывается статус «Служба недоступна». После этого
  метеоролог может ввести прогноз вручную. Пока служба отвечает, ручной ввод
  запрещен: два разных прогноза на одно окно путали бы решение.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .entities import Launch
from .enums import FINAL_STATUSES, ForecastSource, ForecastStatus, Permission
from .errors import BusinessRuleError, ValidationError
from .meteo_client import Forecast, MeteoProvider, MeteoUnavailable
from .security import Session
from .validation import require_number
from .weather import WeatherLimits


@dataclass(frozen=True)
class ForecastRecord:
    """Сохраненный результат запроса прогноза для конкретного стартового окна."""
    launch_id: int
    window_start: datetime
    requested_at: datetime
    source: ForecastSource
    status: ForecastStatus
    forecast: Forecast | None
    details: str
    staff_id: int | None = None
    forecast_no: int | None = None

    def summary(self) -> str:
        text = f"{self.status.title} ({self.source.title.lower()}, {self.requested_at:%d.%m %H:%M})"
        return f"{text}: {self.details}" if self.details else text


class ForecastService:
    LEAD_TIME = timedelta(hours=24)
    RETRY_AFTER = timedelta(minutes=30)     # повтор, если служба была недоступна

    def __init__(self, repos, clock, limits: WeatherLimits, provider: MeteoProvider | None):
        self._repos = repos
        self._clock = clock
        self._limits = limits
        self._provider = provider

    def _now(self) -> datetime:
        return self._clock().replace(second=0, microsecond=0)

    # ------------------------------------------------------------------ правила
    def in_lead_time(self, launch: Launch, now: datetime) -> bool:
        return launch.window.start - self.LEAD_TIME <= now < launch.window.start

    def _evaluate(self, forecast: Forecast) -> tuple[ForecastStatus, str]:
        problems = self._limits.violations(forecast)
        if problems:
            return ForecastStatus.WARNING, "; ".join(problems)
        return ForecastStatus.OK, ""

    def _site(self, launch: Launch) -> str:
        pad = self._repos.pads.get(launch.pad_id)
        return self._repos.spaceports.get(pad.spaceport_id).code

    def _query(self, launch: Launch, staff_id: int | None) -> ForecastRecord:
        """Запросить внешнюю службу и сохранить результат (в том числе отказ службы)."""
        now = self._now()
        try:
            if self._provider is None:
                raise MeteoUnavailable("внешняя метеослужба не настроена")
            forecast = self._provider.fetch(self._site(launch), launch.window.start)
            status, details = self._evaluate(forecast)
        except MeteoUnavailable as e:
            forecast, status, details = None, ForecastStatus.UNAVAILABLE, f"{e}. Нужен ручной ввод метеоролога"
        record = ForecastRecord(launch.id, launch.window.start, now, ForecastSource.AUTO, status, forecast,
                                details, staff_id)
        return self._repos.forecasts.add(record)

    # ------------------------------------------------------------------ автоматический режим
    def due(self) -> list[Launch]:
        """Пуски, которым пора запросить прогноз."""
        now, result = self._now(), []
        # ЛР5: отбор по окну и статусу делает SQL (индекс ix_launch_window), а не перебор всех пусков в Python
        for launch in self._repos.launches.starting_between(now, now + self.LEAD_TIME):
            last = self._repos.forecasts.latest(launch.id, launch.window.start)
            if last is None or (last.status is ForecastStatus.UNAVAILABLE and last.source is ForecastSource.AUTO
                                and now - last.requested_at >= self.RETRY_AFTER):
                result.append(launch)
        return result

    def run_auto(self) -> list[ForecastRecord]:
        """Вызывается таймером без пользователя (staff_id = NULL)."""
        return [self._query(launch, None) for launch in self.due()]

    # ------------------------------------------------------------------ действия пользователей
    def _launch_in_scope(self, session: Session, launch_id: int) -> Launch:
        launch = self._repos.launches.get(launch_id)
        session.ensure_spaceport(self._repos.pads.get(launch.pad_id).spaceport_id)
        if launch.status in FINAL_STATUSES:
            raise BusinessRuleError(f"Пуск в статусе «{launch.status.title}» — прогноз не нужен")
        if not self.in_lead_time(launch, self._now()):
            raise BusinessRuleError("Прогноз запрашивается за 24 часа до стартового окна и позже, до его начала")
        return launch

    def request(self, session: Session, launch_id: int) -> ForecastRecord:
        """Запросить прогноз сразу, не дожидаясь таймера."""
        session.require(Permission.REQUEST_FORECAST)
        return self._query(self._launch_in_scope(session, launch_id), session.staff_id)

    def enter_manual(self, session: Session, launch_id: int, ground_wind: float, altitude_wind: float,
                     temperature: float, cloud_base: float | None, thunderstorm: bool) -> ForecastRecord:
        """Ручной прогноз метеоролога — только когда служба недоступна (Т4.3)."""
        session.require(Permission.RECORD_WEATHER)
        launch = self._launch_in_scope(session, launch_id)
        last = self._repos.forecasts.latest(launch.id, launch.window.start)
        if last is None or last.status is not ForecastStatus.UNAVAILABLE:
            raise BusinessRuleError("Ручной ввод прогноза разрешен, только если внешняя служба недоступна. "
                                    "Сначала запросите прогноз у службы")
        if not isinstance(thunderstorm, bool):
            raise ValidationError("Признак грозы должен быть да/нет")
        forecast = Forecast(
            launch.window.start,
            require_number(ground_wind, "Ветер у земли, м/с", min_value=0, max_value=100),
            require_number(altitude_wind, "Ветер на высотах, м/с", min_value=0, max_value=200),
            require_number(temperature, "Температура, °C", min_value=-90, max_value=60), thunderstorm,
            None if cloud_base in (None, "") else require_number(cloud_base, "Нижняя граница облачности, м",
                                                                 min_value=0, max_value=20000))
        status, details = self._evaluate(forecast)
        record = ForecastRecord(launch.id, launch.window.start, self._now(), ForecastSource.MANUAL, status,
                                forecast, details, session.staff_id)
        return self._repos.forecasts.add(record)

    def latest(self, launch: Launch) -> ForecastRecord | None:
        return self._repos.forecasts.latest(launch.id, launch.window.start)

    def upcoming(self, session: Session, spaceport_id: int | None = None) -> list[tuple[Launch, ForecastRecord | None]]:
        """Пуски с окном в ближайшие 48 часов и последний прогноз по каждому (вкладка «Метео»)."""
        session.require(Permission.REQUEST_FORECAST, Permission.RECORD_WEATHER)
        now, scope = self._now(), session.scope(spaceport_id)
        launches = self._repos.launches.starting_between(now, now + 2 * self.LEAD_TIME)
        pads = {p.id: p.spaceport_id for p in self._repos.pads.list_all(scope)}
        return [(l, self.latest(l)) for l in launches if l.pad_id in pads]
