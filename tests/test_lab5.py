"""Тесты ЛР5: оптимизации не меняют результат, утечки устранены."""
import json
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from helpers import PWD, World
from lcc.controller.web import WebApi
from lcc.model import Database
from lcc.model.database import dt_from_db, dt_to_db
from lcc.model.enums import LaunchStatus, Role
from lcc.model.errors import AuthenticationError, ValidationError
from lcc.model.reports import Period
from lcc.model.units import Mass


class OptimizationTest(unittest.TestCase):
    def setUp(self):
        self.w = World()

    def test_dates_roundtrip(self):                                   # оптимизация №2
        moment = datetime(2026, 10, 1, 8, 30)
        self.assertEqual(dt_from_db(dt_to_db(moment)), moment)
        self.assertEqual(dt_from_db("2026-10-01T08:30"), datetime.strptime("2026-10-01T08:30", "%Y-%m-%dT%H:%M"))

    def test_used_by_batch_matches_single_queries(self):              # оптимизация №1
        w = self.w
        l = w.new_launch()
        w.to_fueling(l)
        w.s.fuel.refuel(w.fuel, l.id, w.kero.id, "12.3")
        w.s.fuel.refuel(w.fuel, l.id, w.kero.id, 4)
        w.s.fuel.refuel(w.fuel, l.id, w.lox.id, 50)
        batch = w.repos.fuel.used_by_batch()
        for b in (w.kero, w.lox, w.kero_p):
            self.assertEqual(batch.get(b.id, Mass.zero()), w.repos.fuel.used_mass(b.id))
        self.assertEqual(dict((x.batch_no, r) for x, r in w.s.fuel.list_batches(w.fuel))["RG-1"], Mass.from_kg("83.7"))

    def test_search_by_vehicle_and_status(self):                       # оптимизация №3
        w = self.w
        from lcc.model.enums import VehicleClass
        other = w.s.reference.add_vehicle_type(w.head, "Ангара-А5", VehicleClass.HEAVY, 3)
        a = w.new_launch(days_ahead=3)
        start = w.clock.now + timedelta(days=4)
        b = w.s.launches.register_launch(w.head, other.id, w.pad.id, "Луч", "ГСО", start, start + timedelta(hours=1))
        w.s.launches.cancel(w.head, b.id, "тест")
        self.assertEqual([l.id for l in w.s.launches.list_launches(w.head, vehicle_type_id=w.vt.id)], [a.id])
        self.assertEqual([l.id for l in w.s.launches.list_launches(w.head, ("cancelled",), None, other.id)], [b.id])
        with self.assertRaises(ValidationError):
            w.s.launches.list_launches(w.head, ("летит",))
        api = WebApi(w.s)
        token = json.loads(api.handle("POST", "/api/login", {}, json.dumps({"login": "head", "password": PWD})
                                      .encode()).body)["token"]
        resp = api.handle("GET", f"/api/launches?vehicle_type={other.id}&status=cancelled",
                          {"authorization": f"Bearer {token}"}, b"")
        self.assertEqual([r["id"] for r in json.loads(resp.body)], [b.id])

    def test_pads_report_counts(self):                                 # оптимизация №4
        w = self.w
        l = w.new_launch(days_ahead=1)
        w.to_ready(l)
        w.clock.now = l.window.start
        w.s.launches.record_result(w.head, l.id, True, 5)
        row = next(r for r in w.s.reports.build(w.head, "pads", Period(date(2026, 8, 1), date(2026, 9, 30))).rows
                   if r[1] == "SK-1")
        self.assertEqual(row[3:5], [1, 1])                                 # одна подготовка, один пуск
        empty = w.s.reports.build(w.head, "pads", Period(date(2025, 1, 1), date(2025, 1, 31))).rows
        self.assertTrue(all(r[3:5] == [0, 0] for r in empty))

    def test_period_bounds_cached(self):
        p = Period(date(2026, 1, 1), date(2026, 1, 31))
        self.assertIs(p.start, p.start)
        self.assertEqual(p.end, datetime(2026, 1, 31, 23, 59))

    def test_forecast_due_uses_window(self):                            # оптимизация №5
        w = self.w
        soon = w.new_launch(days_ahead=1)
        w.new_launch(days_ahead=5)
        w.clock.advance(hours=2)
        self.assertEqual([l.id for l in w.s.forecasts.due()], [soon.id])


