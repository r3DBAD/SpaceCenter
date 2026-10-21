"""Тесты ЛР4: космодромы, инспектор и аудит, прогноз внешней метеослужбы, топливо 0,1 кг,
журнал переносов, миграция базы ЛР3. Номера требований — docs/07_lab4_analysis.md."""
import json
import sqlite3
import tempfile
import threading
import time
import unittest
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from helpers import PWD, World
from lcc.controller.web import WebApi
from lcc.model import Database, Repositories, Services
from lcc.model.enums import ForecastSource, ForecastStatus, FuelComponent, LaunchStatus, PostponeReason, Role
from lcc.model.errors import AccessDeniedError, BusinessRuleError, ValidationError
from lcc.model.meteo_client import Forecast, HttpMeteoProvider, MeteoProvider, MeteoUnavailable, parse_forecast
from lcc.model.reports import Period
from lcc.model.security import PasswordHasher
from lcc.model.units import Mass

PERIOD = Period(date(2026, 8, 1), date(2026, 12, 31))


class StaticMeteo(MeteoProvider):
    """Заглушка службы: отдает заданный прогноз или ошибку, считает вызовы."""

    def __init__(self, forecast: Forecast | None = None, error: str | None = None):
        self.forecast, self.error, self.calls = forecast, error, []

    def fetch(self, site, at):
        self.calls.append((site, at))
        if self.error:
            raise MeteoUnavailable(self.error)
        return self.forecast


CALM = Forecast(datetime(2026, 9, 2), 5.0, 18.0, 10.0, False, 1200.0)
STORM = Forecast(datetime(2026, 9, 2), 19.0, 35.0, 10.0, True, None)


# ====================================================================== Т1: космодромы
class SpaceportScopeTest(unittest.TestCase):
    def setUp(self):
        self.w = World()

    def test_staff_spaceport_rules(self):                              # П1, П2
        w = self.w
        with self.assertRaises(ValidationError):                        # инженер без космодрома
            w.s.staff.create_staff(w.head, "e2", "Инженер", Role.PAD_ENGINEER, PWD)
        with self.assertRaises(ValidationError):                        # инспектор с космодромом
            w.s.staff.create_staff(w.head, "i2", "Инспектор", Role.SAFETY_INSPECTOR, PWD, w.vos.id)
        self.assertTrue(w.head.is_global and w.insp.is_global)
        self.assertEqual(w.dir.spaceport_id, w.vos.id)

    def test_director_sees_only_own_spaceport(self):                   # Т1.2
        w = self.w
        own = w.new_launch()
        other = w.s.launches.register_launch(w.dir_p, w.vt.id, w.pad_p.id, "Глонасс", "МОО",
                                             w.clock.now + timedelta(days=3), w.clock.now + timedelta(days=3, hours=1))
        self.assertEqual([l.id for l in w.s.launches.list_launches(w.dir)], [own.id])
        self.assertEqual([l.id for l in w.s.launches.list_launches(w.dir_p)], [other.id])
        self.assertEqual({l.id for l in w.s.launches.list_launches(w.head)}, {own.id, other.id})   # Т1.3
        with self.assertRaises(AccessDeniedError):
            w.s.launches.launch_card(w.dir, other.id)
        with self.assertRaises(AccessDeniedError):                      # фильтр на чужой космодром
            w.s.launches.list_launches(w.dir, spaceport_id=w.pls.id)
        with self.assertRaises(AccessDeniedError):                      # регистрация на чужой площадке
            w.s.launches.register_launch(w.dir, w.vt.id, w.pad_p.id, "X", "Y", w.clock.now + timedelta(days=9),
                                         w.clock.now + timedelta(days=9, hours=1))
        with self.assertRaises(AccessDeniedError):                      # инженер Восточного
            w.s.launches.advance_stage(w.eng, other.id)

    def test_fuel_from_other_spaceport_rejected(self):                 # Т5.3
        w = self.w
        l = w.new_launch()
        w.to_fueling(l)
        with self.assertRaises(BusinessRuleError):
            w.s.fuel.refuel(w.head, l.id, w.kero_p.id, 10)
        with self.assertRaises(AccessDeniedError):                      # заправщик Плесецка
            w.s.fuel.refuel(w.fuel_p, l.id, w.kero_p.id, 10)
        self.assertEqual([b.batch_no for b, _ in w.s.fuel.list_batches(w.fuel_p)], ["RG-P"])

    def test_reports_by_spaceport_and_summary(self):                   # Т1.3
        w = self.w
        r_all = w.s.reports.build(w.head, "pads", PERIOD)
        r_vos = w.s.reports.build(w.dir, "pads", PERIOD)
        self.assertEqual(sorted(row[0] for row in r_all.rows), ["PLS", "VOS"])
        self.assertEqual([row[0] for row in r_vos.rows], ["VOS"])
        self.assertIn("Сводка по всем космодромам", r_all.notes)
        with self.assertRaises(AccessDeniedError):
            w.s.reports.build(w.dir, "pads", PERIOD, spaceport_id=w.pls.id)


