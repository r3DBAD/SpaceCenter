"""Слой Controller: JSON API для веб-интерфейса на Vue.

Контроллер принимает HTTP-запрос, разбирает формат (JSON, даты ISO), вызывает
сервисы Model и сериализует результат. Бизнес-правил и SQL здесь нет; ошибки
Model возвращаются клиенту текстом с кодом 4xx.

Запуск: ``python -m lcc --db demo.db`` → http://127.0.0.1:8000
"""
from __future__ import annotations

import json
import mimetypes
import re
import secrets
from dataclasses import dataclass, field
from datetime import date, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from ..model.enums import (FINAL_STATUSES, ROLE_PERMISSIONS, FuelComponent, LaunchStatus, MaintenanceKind,
                           PadState, PostponeReason, Role, VehicleClass)
from ..model.errors import (AccessDeniedError, AuthenticationError, DomainError, NotFoundError,
                            ValidationError)
from ..model.reports import Period
from ..model.services import Services
from ..view.csv_export import CsvExporter

STATIC_DIR = Path(__file__).resolve().parents[1] / "view" / "web"
#: Отдаются только эти файлы — защита от выхода за пределы каталога (path traversal).
STATIC_FILES = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css",
                "/vendor/vue.global.prod.js": "vendor/vue.global.prod.js"}
DT = "%d.%m.%Y %H:%M"
MAX_BODY = 64 * 1024


@dataclass
class Response:
    status: int
    body: bytes
    content_type: str = "application/json; charset=utf-8"
    headers: dict = field(default_factory=dict)


def _json(status: int, data) -> Response:
    return Response(status, json.dumps(data, ensure_ascii=False).encode("utf-8"))


def _parse_dt(value, field_name: str) -> datetime:
    """Формат <input type="datetime-local">: YYYY-MM-DDTHH:MM."""
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        raise ValidationError(f"Поле «{field_name}»: ожидается дата и время") from None


