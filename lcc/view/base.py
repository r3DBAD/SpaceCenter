"""Интерфейс представления (контракт между Controller и View).

Controller зависит только от этой абстракции, поэтому консольное
представление можно заменить (например, на тестовое :class:`ScriptedView`
или графическое) без изменения Controller и Model.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date, datetime
from typing import Sequence


class View(ABC):
    """Абстрактное представление. Не содержит бизнес-логики и не обращается к хранилищу."""

    @abstractmethod
    def show_title(self, text: str) -> None: ...

    @abstractmethod
    def show_message(self, text: str) -> None: ...

    @abstractmethod
    def show_error(self, text: str) -> None: ...

    @abstractmethod
    def show_table(self, title: str, columns: Sequence[str], rows: Sequence[Sequence], notes: Sequence[str] = ()) -> None: ...

    @abstractmethod
    def choose(self, title: str, options: Sequence[tuple[str, str]]) -> str:
        """Выбор пункта меню; возвращает ключ выбранного пункта."""

    @abstractmethod
    def ask_text(self, label: str, default: str | None = None) -> str: ...

    @abstractmethod
    def ask_password(self, label: str) -> str: ...

    @abstractmethod
    def ask_int(self, label: str, default: int | None = None) -> int: ...

    @abstractmethod
    def ask_float(self, label: str, default: float | None = None) -> float: ...

    @abstractmethod
    def ask_date(self, label: str, default: date | None = None) -> date: ...

    @abstractmethod
    def ask_datetime(self, label: str, default: datetime | None = None) -> datetime: ...

    @abstractmethod
    def confirm(self, question: str) -> bool: ...
