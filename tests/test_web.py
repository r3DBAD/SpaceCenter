"""Тесты веб-контроллера (JSON API) — вызываются напрямую через WebApi.handle, без сети."""
import json
import unittest
from datetime import timedelta

from helpers import PWD, World
from lcc.controller.web import WebApi


class WebApiTest(unittest.TestCase):
    def setUp(self):
        self.w = World()
        self.api = WebApi(self.w.s)

    def call(self, method, url, body=None, token=None):
        headers = {"authorization": f"Bearer {token}"} if token else {}
        resp = self.api.handle(method, url, headers, json.dumps(body).encode() if body is not None else b"")
        data = json.loads(resp.body) if resp.content_type.startswith("application/json") else resp.body
        return resp.status, data

    def login(self, login):
        status, data = self.call("POST", "/api/login", {"login": login, "password": PWD})
        self.assertEqual(status, 200)
        return data["token"]

    def test_auth_required_and_bad_password(self):
        self.assertEqual(self.call("GET", "/api/launches")[0], 401)
        self.assertEqual(self.call("POST", "/api/login", {"login": "head", "password": "x"})[0], 401)
        self.assertEqual(self.call("GET", "/api/me", token="forged")[0], 401)

    def test_static_whitelist(self):
        self.assertEqual(self.call("GET", "/")[0], 200)
        self.assertEqual(self.call("GET", "/../lcc/model/schema.sql")[0], 404)
        self.assertEqual(self.call("GET", "/vendor/../../model/schema.sql")[0], 404)

    def test_register_validation_and_rights(self):
        head, meteo = self.login("head"), self.login("meteo")
        start = self.w.clock.now + timedelta(days=3)
        body = {"vehicle_type_id": self.w.vt.id, "pad_id": self.w.pad.id, "payload": "", "target_orbit": "ГСО",
                "window_start": start.isoformat(), "window_end": (start + timedelta(hours=1)).isoformat()}
        status, data = self.call("POST", "/api/launches", body, head)
        self.assertEqual((status, data["error"]), (400, "Поле «Полезная нагрузка» обязательно для заполнения"))
        body["payload"] = "Спутник"
        self.assertEqual(self.call("POST", "/api/launches", body, meteo)[0], 403)
        status, data = self.call("POST", "/api/launches", body, head)
        self.assertEqual(status, 201)
        status, card = self.call("GET", f"/api/launches/{data['id']}", token=head)
        self.assertEqual((status, card["status"]), (200, "planned"))
        self.assertEqual(self.call("GET", "/api/launches/999", token=head)[0], 404)

    def test_scenario_via_api(self):
        l = self.w.new_launch()
        eng, fuel, meteo = self.login("eng"), self.login("fuel"), self.login("meteo")
        for _ in range(3):
            self.assertEqual(self.call("POST", f"/api/launches/{l.id}/advance", {}, eng)[0], 200)
        self.assertEqual(self.call("POST", f"/api/launches/{l.id}/refuel",
                                   {"batch_id": self.w.kero.id, "volume": "много"}, fuel)[0], 400)
        for batch, vol in ((self.w.kero.id, 30), (self.w.lox.id, 70)):
            self.assertEqual(self.call("POST", f"/api/launches/{l.id}/refuel", {"batch_id": batch, "volume": vol},
                                       fuel)[0], 200)
        self.call("POST", f"/api/launches/{l.id}/advance", {}, fuel)
        status, d = self.call("POST", f"/api/launches/{l.id}/weather",
                              {"ground_wind": 25, "altitude_wind": 5, "temperature": 5, "cloud_base": 900,
                               "thunderstorm": False}, meteo)
        self.assertEqual((status, d["allowed"]), (200, False))
        status, rep = self.call("GET", "/api/reports/fuel?from=2026-08-01&to=2026-09-30", token=self.login("head"))
        self.assertEqual(status, 200)
        self.assertIn(["RG-1", "Горючее", "РГ-1", "100", "30", "1", "70", "30.0", "10.12.2026"], rep["rows"])
        status, csv_body = self.call("GET", "/api/reports/fuel.csv?from=2026-08-01&to=2026-09-30",
                                     token=self.login("head"))
        self.assertEqual(status, 200)
        self.assertIn("Расход топлива по партиям", csv_body.decode("utf-8-sig"))

    def test_bad_json_and_period(self):
        head = self.login("head")
        resp = self.api.handle("POST", "/api/launches", {"authorization": f"Bearer {head}"}, b"{not json")
        self.assertEqual(resp.status, 400)
        self.assertEqual(self.call("GET", "/api/reports/fuel?from=2026-09-30&to=2026-09-01", token=head)[0], 400)


if __name__ == "__main__":
    unittest.main()
