"""Этапы подготовки пуска (блоки A21–A24 IDEF0).

Каждый этап — подкласс :class:`PreparationStage` со своим условием завершения
(полиморфизм вместо цепочки ``if status == …`` в сервисе). Сервис собирает
:class:`StageContext` из хранилища и вызывает ``stage.check_completion(ctx)``.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .entities import Launch, LaunchPad, PadUsage
from .enums import FuelComponent, LaunchStatus, Permission
from .errors import BusinessRuleError
from .weather import WeatherLimits, WeatherObservation


@dataclass
class StageContext:
    """Данные, необходимые для проверки завершения этапа."""
    launch: Launch
    pad: LaunchPad
    pad_usage: PadUsage
    now: datetime
    fuel_loaded: dict[FuelComponent, float] = field(default_factory=dict)
    last_weather: WeatherObservation | None = None
    weather_limits: WeatherLimits = field(default_factory=WeatherLimits)


class PreparationStage(ABC):
    """Этап подготовки: какой статус завершает, кто вправе его закрыть, что проверить."""

    status: LaunchStatus
    permission: Permission
    title: str

    @abstractmethod
    def check_completion(self, ctx: StageContext) -> None:
        """Поднять :class:`BusinessRuleError`, если этап нельзя завершить."""


class PlanningStage(PreparationStage):
    """Запланирован → Сборка на ТК: пуск начинает занимать площадку."""
    status = LaunchStatus.PLANNED
    permission = Permission.MARK_PAD_STAGES
    title = "Начать сборку РН на техническом комплексе"

    def check_completion(self, ctx: StageContext) -> None:
        ctx.pad.ensure_can_accept(ctx.pad_usage)


class AssemblyStage(PreparationStage):
    status = LaunchStatus.ASSEMBLY
    permission = Permission.MARK_PAD_STAGES
    title = "Сборка завершена — транспортировать РН на СК"

    def check_completion(self, ctx: StageContext) -> None:
        if ctx.now > ctx.launch.window.end:
            raise BusinessRuleError("Стартовое окно истекло — перенесите пуск")


class TransportStage(PreparationStage):
    status = LaunchStatus.TRANSPORT
    permission = Permission.MARK_PAD_STAGES
    title = "РН на стартовом комплексе — начать заправку"

    def check_completion(self, ctx: StageContext) -> None:
        ctx.pad.ensure_operational(ctx.pad_usage)


class FuelingStage(PreparationStage):
    """Заправку можно закрыть только при наличии расхода и горючего, и окислителя."""
    status = LaunchStatus.FUELING
    permission = Permission.MANAGE_FUEL
    title = "Заправка завершена — начать предстартовый контроль"

    def check_completion(self, ctx: StageContext) -> None:
        missing = [c.title.lower() for c in FuelComponent if ctx.fuel_loaded.get(c, 0) <= 0]
        if missing:
            raise BusinessRuleError("Заправка не завершена: не заправлено — " + ", ".join(missing))


class PrelaunchCheckStage(PreparationStage):
    """Допуск к пуску: свежее (не старше 3 ч) метеонаблюдение в пределах нормы."""
    status = LaunchStatus.PRELAUNCH_CHECK
    permission = Permission.MARK_PAD_STAGES
    title = "Предстартовый контроль пройден — допустить к пуску"
    WEATHER_MAX_AGE = timedelta(hours=3)

    def check_completion(self, ctx: StageContext) -> None:
        ctx.pad.ensure_operational(ctx.pad_usage)
        obs = ctx.last_weather
        if obs is None or ctx.now - obs.observed_at > self.WEATHER_MAX_AGE:
            raise BusinessRuleError("Нет актуальных метеоданных (не старше 3 ч) — обратитесь к метеорологу")
        problems = ctx.weather_limits.violations(obs)
        if problems:
            raise BusinessRuleError("Метеоусловия не допускают пуск: " + "; ".join(problems))


#: Реестр этапов по текущему статусу пуска.
STAGES: dict[LaunchStatus, PreparationStage] = {
    s.status: s for s in (PlanningStage(), AssemblyStage(), TransportStage(), FuelingStage(), PrelaunchCheckStage())
}


def stage_for(status: LaunchStatus) -> PreparationStage:
    try:
        return STAGES[status]
    except KeyError:
        raise BusinessRuleError(f"Для статуса «{status.title}» нет этапа подготовки") from None
