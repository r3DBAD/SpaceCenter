"""Тесты сервисов: права доступа, бизнес-правила, вычисляемые показатели, отчёты, SQL-инъекции."""
import unittest
from datetime import date, timedelta

from helpers import PWD, World
from lcc.model.enums import LaunchStatus, MaintenanceKind, PadState, PostponeReason
from lcc.model.errors import AccessDeniedError, AuthenticationError, BusinessRuleError, ValidationError
from lcc.model.reports import Period


class AuthTest(unittest.TestCase):
    def setUp(self):
        self.w = World()

    def test_wrong_password_and_lockout(self):
        for _ in range(5):
            with self.assertRaises(AuthenticationError):
                self.w.s.auth.login("eng", "wrong")
        with self.assertRaises(AuthenticationError):       # заблокирован даже с верным паролем
            self.w.s.auth.login("eng", PWD)

    def test_sql_injection_in_login(self):
        with self.assertRaises(AuthenticationError):
            self.w.s.auth.login("' OR '1'='1", "' OR '1'='1")

    def test_password_not_stored_plain(self):
        row = self.w.db.query_one("SELECT password_hash, password_salt FROM staff WHERE login='head'")
        self.assertNotEqual(row["password_hash"], PWD)
        self.assertEqual(len(row["password_hash"]), 64)

    def test_first_admin_only_once(self):
        with self.assertRaises(BusinessRuleError):
            self.w.s.staff.create_staff(None, "hacker", "X", "head", "abc12345")


class AccessTest(unittest.TestCase):
    """Права проверяются в Model — вызов сервиса в обход меню не помогает."""

    def setUp(self):
        self.w = World()

    def test_roles(self):
        w = self.w
        start = w.clock.now + timedelta(days=1)
        with self.assertRaises(AccessDeniedError):
            w.s.launches.register_launch(w.meteo, w.vt.id, w.pad.id, "ПН", "ГСО", start, start + timedelta(hours=1))
        launch = w.new_launch()
        with self.assertRaises(AccessDeniedError):
            w.s.launches.advance_stage(w.fuel, launch.id)          # сборку отмечает инженер ТК
        with self.assertRaises(AccessDeniedError):
            w.s.reports.build(w.eng, "fuel", Period(date(2026, 1, 1), date(2026, 12, 31)))
        with self.assertRaises(AccessDeniedError):
            w.s.launches.postpone(w.meteo, launch.id, PostponeReason.TECHNICAL,
                                  launch.window.start + timedelta(days=1), launch.window.end + timedelta(days=1))
        with self.assertRaises(AccessDeniedError):
            w.s.staff.create_staff(w.eng, "x", "X", "head", "abc12345")