# ====================================================================== Т3: инспектор и аудит
class InspectorAuditTest(unittest.TestCase):
    def setUp(self):
        self.w = World()
        w = self.w
        self.l = w.new_launch()
        w.to_fueling(self.l)
        w.s.telemetry.register_incident(w.tm, self.l.id, None, "Потеря сигнала", "Резервный канал")
        w.s.launches.postpone(w.head, self.l.id, PostponeReason.TECHNICAL, self.l.window.start + timedelta(days=1),
                              self.l.window.end + timedelta(days=1), "Замена датчика")

    def audit(self):
        return self.w.s.audit.list_records(self.w.head, datetime(2026, 1, 1), datetime(2027, 1, 1), "insp")

    def test_inspector_reads_journals_only(self):                      # Т3.1, Т3.2
        w = self.w
        inc = w.s.journal.incidents(w.insp, datetime(2026, 8, 1), datetime(2026, 12, 1))
        self.assertEqual((inc[0].launch_id, inc[0].vehicle, inc[0].spaceport), (self.l.id, "Союз-2.1б", "VOS"))
        self.assertEqual(len(w.s.journal.postponements(w.insp, datetime(2026, 8, 1), datetime(2026, 12, 1))), 1)
        self.assertEqual({k for k, _ in w.s.reports.available(w.insp)}, {"incidents", "postponements"})
        w.s.reports.build(w.insp, "incidents", PERIOD)
        for forbidden in (lambda: w.s.launches.list_launches(w.insp),
                          lambda: w.s.launches.launch_card(w.insp, self.l.id),
                          lambda: w.s.launches.postpone(w.insp, self.l.id, "weather", w.clock.now + timedelta(days=20),
                                                        w.clock.now + timedelta(days=20, hours=1)),
                          lambda: w.s.reports.build(w.insp, "fuel", PERIOD),
                          lambda: w.s.audit.list_records(w.insp, datetime(2026, 1, 1), datetime(2027, 1, 1))):
            with self.assertRaises(AccessDeniedError):
                forbidden()

    def test_every_inspector_action_is_audited(self):                  # Т3.3, П12
        w = self.w
        w.s.journal.postponements(w.insp, datetime(2026, 8, 1), datetime(2026, 12, 1), reason="technical")
        w.s.reports.build(w.insp, "postponements", PERIOD)
        with self.assertRaises(AccessDeniedError):
            w.s.launches.list_launches(w.insp)
        actions = [(a.action, a.outcome) for a in self.audit()]
        self.assertEqual(actions, [("Вход в систему", "ok"), ("Просмотр журнала", "ok"),
                                   ("Формирование отчета", "ok"), ("Отказ в доступе", "denied")])
        self.assertIn("причина «Техническая»", self.audit()[1].details)

    def test_denial_inside_transaction_is_kept(self):
        """Отказ внутри транзакции: действие откатывается, а запись аудита остается."""
        w = self.w
        with self.assertRaises(AccessDeniedError):
            w.s.launches.advance_stage(w.insp, self.l.id)
        self.assertEqual(self.audit()[-1].outcome, "denied")

    def test_other_roles_not_audited(self):
        w = self.w
        w.s.journal.incidents(w.head, datetime(2026, 8, 1), datetime(2026, 12, 1))
        self.assertEqual(w.s.audit.list_records(w.head, datetime(2026, 1, 1), datetime(2027, 1, 1), "head"), [])

    def test_audit_api_and_inspector_ui_requests(self):
        api = WebApi(self.w.s)

        def call(method, url, token):
            r = api.handle(method, url, {"authorization": f"Bearer {token}"}, b"")
            return r.status, json.loads(r.body)
        token = json.loads(api.handle("POST", "/api/login", {}, json.dumps({"login": "insp", "password": PWD}).encode())
                           .body)["token"]
        status, ref = call("GET", "/api/reference", token)              # П11: без расписания и площадок
        self.assertEqual((status, ref["pads"], ref["vehicle_types"]), (200, [], []))
        self.assertEqual(call("GET", "/api/launches", token)[0], 403)
        status, rows = call("GET", "/api/journal/incidents?from=2026-08-01&to=2026-12-01", token)
        self.assertEqual((status, rows[0]["launch_id"]), (200, self.l.id))
        head = json.loads(api.handle("POST", "/api/login", {}, json.dumps({"login": "head", "password": PWD})
                                     .encode()).body)["token"]
        status, audit = call("GET", "/api/audit?from=2026-01-01&to=2026-12-31&login=insp", head)
        # вход в World, вход через API, отказ на /api/launches, просмотр журнала
        self.assertEqual([a["outcome"] for a in audit], ["выполнено", "выполнено", "отказ", "выполнено"])
        self.assertEqual(call("GET", "/api/audit?from=2026-01-01&to=2026-12-31", token)[0], 403)


