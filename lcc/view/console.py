"""Консольное представление.

View отвечает только за ввод/вывод: разбор формата («это число?», «это дата?»)
и оформление таблиц. Допустимость значений (отрицательный объём, окно в
прошлом и т. п.) проверяет Model — даже если View пропустит некорректные данные.
"""
from __future__ import annotations

import getpass
import unicodedata
from datetime import date, datetime
from typing import Callable, Sequence

from .base import View

DATE_FMT = "%d.%m.%Y"
DT_FMT = "%d.%m.%Y %H:%M"


def _width(text: str) -> int:
    return sum(0 if unicodedata.combining(ch) else 1 for ch in text)


class ConsoleView(View):
    def __init__(self, input_fn: Callable[[str], str] = input, output_fn: Callable[[str], None] = print,
                 password_fn: Callable[[str], str] = getpass.getpass):
        self._input = input_fn
        self._print = output_fn
        self._password = password_fn

    # ------------------------------------------------------------------ вывод
    def show_title(self, text: str) -> None:
        line = "═" * max(_width(text) + 4, 40)
        self._print(f"\n{line}\n  {text}\n{line}")

    def show_message(self, text: str) -> None:
        self._print(f"✔ {text}")

    def show_error(self, text: str) -> None:
        self._print(f"✖ Ошибка: {text}")

    def show_table(self, title, columns, rows, notes=()) -> None:
        cells = [[str(c) for c in columns]] + [["" if v is None else str(v) for v in r] for r in rows]
        widths = [max(_width(row[i]) for row in cells) for i in range(len(columns))]

        def fmt(row):
            return "│ " + " │ ".join(v + " " * (w - _width(v)) for v, w in zip(row, widths)) + " │"
        sep = "├─" + "─┼─".join("─" * w for w in widths) + "─┤"
        self._print(f"\n{title}")
        self._print("┌─" + "─┬─".join("─" * w for w in widths) + "─┐")
        self._print(fmt(cells[0]))
        self._print(sep)
        for row in cells[1:]:
            self._print(fmt(row))
        if len(cells) == 1:
            self._print("│ " + "(нет данных)".ljust(sum(widths) + 3 * (len(widths) - 1)) + " │")
        self._print("└─" + "─┴─".join("─" * w for w in widths) + "─┘")
        for n in notes:
            self._print(f"  • {n}")

    # ------------------------------------------------------------------ ввод
    def choose(self, title: str, options: Sequence[tuple[str, str]]) -> str:
        self._print(f"\n{title}")
        for key, label in options:
            self._print(f"  {key}. {label}")
        keys = {k for k, _ in options}
        while True:
            answer = self._input("Выберите пункт: ").strip()
            if answer in keys:
                return answer
            self._print("  Нет такого пункта, повторите ввод")

    def _ask(self, label: str, parse, default=None, hint: str = ""):
        suffix = f" [{default}]" if default not in (None, "") else ""
        while True:
            raw = self._input(f"{label}{hint}{suffix}: ").strip()
            if not raw and default is not None:
                return default
            try:
                return parse(raw)
            except ValueError:
                self._print("  Неверный формат, повторите ввод")

    def ask_text(self, label: str, default: str | None = None) -> str:
        return self._ask(label, lambda s: s, default)

    def ask_password(self, label: str) -> str:
        return self._password(f"{label}: ")

    def ask_int(self, label: str, default: int | None = None) -> int:
        return self._ask(label, int, default)

    def ask_float(self, label: str, default: float | None = None) -> float:
        return self._ask(label, lambda s: float(s.replace(",", ".")), default)

    def ask_date(self, label: str, default: date | None = None) -> date:
        value = self._ask(label, lambda s: datetime.strptime(s, DATE_FMT).date(),
                          default.strftime(DATE_FMT) if default else None, " (ДД.ММ.ГГГГ)")
        return datetime.strptime(value, DATE_FMT).date() if isinstance(value, str) else value

    def ask_datetime(self, label: str, default: datetime | None = None) -> datetime:
        value = self._ask(label, lambda s: datetime.strptime(s, DT_FMT),
                          default.strftime(DT_FMT) if default else None, " (ДД.ММ.ГГГГ ЧЧ:ММ)")
        return datetime.strptime(value, DT_FMT) if isinstance(value, str) else value

    def confirm(self, question: str) -> bool:
        return self._ask(question, self._yes_no, hint=" (д/н)")

    @staticmethod
    def _yes_no(s: str) -> bool:
        s = s.lower()
        if s in ("д", "да", "y", "yes"):
            return True
        if s in ("н", "нет", "n", "no"):
            return False
        raise ValueError(s)