class LaunchFlowTest(unittest.TestCase):
    def setUp(self):
        self.w = World()

    def test_window_in_past_rejected(self):
        w = self.w
        with self.assertRaises(ValidationError):
            w.s.launches.register_launch(w.head, w.vt.id, w.pad.id, "ПН", "ГСО",
                                         w.clock.now - timedelta(hours=2), w.clock.now - timedelta(hours=1))

    def test_pad_capacity(self):
        w = self.w
        first = w.new_launch()
        second = w.new_launch(days_ahead=6)                 # регистрация допустима, площадка ещё свободна
        w.s.launches.advance_stage(w.eng, first.id)        # первый занял площадку (лимит 1)
        with self.assertRaises(BusinessRuleError):
            w.s.launches.advance_stage(w.eng, second.id)
        with self.assertRaises(BusinessRuleError):
            w.new_launch(days_ahead=7)

    def test_fueling_requires_both_components_and_stage(self):
        w = self.w
        l = w.new_launch()
        with self.assertRaises(BusinessRuleError):          # ещё не этап заправки
            w.s.fuel.refuel(w.fuel, l.id, w.kero.id, 10)
        w.to_fueling(l)
        w.s.fuel.refuel(w.fuel, l.id, w.kero.id, 30)
        with self.assertRaises(BusinessRuleError):          # нет окислителя
            w.s.launches.advance_stage(w.fuel, l.id)
        with self.assertRaises(BusinessRuleError):          # больше остатка (100 - 30)
            w.s.fuel.refuel(w.fuel, l.id, w.kero.id, 71)
        w.s.fuel.refuel(w.fuel, l.id, w.lox.id, 50)
        self.assertIs(w.s.launches.advance_stage(w.fuel, l.id), LaunchStatus.PRELAUNCH_CHECK)
        remaining = dict((b.batch_no, rest) for b, rest in w.s.fuel.list_batches(w.fuel))
        self.assertEqual(remaining["RG-1"], 70)

    def test_expired_batch(self):
        w = self.w
        l = w.new_launch(days_ahead=40)
        w.to_fueling(l)
        w.clock.advance(days=31)                            # LOX-1 годен 30 дней
        with self.assertRaises(BusinessRuleError):
            w.s.fuel.refuel(w.fuel, l.id, w.lox.id, 10)

    def test_weather_gate_and_postpone(self):
        w = self.w
        l = w.new_launch()
        w.to_fueling(l)
        w.s.fuel.refuel(w.fuel, l.id, w.kero.id, 30)
        w.s.fuel.refuel(w.fuel, l.id, w.lox.id, 70)
        w.s.launches.advance_stage(w.fuel, l.id)
        with self.assertRaises(BusinessRuleError):          # нет метеоданных
            w.s.launches.advance_stage(w.eng, l.id)
        decision = w.s.weather.record_observation(w.meteo, l.id, 20, 10, 5, 1000, True)
        self.assertFalse(decision.allowed)
        self.assertEqual(len(decision.reasons), 2)
        with self.assertRaises(BusinessRuleError):          # метео не в норме
            w.s.launches.advance_stage(w.eng, l.id)
        w.s.launches.postpone(w.meteo, l.id, PostponeReason.WEATHER, l.window.start + timedelta(days=1),
                              l.window.end + timedelta(days=1), "; ".join(decision.reasons))
        card = w.s.launches.launch_card(w.head, l.id)
        self.assertIs(card.launch.status, LaunchStatus.PRELAUNCH_CHECK)   # этап сохранён
        self.assertEqual(len(card.journal), 1)
        w.clock.advance(hours=4)                            # старое наблюдение устарело
        with self.assertRaises(BusinessRuleError):
            w.s.launches.advance_stage(w.eng, l.id)
        w.s.weather.record_observation(w.meteo, l.id, 5, 10, 5, 1000, False)
        self.assertIs(w.s.launches.advance_stage(w.eng, l.id), LaunchStatus.READY)

    def test_pad_maintenance_cycle(self):
        w = self.w
        for i in range(2):                                   # cycle_limit = 2
            l = w.new_launch(days_ahead=1)
            w.to_ready(l)
            w.clock.now = l.window.start
            w.s.launches.record_result(w.head, l.id, True, 10)
            w.clock.advance(hours=2)
        pads = dict((p.code, (p, u)) for p, u in w.s.reference.list_pads(w.head))
        pad, usage = pads["SK-1"]
        self.assertEqual(usage.cycles_since_maintenance, 2)
        self.assertIs(pad.state(usage), PadState.MAINTENANCE_REQUIRED)
        with self.assertRaises(BusinessRuleError):          # площадка «Требует ТО» — регистрация запрещена
            w.new_launch(days_ahead=3)
        w.s.pads.open_maintenance(w.eng, w.pad.id, MaintenanceKind.INSPECTION)
        w.clock.advance(hours=5)
        w.s.pads.close_maintenance(w.eng, w.pad.id)
        pad, usage = dict((p.code, (p, u)) for p, u in w.s.reference.list_pads(w.head))["SK-1"]
        self.assertEqual(usage.cycles_since_maintenance, 0)
        self.assertIs(pad.state(usage), PadState.READY)

    def test_sql_injection_in_text_fields(self):
        w = self.w
        payload = "X'); DROP TABLE launch; --"
        start = w.clock.now + timedelta(days=1)
        l = w.s.launches.register_launch(w.head, w.vt.id, w.pad.id, payload, "ГСО", start, start + timedelta(hours=1))
        self.assertEqual(w.s.launches.launch_card(w.head, l.id).launch.payload, payload)
        self.assertEqual(len(w.s.launches.list_launches(w.head)), 1)


class ReportsTest(unittest.TestCase):
    def test_reports_after_launch(self):
        w = World()
        l = w.new_launch(days_ahead=1)
        w.to_ready(l)
        w.clock.now = l.window.start
        w.s.telemetry.register_incident(w.tm, l.id, None, "Потеря сигнала", "Переход на резерв")
        w.s.launches.record_result(w.head, l.id, True, 12)
        period = Period(date(2026, 8, 1), date(2026, 9, 30))
        r1 = w.s.reports.build(w.head, "launches", period)
        self.assertEqual(r1.rows[0][:4], ["Союз-2.1б", "средний", 1, 1])
        r2 = w.s.reports.build(w.head, "fuel", period)
        self.assertEqual({row[0]: row[6] for row in r2.rows}, {"RG-1": "70", "LOX-1": "130"})
        r3 = w.s.reports.build(w.head, "pads", period)
        self.assertEqual(r3.rows[0][3], 1)
        r4 = w.s.reports.build(w.head, "incidents", period)
        self.assertIn(["НС (потеря сигнала)", "не определена", 1], r4.rows)

    def test_period_validation(self):
        with self.assertRaises(ValidationError):
            Period(date(2026, 9, 2), date(2026, 9, 1))


if __name__ == "__main__":
    unittest.main()