# ====================================================================== Т4: прогноз
class ForecastTest(unittest.TestCase):
    def make(self, provider):
        self.w = World(meteo=provider)
        self.l = self.w.new_launch(days_ahead=2)                        # окно через 48 ч
        return self.w

    def test_auto_request_only_within_24h(self):                       # Т4.1
        meteo = StaticMeteo(CALM)
        w = self.make(meteo)
        self.assertEqual(w.s.forecasts.run_auto(), [])                   # еще рано
        w.clock.advance(hours=25)
        recs = w.s.forecasts.run_auto()
        self.assertEqual([(r.status, r.source) for r in recs], [(ForecastStatus.OK, ForecastSource.AUTO)])
        self.assertEqual(meteo.calls, [("VOS", self.l.window.start)])
        self.assertEqual(w.s.forecasts.run_auto(), [])                   # повторно не запрашивает

    def test_warning_does_not_block(self):                             # Т4.2, П6
        w = self.make(StaticMeteo(STORM))
        w.clock.advance(hours=25)
        rec = w.s.forecasts.run_auto()[0]
        self.assertIs(rec.status, ForecastStatus.WARNING)
        self.assertIn("ветер у земли 19 м/с", rec.details)
        self.assertIn("грозовая активность", rec.details)
        w.s.launches.advance_stage(w.eng, self.l.id)                     # подготовка идет дальше
        self.assertIs(w.s.launches.launch_card(w.head, self.l.id).forecast.status, ForecastStatus.WARNING)

    def test_unavailable_then_manual(self):                            # Т4.3, П5
        meteo = StaticMeteo(error="служба не ответила за 30 с")
        w = self.make(meteo)
        w.clock.advance(hours=25)
        with self.assertRaises(BusinessRuleError):                      # до отказа службы ручной ввод запрещен
            w.s.forecasts.enter_manual(w.meteo, self.l.id, 5, 10, 5, 900, False)
        rec = w.s.forecasts.run_auto()[0]
        self.assertIs(rec.status, ForecastStatus.UNAVAILABLE)
        self.assertIn("ручной ввод", rec.details)
        manual = w.s.forecasts.enter_manual(w.meteo, self.l.id, 19, 10, 5, None, False)
        self.assertEqual((manual.source, manual.status), (ForecastSource.MANUAL, ForecastStatus.WARNING))
        with self.assertRaises(BusinessRuleError):                      # служба уже не «последняя»
            w.s.forecasts.enter_manual(w.meteo, self.l.id, 5, 10, 5, 900, False)
        w.clock.advance(minutes=40)
        self.assertEqual(w.s.forecasts.run_auto(), [])                   # после ручного ввода не повторяет

    def test_retry_after_unavailable(self):
        meteo = StaticMeteo(error="нет связи")
        w = self.make(meteo)
        w.clock.advance(hours=25)
        w.s.forecasts.run_auto()
        w.clock.advance(minutes=10)
        self.assertEqual(w.s.forecasts.run_auto(), [])
        meteo.error, meteo.forecast = None, CALM
        w.clock.advance(minutes=25)
        self.assertIs(w.s.forecasts.run_auto()[0].status, ForecastStatus.OK)

    def test_no_provider_configured(self):
        w = self.make(None)
        w.clock.advance(hours=25)
        self.assertIn("не настроена", w.s.forecasts.request(w.meteo, self.l.id).details)

    def test_manual_request_rules(self):
        w = self.make(StaticMeteo(CALM))
        with self.assertRaises(BusinessRuleError):                      # раньше чем за 24 ч
            w.s.forecasts.request(w.meteo, self.l.id)
        w.clock.advance(hours=25)
        with self.assertRaises(AccessDeniedError):
            w.s.forecasts.request(w.eng, self.l.id)
        with self.assertRaises(AccessDeniedError):
            w.s.forecasts.request(w.dir_p, self.l.id)                    # чужой космодром
        self.assertIs(w.s.forecasts.request(w.dir, self.l.id).status, ForecastStatus.OK)

    def test_parse_forecast_format(self):                              # П4
        f = parse_forecast({"valid_at": "2026-09-02T06:00", "thunderstorm": False, "cloud_base_m": 800,
                            "levels": [{"altitude_m": 10000, "wind_ms": 32, "temperature_c": -50},
                                       {"altitude_m": 0, "wind_ms": 4, "temperature_c": 3}]})
        self.assertEqual((f.ground_wind, f.altitude_wind, f.temperature, f.cloud_base), (4, 32, 3, 800))
        for bad in ([], {"levels": []}, {"valid_at": "x", "thunderstorm": False, "levels": [{"altitude_m": 0}]},
                    {"valid_at": "2026-09-02T06:00", "thunderstorm": "нет",
                     "levels": [{"altitude_m": 0, "wind_ms": 1, "temperature_c": 1}]}):
            with self.assertRaises(MeteoUnavailable):
                parse_forecast(bad)