def _parse_date(value, field_name: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise ValidationError(f"Поле «{field_name}»: ожидается дата") from None


class WebApi:
    """Маршрутизация запросов API. Не зависит от HTTP-сервера — удобно тестировать напрямую."""

    def __init__(self, services: Services):
        self.s = services
        self._sessions: dict[str, object] = {}        # токен → Session (в памяти процесса)
        self._routes = [
            ("POST", r"/api/login", self.login, False),
            ("POST", r"/api/logout", self.logout, True),
            ("GET", r"/api/me", self.me, True),
            ("GET", r"/api/reference", self.reference, True),
            ("GET", r"/api/launches", self.launches, True),
            ("POST", r"/api/launches", self.register, True),
            ("GET", r"/api/launches/(\d+)", self.card, True),
            ("POST", r"/api/launches/(\d+)/advance", self.advance, True),
            ("POST", r"/api/launches/(\d+)/postpone", self.postpone, True),
            ("POST", r"/api/launches/(\d+)/cancel", self.cancel, True),
            ("POST", r"/api/launches/(\d+)/result", self.result, True),
            ("POST", r"/api/launches/(\d+)/refuel", self.refuel, True),
            ("POST", r"/api/launches/(\d+)/weather", self.weather, True),
            ("POST", r"/api/launches/(\d+)/incidents", self.incident, True),
            ("GET", r"/api/fuel", self.fuel, True),
            ("POST", r"/api/fuel", self.receive_batch, True),
            ("GET", r"/api/staff", self.staff, True),
            ("POST", r"/api/staff", self.add_staff, True),
            ("POST", r"/api/staff/(\d+)/deactivate", self.deactivate_staff, True),
            ("POST", r"/api/pads", self.add_pad, True),
            ("POST", r"/api/pads/(\d+)/maintenance", self.open_maintenance, True),
            ("POST", r"/api/pads/(\d+)/maintenance/close", self.close_maintenance, True),
            ("POST", r"/api/pads/(\d+)/faulty", self.mark_faulty, True),
            ("POST", r"/api/vehicle-types", self.add_vehicle_type, True),
            ("POST", r"/api/channels", self.add_channel, True),
            ("GET", r"/api/reports", self.report_list, True),
            ("GET", r"/api/reports/(\w+)", self.report, True),
            ("GET", r"/api/reports/(\w+)\.csv", self.report_csv, True),
        ]

    # ------------------------------------------------------------------ диспетчер
    def handle(self, method: str, url: str, headers: dict, body: bytes) -> Response:
        parsed = urlparse(url)
        path = parsed.path
        if method == "GET" and path in STATIC_FILES:
            return self._static(STATIC_FILES[path])
        for m, pattern, action, needs_auth in self._routes:
            match = re.fullmatch(pattern, path)
            if m != method or not match:
                continue
            try:
                session = self._session(headers) if needs_auth else None
                data = json.loads(body.decode("utf-8")) if body else {}
                if not isinstance(data, dict):
                    raise ValidationError("Ожидается JSON-объект")
                query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
                return action(session, *match.groups(), data=data, query=query, headers=headers)
            except json.JSONDecodeError:
                return _json(400, {"error": "Некорректный JSON"})
            except AuthenticationError as e:
                return _json(401, {"error": str(e)})
            except AccessDeniedError as e:
                return _json(403, {"error": str(e)})
            except NotFoundError as e:
                return _json(404, {"error": str(e)})
            except DomainError as e:                      # ValidationError, BusinessRuleError
                return _json(400, {"error": str(e)})
            except (KeyError, TypeError, ValueError):
                return _json(400, {"error": "Не заполнены обязательные поля запроса"})
        return _json(404, {"error": "Не найдено"})

    def _session(self, headers: dict):
        auth = headers.get("authorization", "")
        token = auth[7:] if auth.startswith("Bearer ") else ""
        session = self._sessions.get(token)
        if session is None:
            raise AuthenticationError("Требуется вход в систему")
        return session

    @staticmethod
    def _static(name: str) -> Response:
        path = STATIC_DIR / name
        ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        return Response(200, path.read_bytes(), ctype)

    # ------------------------------------------------------------------ сериализация
    @staticmethod
    def _launch(l, vt_names, pad_codes) -> dict:
        return {"id": l.id, "vehicle": vt_names.get(l.vehicle_type_id, "?"), "vehicle_type_id": l.vehicle_type_id,
                "payload": l.payload, "orbit": l.target_orbit, "pad": pad_codes.get(l.pad_id, "?"),
                "window": f"{l.window.start:{DT}} – {l.window.end:%H:%M}",
                "window_start": l.window.start.isoformat(timespec="minutes"),
                "window_end": l.window.end.isoformat(timespec="minutes"),
                "status": l.status.value, "status_title": l.status.title, "final": l.status in FINAL_STATUSES}

    def _names(self, session):
        vts = {v.id: v.name for v in self.s.reference.list_vehicle_types(session)}
        pads = {p.id: p.code for p, _ in self.s.reference.list_pads(session)}
        return vts, pads

    # ------------------------------------------------------------------ вход
    def login(self, _session, data, **_):
        session = self.s.auth.login(data.get("login", ""), data.get("password", ""))
        token = secrets.token_urlsafe(32)
        self._sessions[token] = session
        return _json(200, {"token": token})

    def logout(self, _session, headers, **_):
        self._sessions.pop(headers.get("authorization", "")[7:], None)
        return _json(200, {"ok": True})

    def me(self, session, **_):
        st = session.staff
        return _json(200, {"login": st.login, "full_name": st.full_name, "role": st.role.value,
                           "role_title": st.role.title,
                           "permissions": sorted(p.value for p in ROLE_PERMISSIONS[st.role])})

    def reference(self, session, **_):
        data = {"vehicle_types": [{"id": v.id, "name": v.name, "class": v.vehicle_class.title}
                                  for v in self.s.reference.list_vehicle_types(session)],
                "pads": [{"id": p.id, "code": p.code, "name": p.name,
                          "preparations": f"{u.active_preparations}/{p.max_concurrent}",
                          "cycles": f"{u.cycles_since_maintenance}/{p.cycle_limit}",
                          "hours": f"{u.hours_since_maintenance:g}/{p.hours_limit:g}",
                          "state": p.state(u).title, "state_key": p.state(u).value,
                          "in_maintenance": p.state(u) is PadState.IN_MAINTENANCE}
                         for p, u in self.s.reference.list_pads(session)],
                "postpone_reasons": [{"value": r.value, "title": r.title} for r in PostponeReason],
                "maintenance_kinds": [{"value": k.value, "title": k.title} for k in MaintenanceKind],
                "vehicle_classes": [{"value": c.value, "title": c.title} for c in VehicleClass],
                "roles": [{"value": r.value, "title": r.title} for r in Role],
                "fuel_components": [{"value": c.value, "title": c.title} for c in FuelComponent]}
        try:
            data["channels"] = [{"id": c.id, "no": c.channel_no, "system": c.vehicle_system,
                                 "parameter": c.parameter} for c in self.s.reference.list_channels(session)]
        except AccessDeniedError:
            data["channels"] = []
        lim = self.s.weather.limits
        data["weather_limits"] = (f"ветер у земли ≤ {lim.max_ground_wind:g} м/с, на высотах ≤ {lim.max_altitude_wind:g} м/с, "
                                  f"t° {lim.min_temperature:g}…{lim.max_temperature:g} °C, "
                                  f"облачность ≥ {lim.min_cloud_base:g} м, без грозы")
        return _json(200, data)

    # ------------------------------------------------------------------ пуски
    def launches(self, session, **_):
        vts, pads = self._names(session)
        return _json(200, [self._launch(l, vts, pads) for l in self.s.launches.list_launches(session)])

    def card(self, session, launch_id, **_):
        c = self.s.launches.launch_card(session, int(launch_id))
        vts, pads = self._names(session)
        l = c.launch
        next_stage = None
        if l.status not in FINAL_STATUSES and l.status is not LaunchStatus.READY:
            next_stage = self.s.launches.next_stage_title(l)
        return _json(200, {
            **self._launch(l, vts, pads),
            "vehicle_class": c.vehicle.vehicle_class.title, "pad_name": c.pad.name,
            "cancel_reason": l.cancel_reason, "next_stage": next_stage,
            "fuel": [{"component": k.title, "volume": v} for k, v in c.fuel_loaded.items()],
            "last_weather": f"{c.last_weather.observed_at:{DT}}" if c.last_weather else None,
            "stage_log": [{"no": n, "status": s.title, "at": f"{t:{DT}}", "who": who} for n, s, t, who in c.stage_log],
            "journal": [{"at": f"{r.recorded_at:{DT}}", "category": r.category, "text": r.summary()}
                        for r in c.journal]})

    def register(self, session, data, **_):
        l = self.s.launches.register_launch(session, int(data["vehicle_type_id"]), int(data["pad_id"]),
                                            data.get("payload", ""), data.get("target_orbit", ""),
                                            _parse_dt(data.get("window_start"), "Начало окна"),
                                            _parse_dt(data.get("window_end"), "Конец окна"))
        return _json(201, {"id": l.id, "message": f"Пуск №{l.id} зарегистрирован, статус «{l.status.title}»"})

    def advance(self, session, launch_id, **_):
        status = self.s.launches.advance_stage(session, int(launch_id))
        return _json(200, {"message": f"Новый статус: «{status.title}»"})

    def postpone(self, session, launch_id, data, **_):
        rec = self.s.launches.postpone(session, int(launch_id), data.get("reason", ""),
                                       _parse_dt(data.get("new_start"), "Новое начало окна"),
                                       _parse_dt(data.get("new_end"), "Новый конец окна"), data.get("comment", ""))
        return _json(200, {"message": f"Пуск перенесён: {rec.summary()}"})

    def cancel(self, session, launch_id, data, **_):
        self.s.launches.cancel(session, int(launch_id), data.get("reason", ""))
        return _json(200, {"message": "Пуск отменён"})

    def result(self, session, launch_id, data, **_):
        status = self.s.launches.record_result(session, int(launch_id), bool(data.get("success")),
                                               data.get("pad_hours"))
        return _json(200, {"message": f"Статус «{status.title}», цикл площадки учтён"})

    # ------------------------------------------------------------------ топливо, метео, телеметрия
    def fuel(self, session, **_):
        return _json(200, [{"id": b.id, "batch_no": b.batch_no, "component": b.component.title, "grade": b.grade,
                            "volume": b.initial_volume, "remaining": rest,
                            "expires_on": f"{b.expires_on:%d.%m.%Y}"} for b, rest in self.s.fuel.list_batches(session)])

    def receive_batch(self, session, data, **_):
        b = self.s.fuel.receive_batch(session, data.get("batch_no", ""), data.get("component", ""),
                                      data.get("grade", ""), data.get("volume"),
                                      _parse_date(data.get("produced_on"), "Дата изготовления"),
                                      _parse_date(data.get("expires_on"), "Срок годности"))
        return _json(201, {"message": f"Партия {b.batch_no} принята"})

    def refuel(self, session, launch_id, data, **_):
        rest = self.s.fuel.refuel(session, int(launch_id), int(data["batch_id"]), data.get("volume"))
        return _json(200, {"message": f"Расход списан, остаток партии {rest:g} т"})

    def weather(self, session, launch_id, data, **_):
        d = self.s.weather.record_observation(session, int(launch_id), data.get("ground_wind"),
                                              data.get("altitude_wind"), data.get("temperature"),
                                              data.get("cloud_base"), bool(data.get("thunderstorm")))
        return _json(200, {"allowed": d.allowed, "reasons": list(d.reasons)})

    def incident(self, session, launch_id, data, **_):
        ch = data.get("channel_id")
        i = self.s.telemetry.register_incident(session, int(launch_id), int(ch) if ch else None,
                                               data.get("description", ""), data.get("measures", ""))
        return _json(201, {"message": f"НС зарегистрирована в {i.recorded_at:{DT}}"})

    # ------------------------------------------------------------------ персонал и справочники
    def staff(self, session, **_):
        return _json(200, [{"id": st.id, "login": st.login, "full_name": st.full_name, "role": st.role.title,
                            "active": st.is_active} for st in self.s.staff.list_staff(session)])

    def add_staff(self, session, data, **_):
        st = self.s.staff.create_staff(session, data.get("login", ""), data.get("full_name", ""),
                                       data.get("role", ""), data.get("password", ""))
        return _json(201, {"message": f"Сотрудник {st.login} ({st.role.title}) добавлен"})

    def deactivate_staff(self, session, staff_id, **_):
        self.s.staff.deactivate(session, int(staff_id))
        return _json(200, {"message": "Учётная запись заблокирована"})

    def add_pad(self, session, data, **_):
        p = self.s.reference.add_pad(session, data.get("code", ""), data.get("name", ""), data.get("max_concurrent"),
                                     data.get("cycle_limit"), data.get("hours_limit"))
        return _json(201, {"message": f"Площадка {p.code} добавлена"})

    def open_maintenance(self, session, pad_id, data, **_):
        rec = self.s.pads.open_maintenance(session, int(pad_id), data.get("kind", ""), data.get("notes", ""))
        return _json(200, {"message": f"ТО №{rec.maint_no} открыто, площадка на обслуживании"})

    def close_maintenance(self, session, pad_id, **_):
        self.s.pads.close_maintenance(session, int(pad_id))
        return _json(200, {"message": "ТО закрыто, счётчики циклов и моточасов обнулены"})

    def mark_faulty(self, session, pad_id, **_):
        self.s.pads.mark_faulty(session, int(pad_id))
        return _json(200, {"message": "Площадка помечена неисправной"})

    def add_vehicle_type(self, session, data, **_):
        v = self.s.reference.add_vehicle_type(session, data.get("name", ""), data.get("vehicle_class", ""),
                                              data.get("stages_count"))
        return _json(201, {"message": f"Тип РН «{v.name}» добавлен"})

    def add_channel(self, session, data, **_):
        ch = self.s.reference.add_channel(session, data.get("channel_no", ""), data.get("vehicle_system", ""),
                                          data.get("parameter", ""), data.get("frequency_hz"))
        return _json(201, {"message": f"Канал {ch.channel_no} добавлен"})

    # ------------------------------------------------------------------ отчёты
    def report_list(self, session, **_):
        return _json(200, [{"key": k, "title": t} for k, t in self.s.reports.available()])

    def _build(self, session, key, query):
        period = Period(_parse_date(query.get("from"), "Начало периода"), _parse_date(query.get("to"), "Конец периода"))
        return self.s.reports.build(session, key, period)

    def report(self, session, key, query, **_):
        r = self._build(session, key, query)
        return _json(200, {"title": r.title, "period": str(r.period), "columns": r.columns,
                           "rows": [[str(v) for v in row] for row in r.rows], "notes": r.notes})

    def report_csv(self, session, key, query, **_):
        r = self._build(session, key, query)
        name = CsvExporter.file_name(r)
        return Response(200, CsvExporter.to_text(r).encode("utf-8-sig"), "text/csv; charset=utf-8",
                        {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})


class _Handler(BaseHTTPRequestHandler):
    api: WebApi = None          # задаётся в run_server

    def _dispatch(self, method: str):
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            resp = _json(413, {"error": "Слишком большой запрос"})
        else:
            body = self.rfile.read(length) if length else b""
            headers = {k.lower(): v for k, v in self.headers.items()}
            resp = self.api.handle(method, self.path, headers, body)
        self.send_response(resp.status)
        self.send_header("Content-Type", resp.content_type)
        self.send_header("Content-Length", str(len(resp.body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cache-Control", "no-store")
        for k, v in resp.headers.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(resp.body)

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def log_message(self, fmt, *args):          # краткий лог без тела запросов (там пароли)
        print(f"{self.address_string()} {self.command} {urlparse(self.path).path} → {args[1] if len(args) > 1 else ''}")


def run_server(services: Services, host: str = "127.0.0.1", port: int = 8000, open_browser: bool = False) -> None:
    """Однопоточный сервер: соединение SQLite используется в одном потоке."""
    _Handler.api = WebApi(services)
    server = HTTPServer((host, port), _Handler)
    url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '') else host}:{port}"
    print(f"Веб-интерфейс: {url}  (Ctrl+C — остановить)")
    if open_browser:
        import webbrowser
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено")
    finally:
        server.server_close()
