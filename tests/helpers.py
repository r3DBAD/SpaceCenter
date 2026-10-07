"""Общие помощники тестов: управляемые часы и фабрика модели с сотрудниками всех ролей."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lcc.model import Database, Repositories, Services  # noqa: E402
from lcc.model.enums import FuelComponent, Role, VehicleClass  # noqa: E402

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