class _StubHandler(BaseHTTPRequestHandler):
    delay = 0.0
    body = b""

    def do_GET(self):
        time.sleep(self.delay)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(self.body)
        except (BrokenPipeError, ConnectionResetError):     # клиент ушел по таймауту
            pass

    def log_message(self, *args):
        pass


class HttpMeteoProviderTest(unittest.TestCase):
    """Настоящий HTTP-клиент против локального сервера: ответ, таймаут, нет связи (Т4.4)."""

    def serve(self, delay=0.0, body=None):
        handler = type("H", (_StubHandler,), {"delay": delay, "body": body if body is not None else json.dumps(
            {"valid_at": "2026-09-02T06:00", "thunderstorm": False,
             "levels": [{"altitude_m": 0, "wind_ms": 3, "temperature_c": 8}]}).encode()})
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_address[1]}"

    def test_ok(self):
        f = HttpMeteoProvider(self.serve(), timeout=2).fetch("VOS", datetime(2026, 9, 2, 6))
        self.assertEqual(f.ground_wind, 3)

    def test_timeout(self):
        started = time.monotonic()
        with self.assertRaisesRegex(MeteoUnavailable, "не ответила за 0.5 с"):
            HttpMeteoProvider(self.serve(delay=2), timeout=0.5).fetch("VOS", datetime(2026, 9, 2, 6))
        self.assertLess(time.monotonic() - started, 1.9)

    def test_bad_json_and_no_connection(self):
        with self.assertRaisesRegex(MeteoUnavailable, "не JSON"):
            HttpMeteoProvider(self.serve(body=b"<html>"), timeout=2).fetch("VOS", datetime(2026, 9, 2, 6))
        with self.assertRaisesRegex(MeteoUnavailable, "нет связи"):
            HttpMeteoProvider("http://127.0.0.1:9", timeout=2).fetch("VOS", datetime(2026, 9, 2, 6))

    def test_timeout_leads_to_manual_mode(self):
        w = World(meteo=HttpMeteoProvider(self.serve(delay=2), timeout=0.5))
        l = w.new_launch(days_ahead=1)
        self.assertIs(w.s.forecasts.run_auto()[0].status, ForecastStatus.UNAVAILABLE)
        self.assertIs(w.s.forecasts.enter_manual(w.meteo, l.id, 5, 10, 5, 900, False).status, ForecastStatus.OK)


