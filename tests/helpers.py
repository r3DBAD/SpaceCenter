"""Общие помощники тестов: управляемые часы, фабрика модели, скриптовое представление."""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lcc.model import Database, Repositories, Services  # noqa: E402
from lcc.model.enums import FuelComponent, Role, VehicleClass  # noqa: E402
from lcc.view.base import View  # noqa: E402

PWD = "test1234"
NOW = datetime(2026, 9, 1, 8, 0)


class Clock:
    def __init__(self, now: datetime = NOW):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, **kw):
        self.now += timedelta(**kw)


class World:
    """Модель в памяти с сотрудниками всех ролей и справочниками."""

    def __init__(self):
        self.clock = Clock()
        self.db = Database(":memory:")
        self.db.init_schema()
        self.repos = Repositories(self.db)
        self.s = Services(self.repos, self.clock)
        self.s.staff.create_staff(None, "head", "Руководитель", Role.HEAD, PWD)
        self.head = self.s.auth.login("head", PWD)
        sessions = {}
        for login, role in (("eng", Role.PAD_ENGINEER), ("fuel", Role.FUEL_ENGINEER),
                            ("meteo", Role.METEOROLOGIST), ("tm", Role.TELEMETRY_OPERATOR)):
            self.s.staff.create_staff(self.head, login, login.title(), role, PWD)
            sessions[login] = self.s.auth.login(login, PWD)
        self.eng, self.fuel, self.meteo, self.tm = (sessions[k] for k in ("eng", "fuel", "meteo", "tm"))
        self.vt = self.s.reference.add_vehicle_type(self.head, "Союз-2.1б", VehicleClass.MEDIUM, 3)
        self.pad = self.s.reference.add_pad(self.head, "SK-1", "Площадка 31", 1, 2, 100.0)
        d = self.clock.now.date()
        self.kero = self.s.fuel.receive_batch(self.fuel, "RG-1", FuelComponent.FUEL, "РГ-1", 100.0,
                                              d - timedelta(days=10), d + timedelta(days=100))
        self.lox = self.s.fuel.receive_batch(self.fuel, "LOX-1", FuelComponent.OXIDIZER, "O2", 200.0,
                                             d - timedelta(days=1), d + timedelta(days=30))

    def new_launch(self, days_ahead: int = 5, pad=None):
        start = self.clock.now + timedelta(days=days_ahead)
        return self.s.launches.register_launch(self.head, self.vt.id, (pad or self.pad).id, "Спутник", "ССО",
                                               start, start + timedelta(hours=1))

    def to_fueling(self, launch):
        for _ in range(3):
            self.s.launches.advance_stage(self.eng, launch.id)

    def to_ready(self, launch):
        self.to_fueling(launch)
        self.s.fuel.refuel(self.fuel, launch.id, self.kero.id, 30)
        self.s.fuel.refuel(self.fuel, launch.id, self.lox.id, 70)
        self.s.launches.advance_stage(self.fuel, launch.id)
        self.s.weather.record_observation(self.meteo, launch.id, 5, 10, 10, 1000, False)
        self.s.launches.advance_stage(self.eng, launch.id)


class ScriptedView(View):
    """Представление для e2e-тестов: ответы берутся из списка, вывод копится в self.out."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.out: list[str] = []
        self.errors: list[str] = []
        self.tables: list[tuple[str, list, list]] = []

    def _next(self):
        if not self.answers:
            raise AssertionError("Скрипт ответов исчерпан; вывод:\n" + "\n".join(self.out[-15:]))
        return self.answers.pop(0)

    def show_title(self, text): self.out.append(text)
    def show_message(self, text): self.out.append(text)

    def show_error(self, text):
        self.out.append("ERROR " + text)
        self.errors.append(text)

    def show_table(self, title, columns, rows, notes=()):
        self.tables.append((title, list(columns), [list(r) for r in rows]))
        self.out.append(title)

    def choose(self, title, options):
        answer = self._next()
        if answer not in {k for k, _ in options}:
            # позволяем указывать пункт меню по тексту — тесты не зависят от нумерации
            matches = [k for k, label in options if label == answer]
            if not matches:
                raise AssertionError(f"Нет пункта «{answer}» в меню «{title}»: {options}")
            answer = matches[0]
        return answer

    def ask_text(self, label, default=None):
        v = self._next()
        return default if v == "" and default is not None else v

    def ask_password(self, label): return self._next()
    def ask_int(self, label, default=None): return int(self._next())
    def ask_float(self, label, default=None): return float(self._next())

    def ask_date(self, label, default=None):
        v = self._next()
        return v if isinstance(v, date) else datetime.strptime(v, "%d.%m.%Y").date()

    def ask_datetime(self, label, default=None):
        v = self._next()
        if v == "" and default is not None:
            return default
        return v if isinstance(v, datetime) else datetime.strptime(v, "%d.%m.%Y %H:%M")

    def confirm(self, question): return self._next() in ("д", True)