class ParallelForecastTest(unittest.TestCase):
    """Запросы к службе выполняются параллельно, результаты пишутся в порядке пусков."""

    def test_run_auto_fetches_in_parallel(self):
        import threading
        import time
        from lcc.model.enums import ForecastStatus
        from lcc.model.meteo_client import Forecast, MeteoProvider, MeteoUnavailable

        class Slow(MeteoProvider):
            threads = set()

            def fetch(self, site, at):
                self.threads.add(threading.get_ident())
                time.sleep(0.3)
                if at.day % 2:
                    raise MeteoUnavailable("нет связи")
                return Forecast(at, 5.0, 10.0, 5.0, False, None)

        w = World(meteo=Slow())
        launches = [w.new_launch(days_ahead=1)]
        start = w.clock.now + timedelta(hours=20)
        for k in range(3):
            s0 = start + timedelta(hours=k)
            launches.append(w.s.launches.register_launch(w.head, w.vt.id, w.pad_p.id, "КА", "ССО", s0,
                                                         s0 + timedelta(minutes=10)))
        w.clock.advance(hours=1)
        t = time.perf_counter()
        recs = w.s.forecasts.run_auto()
        elapsed = time.perf_counter() - t
        self.assertEqual([r.launch_id for r in recs],                  # порядок стартовых окон
                         [l.id for l in sorted(launches, key=lambda l: l.window.start)])
        self.assertLess(elapsed, 0.9)                                  # 4 × 0,3 с последовательно = 1,2 с
        self.assertGreater(len(Slow.threads), 1)
        self.assertEqual({r.status for r in recs} - {ForecastStatus.OK, ForecastStatus.UNAVAILABLE}, set())


class LeakTest(unittest.TestCase):
    def setUp(self):
        self.w = World()
        self.t = [1000.0]
        self.api = WebApi(self.w.s, clock=lambda: self.t[0])

    def login(self, login="head"):
        resp = self.api.handle("POST", "/api/login", {}, json.dumps({"login": login, "password": PWD}).encode())
        return json.loads(resp.body)["token"]

    def get(self, token):
        return self.api.handle("GET", "/api/me", {"authorization": f"Bearer {token}"}, b"").status

    def test_session_expires_after_idle(self):                          # утечка №1
        token = self.login()
        self.t[0] += WebApi.SESSION_TTL - 1
        self.assertEqual(self.get(token), 200)                          # запрос продлевает сеанс
        self.t[0] += WebApi.SESSION_TTL - 1
        self.assertEqual(self.get(token), 200)
        self.t[0] += WebApi.SESSION_TTL + 1
        self.assertEqual(self.get(token), 401)
        self.assertNotIn(token, self.api._sessions)

    def test_sessions_per_user_are_capped(self):
        tokens = [self.login() for _ in range(20)]
        self.assertEqual(len(self.api._sessions), WebApi.MAX_SESSIONS_PER_USER)
        self.assertEqual(self.get(tokens[-1]), 200)
        self.assertEqual(self.get(tokens[0]), 401)
        other = self.login("eng")                                       # чужие сеансы не вытесняются
        self.assertEqual((self.get(other), self.get(tokens[-1])), (200, 200))

    def test_failed_logins_are_purged(self):                            # утечка №2
        auth = self.w.s.auth
        for i in range(2000):
            self.w.clock.advance(seconds=10)
            with self.assertRaises(AuthenticationError):
                auth.login(f"guess{i}", "wrong-pass1")
        self.assertLessEqual(len(auth._failed), auth.PURGE_AT + 1)
        for _ in range(auth.MAX_FAILED):                                # блокировка по-прежнему работает
            with self.assertRaises(AuthenticationError):
                auth.login("head", "wrong-pass1")
        with self.assertRaisesRegex(AuthenticationError, "заблокирована"):
            auth.login("head", PWD)

    def test_database_context_manager_closes(self):                     # утечка №3
        with tempfile.TemporaryDirectory() as tmp:
            with Database(Path(tmp) / "x.db") as db:
                db.init_schema()
            with self.assertRaises(sqlite3.ProgrammingError):
                db.scalar("SELECT 1")


if __name__ == "__main__":
    unittest.main()