# ====================================================================== Т5: топливо
class FuelPrecisionTest(unittest.TestCase):
    def test_mass_value_object(self):                                  # Т5.1, П7
        self.assertEqual(Mass.from_kg("0,1") + Mass.from_kg(0.2), Mass.from_kg("0.3"))   # 0.1 + 0.2 != 0.3 в float
        self.assertEqual(str(Mass.from_kg(123456.7)), "123 456,7 кг")
        for bad in ("0.05", "abc", float("nan"), True):
            with self.assertRaises(ValidationError):
                Mass.from_kg(bad)

    def test_many_small_withdrawals_are_exact(self):
        w = World()
        l = w.new_launch()
        w.to_fueling(l)
        for _ in range(100):
            w.s.fuel.refuel(w.fuel, l.id, w.kero.id, 0.1)
        rest = dict((b.batch_no, r) for b, r in w.s.fuel.list_batches(w.fuel))["RG-1"]
        self.assertEqual(rest, Mass.from_kg(90))                         # ровно 90,0 кг
        with self.assertRaises(ValidationError):
            w.s.fuel.refuel(w.fuel, l.id, w.kero.id, 0.05)

    def test_batch_must_last_until_window_end(self):                   # П8, правило 1
        w = World()
        l = w.new_launch(days_ahead=40)                                  # LOX-1 годен 30 дней
        w.to_fueling(l)
        with self.assertRaisesRegex(BusinessRuleError, "окно заканчивается"):
            w.s.fuel.refuel(w.fuel, l.id, w.lox.id, 10)

    def test_fuel_expired_in_tank_after_postponement(self):            # П8, правила 2 и 3
        w = World()
        l = w.new_launch(days_ahead=5)
        w.to_ready(l)
        start = w.clock.now + timedelta(days=35)
        w.s.launches.postpone(w.head, l.id, PostponeReason.TECHNICAL, start, start + timedelta(hours=1))
        self.assertTrue(w.s.launches.fuel_warnings(w.head, l.id))
        self.assertTrue(w.s.launches.launch_card(w.head, l.id).fuel_warnings)
        w.clock.now = start
        with self.assertRaisesRegex(BusinessRuleError, "просроченной партии"):
            w.s.launches.record_result(w.head, l.id, True, 10)
        self.assertIs(w.s.launches.launch_card(w.head, l.id).launch.status, LaunchStatus.READY)


# ====================================================================== Т6: журнал переносов
class PostponementJournalTest(unittest.TestCase):
    def setUp(self):
        self.w = w = World()
        a, b = w.new_launch(days_ahead=100), w.new_launch(days_ahead=110)     # окна не уходят в прошлое
        for launch, reason, days in ((a, "weather", 1), (a, "technical", 3), (b, "organizational", 10)):
            launch = w.s.launches.launch_card(w.head, launch.id).launch
            w.s.launches.postpone(w.head, launch.id, reason, launch.window.start + timedelta(days=days),
                                  launch.window.end + timedelta(days=days), "")
            w.clock.advance(days=40)

    def test_filter_by_reason(self):                                   # Т6.2
        w = self.w
        rows = w.s.journal.postponements(w.head, datetime(2026, 1, 1), datetime(2027, 12, 31), reason="technical")
        self.assertEqual([r.record.reason for r in rows], [PostponeReason.TECHNICAL])
        with self.assertRaises(ValidationError):
            w.s.journal.postponements(w.head, datetime(2026, 1, 1), datetime(2027, 1, 1), reason="incident")

    def test_stats_by_period(self):                                    # Т6.3
        w = self.w
        r = w.s.reports.build(w.head, "postponements", Period(date(2026, 9, 1), date(2026, 12, 31)))
        self.assertEqual([row[:3] for row in r.rows],
                         [["2026-09", "Погода", 1], ["2026-10", "Техническая", 1], ["2026-11", "Организационная", 1]])
        self.assertEqual(r.rows[1][4], "72.0")                           # сдвиг на 3 суток
        only = w.s.reports.build(w.head, "postponements", Period(date(2026, 9, 1), date(2026, 12, 31)),
                                 reason="weather")
        self.assertEqual(len(only.rows), 1)


