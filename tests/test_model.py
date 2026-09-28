"""Модульные тесты сущностей: валидация и инварианты на уровне Model."""
import unittest
from datetime import date, datetime, timedelta

import helpers  # noqa: F401  (настраивает sys.path)
from lcc.model.entities import FuelBatch, Launch, LaunchPad, LaunchWindow, PadUsage, TelemetryChannel
from lcc.model.enums import FuelComponent, LaunchStatus, PadState, PostponeReason
from lcc.model.errors import BusinessRuleError, ValidationError
from lcc.model.security import PasswordHasher
from lcc.model.weather import WeatherLimits, WeatherObservation

T = datetime(2026, 10, 1, 10, 0)


def launch(**kw):
    args = dict(vehicle_type_id=1, pad_id=1, created_by=1, payload="ПН", target_orbit="ГСО",
                window=LaunchWindow(T, T + timedelta(hours=1)))
    args.update(kw)
    return Launch(**args)


class LaunchWindowTest(unittest.TestCase):
    def test_start_must_be_before_end(self):
        with self.assertRaises(ValidationError):
            LaunchWindow(T, T)

    def test_duration_limits(self):
        with self.assertRaises(ValidationError):
            LaunchWindow(T, T + timedelta(hours=25))
        with self.assertRaises(ValidationError):
            LaunchWindow("2026-10-01", T)


class LaunchTest(unittest.TestCase):
    def test_required_fields(self):
        with self.assertRaises(ValidationError):
            launch(payload="   ")
        with self.assertRaises(ValidationError):
            launch(vehicle_type_id=-1)

    def test_strict_stage_order(self):
        l = launch()
        path = [l.advance() for _ in range(5)]
        self.assertEqual(path, [LaunchStatus.ASSEMBLY, LaunchStatus.TRANSPORT, LaunchStatus.FUELING,
                                LaunchStatus.PRELAUNCH_CHECK, LaunchStatus.READY])
        with self.assertRaises(BusinessRuleError):
            l.advance()

    def test_result_only_from_ready_and_inside_window(self):
        l = launch()
        with self.assertRaises(BusinessRuleError):
            l.complete(True, 10, T)
        for _ in range(5):
            l.advance()
        with self.assertRaises(BusinessRuleError):
            l.complete(True, 10, T - timedelta(minutes=1))
        with self.assertRaises(ValidationError):
            l.complete(True, -5, T)
        self.assertIs(l.complete(False, 10, T), LaunchStatus.FAILED)

    def test_postpone_moves_window_forward_only(self):
        l = launch(launch_id=7)
        with self.assertRaises(BusinessRuleError):
            l.postpone(LaunchWindow(T - timedelta(days=1), T), PostponeReason.WEATHER, "", 1, T)
        rec = l.postpone(LaunchWindow(T + timedelta(days=1), T + timedelta(days=1, hours=1)),
                         PostponeReason.WEATHER, "ветер", 1, T)
        self.assertEqual(l.window.start, T + timedelta(days=1))
        self.assertEqual(rec.old_window.start, T)

    def test_cannot_cancel_finished(self):
        l = launch()
        l.cancel("причина")
        with self.assertRaises(BusinessRuleError):
            l.cancel("ещё раз")


class PadTest(unittest.TestCase):
    def setUp(self):
        self.pad = LaunchPad("sk-1", "Площадка", 2, 3, 100)

    def test_state_is_computed(self):
        self.assertIs(self.pad.state(PadUsage()), PadState.READY)
        self.assertIs(self.pad.state(PadUsage(cycles_since_maintenance=3)), PadState.MAINTENANCE_REQUIRED)
        self.assertIs(self.pad.state(PadUsage(hours_since_maintenance=100)), PadState.MAINTENANCE_REQUIRED)
        self.assertIs(self.pad.state(PadUsage(has_open_maintenance=True)), PadState.IN_MAINTENANCE)
        self.pad.mark_faulty()
        self.assertIs(self.pad.state(PadUsage()), PadState.FAULTY)

    def test_capacity_limit(self):
        self.pad.ensure_can_accept(PadUsage(active_preparations=1))
        with self.assertRaises(BusinessRuleError):
            self.pad.ensure_can_accept(PadUsage(active_preparations=2))

    def test_invalid_limits(self):
        with self.assertRaises(ValidationError):
            LaunchPad("X", "Y", 0, 3, 100)
        with self.assertRaises(ValidationError):
            LaunchPad("X", "Y", 1, 3, -1)


class FuelBatchTest(unittest.TestCase):
    def setUp(self):
        self.b = FuelBatch("rg-1", FuelComponent.FUEL, "РГ-1", 100, date(2026, 1, 1), date(2026, 12, 31))

    def test_withdraw_rules(self):
        on = date(2026, 6, 1)
        self.assertEqual(self.b.ensure_can_withdraw(40, 50, on, FuelComponent.FUEL), 40)
        with self.assertRaises(BusinessRuleError):          # больше остатка
            self.b.ensure_can_withdraw(60, 50, on, FuelComponent.FUEL)
        with self.assertRaises(BusinessRuleError):          # просрочена
            self.b.ensure_can_withdraw(1, 0, date(2027, 1, 1), FuelComponent.FUEL)
        with self.assertRaises(BusinessRuleError):          # не тот компонент
            self.b.ensure_can_withdraw(1, 0, on, FuelComponent.OXIDIZER)
        with self.assertRaises(ValidationError):            # отрицательный объём
            self.b.ensure_can_withdraw(-1, 0, on, FuelComponent.FUEL)

    def test_expiry_after_production(self):
        with self.assertRaises(ValidationError):
            FuelBatch("X", FuelComponent.FUEL, "РГ-1", 10, date(2026, 5, 1), date(2026, 5, 1))


class WeatherTest(unittest.TestCase):
    def obs(self, **kw):
        a = dict(launch_id=1, observed_at=T, ground_wind=5, altitude_wind=10, temperature=5, cloud_base=1000,
                 thunderstorm=False, staff_id=1)
        a.update(kw)
        return WeatherObservation(**a)

    def test_limits(self):
        lim = WeatherLimits()
        self.assertEqual(lim.violations(self.obs()), [])
        self.assertEqual(len(lim.violations(self.obs(ground_wind=16, thunderstorm=True, cloud_base=100))), 3)

    def test_physical_validation(self):
        with self.assertRaises(ValidationError):
            self.obs(ground_wind=-1)
        with self.assertRaises(ValidationError):
            self.obs(temperature=150)


class OtherTest(unittest.TestCase):
    def test_channel_frequency_positive(self):
        with self.assertRaises(ValidationError):
            TelemetryChannel("TM-1", "ДУ", "давление", 0)

    def test_password_hash(self):
        h, salt = PasswordHasher.hash("secret123")
        self.assertNotIn("secret123", h)
        self.assertTrue(PasswordHasher.verify("secret123", h, salt))
        self.assertFalse(PasswordHasher.verify("secret124", h, salt))
        with self.assertRaises(ValidationError):
            PasswordHasher.validate_strength("12345678")


if __name__ == "__main__":
    unittest.main()
