"""Журнальные записи: нештатные ситуации и переносы пуска.

Обе записи — неизменяемые факты, связанные с пуском. Общий абстрактный класс
:class:`JournalRecord` позволяет выводить единый журнал и считать отчёт
полиморфно (метод :meth:`JournalRecord.summary`).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from .entities import LaunchWindow, normalize_comment
from .enums import PostponeReason
from .validation import require_datetime, require_enum, require_int, require_text


class JournalRecord(ABC):
    """Базовая запись журнала пуска."""

    category: str = ""

    def __init__(self, launch_id: int, staff_id: int, recorded_at: datetime):
        self._launch_id = require_int(launch_id, "Пуск", min_value=1)
        self._staff_id = require_int(staff_id, "Сотрудник", min_value=1)
        self._recorded_at = require_datetime(recorded_at, "Время записи")

    launch_id = property(lambda self: self._launch_id)
    staff_id = property(lambda self: self._staff_id)
    recorded_at = property(lambda self: self._recorded_at)

    @abstractmethod
    def summary(self) -> str:
        """Краткое описание записи для журнала."""


class Incident(JournalRecord):
    """Нештатная ситуация: потеря сигнала по каналу телеметрии (канал может быть не определён)."""

    category = "НС"

    def __init__(self, launch_id: int, channel_id: int | None, occurred_at: datetime, description: str,
                 measures: str, staff_id: int):
        super().__init__(launch_id, staff_id, occurred_at)
        self._channel_id = None if channel_id is None else require_int(channel_id, "Канал", min_value=1)
        self._description = require_text(description, "Описание НС", 300)
        self._measures = require_text(measures, "Принятые меры", 300)

    channel_id = property(lambda self: self._channel_id)
    description = property(lambda self: self._description)
    measures = property(lambda self: self._measures)

    def summary(self) -> str:
        return f"{self._description}; меры: {self._measures}"


class Postponement(JournalRecord):
    """Перенос пуска на новое стартовое окно с указанием причины."""

    category = "Перенос"

    def __init__(self, launch_id: int, reason: PostponeReason, old_window: LaunchWindow,
                 new_window: LaunchWindow, comment: str, staff_id: int, moment: datetime):
        super().__init__(launch_id, staff_id, moment)
        self._reason = require_enum(reason, PostponeReason, "Причина переноса")
        self._old_window = old_window
        self._new_window = new_window
        self._comment = normalize_comment(comment)

    reason = property(lambda self: self._reason)
    old_window = property(lambda self: self._old_window)
    new_window = property(lambda self: self._new_window)
    comment = property(lambda self: self._comment)

    def summary(self) -> str:
        text = f"{self._reason.title}: {self._old_window.start:%d.%m %H:%M} → {self._new_window.start:%d.%m %H:%M}"
        return f"{text} ({self._comment})" if self._comment else text