# ====================================================================== миграция базы ЛР3
class MigrationTest(unittest.TestCase):
    def make_lab3_db(self, path):
        conn = sqlite3.connect(path)
        conn.executescript((Path(__file__).parent / "schema_v1_lab3.sql").read_text(encoding="utf-8"))
        conn.executescript("""
            INSERT INTO staff VALUES (1,'head','Руководитель','head','h','s',1),(2,'eng','Инженер','pad_engineer','h','s',1);
            INSERT INTO vehicle_type VALUES (1,'Союз-2.1б','medium',3);
            INSERT INTO launch_pad VALUES (1,'SK-1','Площадка 31',2,3,150,0);
            INSERT INTO launch VALUES (1,1,1,1,'Спутник','ССО','2026-09-10T10:00','2026-09-10T11:00','fueling',NULL,'');
            INSERT INTO stage_log VALUES (1,1,'planned','2026-09-01T08:00',1);
            INSERT INTO fuel_batch VALUES (1,'RG-1','fuel','РГ-1',180.0,'2026-01-01','2027-01-01');
            INSERT INTO fuel_consumption VALUES (1,1,1,38.0123,'2026-09-05T10:00',2);
            INSERT INTO postponement VALUES (1,1,'incident','2026-09-08T10:00','2026-09-08T11:00',
                '2026-09-10T10:00','2026-09-10T11:00','НС на 312 с',1,'2026-09-07T10:00');
            INSERT INTO postponement VALUES (1,2,'other','2026-09-10T10:00','2026-09-10T11:00',
                '2026-09-11T10:00','2026-09-11T11:00','',1,'2026-09-08T10:00');
        """)
        conn.commit()
        conn.close()

    def test_lab3_database_is_migrated(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lab3.db"
            self.make_lab3_db(path)
            db = Database(path)
            db.init_schema()
            self.assertEqual(db.schema_version, 2)
            repos = Repositories(db)
            self.assertEqual(repos.spaceports.get(1).code, "MAIN")
            self.assertIsNone(repos.staff.get(1).spaceport_id)           # руководитель центра
            self.assertEqual(repos.staff.get(2).spaceport_id, 1)
            self.assertEqual(repos.pads.get(1).spaceport_id, 1)
            batch = repos.fuel.get_batch(1)
            self.assertEqual(batch.initial_mass, Mass.from_kg(180000))
            self.assertEqual(repos.fuel.used_mass(1), Mass.from_kg("38012.3"))
            self.assertEqual([p.reason for p in repos.journal.postponements(launch_id=1)],
                             [PostponeReason.TECHNICAL, PostponeReason.ORGANIZATIONAL])
            self.assertEqual(repos.journal.postponements(launch_id=1)[0].comment, "НС на 312 с")
            self.assertEqual(db.query("PRAGMA foreign_key_check"), [])
            db.init_schema()                                             # повторное открытие ничего не ломает
            self.assertEqual(len(repos.journal.postponements()), 2)
            db.close()

    def test_failed_migration_keeps_lab3_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lab3.db"
            self.make_lab3_db(path)
            conn = sqlite3.connect(path)                                  # битая ссылка: пуск на несуществующей площадке
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.execute("UPDATE launch SET pad_id = 99")
            conn.commit()
            conn.close()
            with self.assertRaises(sqlite3.IntegrityError):
                Database(path).init_schema()
            conn = sqlite3.connect(path)
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT initial_volume FROM fuel_batch").fetchone()[0], 180.0)
            conn.close()

    def test_lab3_scenarios_on_migrated_database(self):
        """Регрессия: на перенесенной базе работают сценарии ЛР3 (регистрация, заправка, отчет)."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lab3.db"
            self.make_lab3_db(path)
            db = Database(path)
            db.init_schema()
            db.execute("UPDATE staff SET password_hash = ?, password_salt = ?", PasswordHasher.hash(PWD))
            s = Services(Repositories(db), lambda: datetime(2026, 9, 6, 8, 0))
            head, eng = s.auth.login("head", PWD), s.auth.login("eng", PWD)
            s.staff.create_staff(head, "f", "Заправщик", Role.FUEL_ENGINEER, PWD, 1)
            s.fuel.refuel(s.auth.login("f", PWD), 1, 1, "1.5")
            self.assertEqual(s.fuel.list_batches(head)[0][1], Mass.from_kg("141986.2"))
            s.launches.register_launch(head, 1, 1, "Новый", "ГСО", datetime(2026, 9, 20), datetime(2026, 9, 20, 1))
            self.assertTrue(s.reports.build(head, "fuel", PERIOD).rows)
            self.assertEqual(len(s.launches.list_launches(eng)), 2)
            db.close()

if __name__ == "__main__":
    unittest.main()
