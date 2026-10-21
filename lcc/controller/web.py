"""Слой Controller: JSON API для веб-интерфейса на Vue.

Контроллер принимает HTTP-запрос, разбирает формат (JSON, даты ISO), вызывает
сервисы Model и сериализует результат. Бизнес-правил и SQL здесь нет; ошибки
Model возвращаются клиенту текстом с кодом 4xx.

Запуск: ``python -m lcc --db demo.db`` → http://127.0.0.1:8000

ЛР4: маршруты космодромов, журналов, аудита и прогнозов. Сервер раз в минуту
запускает автоматический запрос прогноза (:meth:`Services.forecasts.run_auto`).
"""
from __future__ import annotations

import json
import mimetypes
import re
import secrets
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from ..model.enums import (FINAL_STATUSES, ROLE_PERMISSIONS, FuelComponent, LaunchStatus, MaintenanceKind,
                           PadState, Permission, PostponeReason, Role, VehicleClass)
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


def _opt_id(value, field_name: str = "Космодром") -> int | None:
    """Необязательный номер из запроса: пусто — не задан."""
    if value in (None, "", "all"):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValidationError(f"Поле «{field_name}»: ожидается номер") from None


def _period(query) -> tuple[datetime, datetime]:
    p = Period(_parse_date(query.get("from"), "Начало периода"), _parse_date(query.get("to"), "Конец периода"))
    return p.start, p.end


class WebApi:
    """Маршрутизация запросов API. Не зависит от HTTP-сервера — удобно тестировать напрямую."""

    #: ЛР5, утечка №1: без срока жизни токен закрытой без «Выхода» вкладки оставался в памяти навсегда.
    SESSION_TTL = 8 * 3600              # простой сеанса, с
    MAX_SESSIONS_PER_USER = 5           # старые сеансы сотрудника вытесняются новыми

    def __init__(self, services: Services, clock=time.monotonic):
        self.s = services
        self._clock = clock
        self._sessions: dict[str, tuple[object, float]] = {}   # токен → (Session, время последнего запроса)
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
            ("GET", r"/api/spaceports", self.spaceports, True),
            ("POST", r"/api/spaceports", self.add_spaceport, True),
            ("GET", r"/api/journal/incidents", self.journal_incidents, True),
            ("GET", r"/api/journal/postponements", self.journal_postponements, True),
            ("GET", r"/api/audit", self.audit, True),
            ("GET", r"/api/forecasts", self.forecasts, True),
            ("POST", r"/api/launches/(\d+)/forecast", self.request_forecast, True),
            ("POST", r"/api/launches/(\d+)/forecast/manual", self.manual_forecast, True),
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
        entry = self._sessions.get(token)
        now = self._clock()
        if entry is not None and now - entry[1] > self.SESSION_TTL:
            del self._sessions[token]
            raise AuthenticationError("Сеанс истек, войдите снова")
        if entry is None:
            raise AuthenticationError("Требуется вход в систему")
        self._sessions[token] = (entry[0], now)
        return entry[0]

    def _purge_sessions(self, staff_id: int) -> None:
        """Удалить истекшие сеансы и лишние сеансы этого сотрудника (оставить MAX − 1 самых свежих)."""
        now = self._clock()
        for token in [t for t, (_, seen) in self._sessions.items() if now - seen > self.SESSION_TTL]:
            del self._sessions[token]
        # устойчивая сортировка по времени: при равном времени раньше идет сеанс, созданный раньше
        own = sorted(((seen, t) for t, (sess, seen) in self._sessions.items() if sess.staff_id == staff_id),
                     key=lambda item: item[0])
        for _, token in own[:max(0, len(own) - self.MAX_SESSIONS_PER_USER + 1)]:
            del self._sessions[token]

    @staticmethod
    def _static(name: str) -> Response:
        path = STATIC_DIR / name
        ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        return Response(200, path.read_bytes(), ctype)

    # ------------------------------------------------------------------ сериализация
    @staticmethod
    def _launch(l, vt_names, pad_codes, pad_spaceports=None) -> dict:
        return {"id": l.id, "vehicle": vt_names.get(l.vehicle_type_id, "?"), "vehicle_type_id": l.vehicle_type_id,
                "payload": l.payload, "orbit": l.target_orbit, "pad": pad_codes.get(l.pad_id, "?"),
                "spaceport": (pad_spaceports or {}).get(l.pad_id, ""),
                "window": f"{l.window.start:{DT}} – {l.window.end:%H:%M}",
                "window_start": l.window.start.isoformat(timespec="minutes"),
                "window_end": l.window.end.isoformat(timespec="minutes"),
                "status": l.status.value, "status_title": l.status.title, "final": l.status in FINAL_STATUSES}

    def _names(self, session):
        vts = {v.id: v.name for v in self.s.reference.list_vehicle_types(session)}
        pads = {p.id: p.code for p in self.s.reference.list_pads_brief(session)}
        return vts, pads

    def _pad_spaceports(self, session) -> dict[int, str]:
        codes = {sp.id: sp.code for sp in self.s.reference.list_spaceports(session)}
        return {p.id: codes.get(p.spaceport_id, "") for p in self.s.reference.list_pads_brief(session)}

    @staticmethod
    def _forecast(rec) -> dict | None:
        if rec is None:
            return None
        f = rec.forecast
        return {"status": rec.status.value, "status_title": rec.status.title, "source": rec.source.title,
                "at": f"{rec.requested_at:{DT}}", "details": rec.details, "summary": rec.summary(),
                "values": None if f is None else (f"ветер у земли {f.ground_wind:g} м/с, на высотах {f.altitude_wind:g} м/с, "
                                                  f"t° {f.temperature:g} °C, гроза: {'да' if f.thunderstorm else 'нет'}")}

    # ------------------------------------------------------------------ вход
    def login(self, _session, data, **_):
        session = self.s.auth.login(data.get("login", ""), data.get("password", ""))
        token = secrets.token_urlsafe(32)
        self._purge_sessions(session.staff_id)
        self._sessions[token] = (session, self._clock())
        return _json(200, {"token": token})

    def logout(self, _session, headers, **_):
        self._sessions.pop(headers.get("authorization", "")[7:], None)
        return _json(200, {"ok": True})

    def me(self, session, **_):
        st = session.staff
        sp = None if session.is_global else self.s.reference.list_spaceports(session)[0]
        return _json(200, {"login": st.login, "full_name": st.full_name, "role": st.role.value,
                           "role_title": st.role.title, "global": session.is_global,
                           "spaceport": None if sp is None else {"id": sp.id, "code": sp.code, "name": sp.name},
                           "permissions": sorted(p.value for p in ROLE_PERMISSIONS[st.role])})

    def reference(self, session, **_):
        """Справочники для страницы. Инспектору расписание и площадки не отдаются (П11)."""
        data = {"spaceports": [{"id": sp.id, "code": sp.code, "name": sp.name, "region": sp.region}
                               for sp in self.s.reference.list_spaceports(session)],
                "postpone_reasons": [{"value": r.value, "title": r.title} for r in PostponeReason],
                "vehicle_types": [], "pads": [], "channels": []}
        if not session.can(Permission.VIEW_SCHEDULE):
            return _json(200, data)
        sp_codes = {sp["id"]: sp["code"] for sp in data["spaceports"]}
        data |= {"vehicle_types": [{"id": v.id, "name": v.name, "class": v.vehicle_class.title}
                                   for v in self.s.reference.list_vehicle_types(session)],
                "pads": [{"id": p.id, "code": p.code, "name": p.name, "spaceport": sp_codes.get(p.spaceport_id, ""),
                          "spaceport_id": p.spaceport_id,
                          "preparations": f"{u.active_preparations}/{p.max_concurrent}",
                          "cycles": f"{u.cycles_since_maintenance}/{p.cycle_limit}",
                          "hours": f"{u.hours_since_maintenance:g}/{p.hours_limit:g}",
                          "state": p.state(u).title, "state_key": p.state(u).value,
                          "in_maintenance": p.state(u) is PadState.IN_MAINTENANCE}
                         for p, u in self.s.reference.list_pads(session)],
                "maintenance_kinds": [{"value": k.value, "title": k.title} for k in MaintenanceKind],
                "vehicle_classes": [{"value": c.value, "title": c.title} for c in VehicleClass],
                "roles": [{"value": r.value, "title": r.title} for r in Role],
                "fuel_components": [{"value": c.value, "title": c.title} for c in FuelComponent]}
        if session.can(Permission.MANAGE_CHANNELS) or session.can(Permission.RECORD_INCIDENT):
            data["channels"] = [{"id": c.id, "no": c.channel_no, "system": c.vehicle_system,
                                 "parameter": c.parameter} for c in self.s.reference.list_channels(session)]
        lim = self.s.weather.limits
        data["weather_limits"] = (f"ветер у земли ≤ {lim.max_ground_wind:g} м/с, на высотах ≤ {lim.max_altitude_wind:g} м/с, "
                                  f"t° {lim.min_temperature:g}…{lim.max_temperature:g} °C, "
                                  f"облачность ≥ {lim.min_cloud_base:g} м, без грозы")
        return _json(200, data)

    # ------------------------------------------------------------------ пуски
    def launches(self, session, query, **_):
        vts, pads = self._names(session)
        sps = self._pad_spaceports(session)
        status = query.get("status")
        launches = self.s.launches.list_launches(session, (status,) if status else None,
                                                 _opt_id(query.get("spaceport")),
                                                 _opt_id(query.get("vehicle_type"), "Тип РН"))
        return _json(200, [self._launch(l, vts, pads, sps) for l in launches])

    def card(self, session, launch_id, **_):
        c = self.s.launches.launch_card(session, int(launch_id))
        vts, pads = self._names(session)
        l = c.launch
        next_stage = None
        if l.status not in FINAL_STATUSES and l.status is not LaunchStatus.READY:
            next_stage = self.s.launches.next_stage_title(l)
        return _json(200, {
            **self._launch(l, vts, pads, {c.pad.id: c.spaceport.code}),
            "spaceport_name": c.spaceport.name,
            "vehicle_class": c.vehicle.vehicle_class.title, "pad_name": c.pad.name,
            "cancel_reason": l.cancel_reason, "next_stage": next_stage,
            "fuel": [{"component": k.title, "mass": str(v)} for k, v in c.fuel_loaded.items()],
            "fuel_warnings": list(c.fuel_warnings), "forecast": self._forecast(c.forecast),
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
        warnings = self.s.launches.fuel_warnings(session, int(launch_id))
        text = f"Пуск перенесён: {rec.summary()}"
        if warnings:
            text += ". Внимание: " + "; ".join(warnings) + ". К пуску такую РН не допустят"
        return _json(200, {"message": text, "warnings": warnings})

    def cancel(self, session, launch_id, data, **_):
        self.s.launches.cancel(session, int(launch_id), data.get("reason", ""))
        return _json(200, {"message": "Пуск отменён"})

    def result(self, session, launch_id, data, **_):
        status = self.s.launches.record_result(session, int(launch_id), bool(data.get("success")),
                                               data.get("pad_hours"))
        return _json(200, {"message": f"Статус «{status.title}», цикл площадки учтён"})

    # ------------------------------------------------------------------ топливо, метео, телеметрия
    def fuel(self, session, query, **_):
        codes = {sp.id: sp.code for sp in self.s.reference.list_spaceports(session)}
        return _json(200, [{"id": b.id, "batch_no": b.batch_no, "component": b.component.title, "grade": b.grade,
                            "spaceport": codes.get(b.spaceport_id, ""), "spaceport_id": b.spaceport_id,
                            "mass": str(b.initial_mass), "remaining": str(rest), "remaining_kg": rest.kg,
                            "expires_on": f"{b.expires_on:%d.%m.%Y}"}
                           for b, rest in self.s.fuel.list_batches(session, _opt_id(query.get("spaceport")))])

    def receive_batch(self, session, data, **_):
        b = self.s.fuel.receive_batch(session, data.get("batch_no", ""), data.get("component", ""),
                                      data.get("grade", ""), data.get("mass_kg"),
                                      _parse_date(data.get("produced_on"), "Дата изготовления"),
                                      _parse_date(data.get("expires_on"), "Срок годности"),
                                      _opt_id(data.get("spaceport_id")))
        return _json(201, {"message": f"Партия {b.batch_no} принята, {b.initial_mass}"})

    def refuel(self, session, launch_id, data, **_):
        rest = self.s.fuel.refuel(session, int(launch_id), int(data["batch_id"]), data.get("mass_kg"))
        return _json(200, {"message": f"Расход списан, остаток партии {rest}"})

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
        codes = {sp.id: sp.code for sp in self.s.reference.list_spaceports(session)}
        return _json(200, [{"id": st.id, "login": st.login, "full_name": st.full_name, "role": st.role.title,
                            "spaceport": codes.get(st.spaceport_id, "центр"),
                            "active": st.is_active} for st in self.s.staff.list_staff(session)])

    def add_staff(self, session, data, **_):
        st = self.s.staff.create_staff(session, data.get("login", ""), data.get("full_name", ""),
                                       data.get("role", ""), data.get("password", ""),
                                       _opt_id(data.get("spaceport_id")))
        return _json(201, {"message": f"Сотрудник {st.login} ({st.role.title}) добавлен"})

    def deactivate_staff(self, session, staff_id, **_):
        self.s.staff.deactivate(session, int(staff_id))
        return _json(200, {"message": "Учётная запись заблокирована"})

    def add_pad(self, session, data, **_):
        p = self.s.reference.add_pad(session, data.get("code", ""), data.get("name", ""), data.get("max_concurrent"),
                                     data.get("cycle_limit"), data.get("hours_limit"), _opt_id(data.get("spaceport_id")))
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

    # ------------------------------------------------------------------ космодромы, журналы, аудит (ЛР4)
    def spaceports(self, session, **_):
        return _json(200, [{"id": sp.id, "code": sp.code, "name": sp.name, "region": sp.region}
                           for sp in self.s.reference.list_spaceports(session)])

    def add_spaceport(self, session, data, **_):
        sp = self.s.reference.add_spaceport(session, data.get("code", ""), data.get("name", ""), data.get("region", ""))
        return _json(201, {"message": f"Космодром {sp.name} ({sp.code}) добавлен"})

    @staticmethod
    def _journal_row(row) -> dict:
        r = row.record
        return {"launch_id": row.launch_id, "vehicle": row.vehicle, "spaceport": row.spaceport,
                "at": f"{r.recorded_at:{DT}}", "category": r.category, "text": r.summary(),
                "reason": getattr(getattr(r, "reason", None), "title", "")}

    def journal_incidents(self, session, query, **_):
        rows = self.s.journal.incidents(session, *_period(query), _opt_id(query.get("spaceport")))
        return _json(200, [self._journal_row(r) for r in rows])

    def journal_postponements(self, session, query, **_):
        rows = self.s.journal.postponements(session, *_period(query), query.get("reason") or None,
                                            _opt_id(query.get("spaceport")))
        return _json(200, [self._journal_row(r) for r in rows])

    def audit(self, session, query, **_):
        records = self.s.audit.list_records(session, *_period(query), query.get("login", ""))
        return _json(200, [{"at": f"{a.recorded_at:{DT}}", "login": a.login, "role": a.role.title, "action": a.action,
                            "object": a.object_type + (f" {a.object_ref}" if a.object_ref else ""),
                            "outcome": "выполнено" if a.outcome == "ok" else "отказ", "details": a.details}
                           for a in records])

    # ------------------------------------------------------------------ прогнозы (ЛР4)
    def forecasts(self, session, query, **_):
        vts, pads = self._names(session)
        sps = self._pad_spaceports(session)
        items = self.s.forecasts.upcoming(session, _opt_id(query.get("spaceport")))
        return _json(200, [{**self._launch(l, vts, pads, sps), "forecast": self._forecast(rec)} for l, rec in items])

    def request_forecast(self, session, launch_id, **_):
        rec = self.s.forecasts.request(session, int(launch_id))
        return _json(200, {"message": f"Прогноз: {rec.summary()}", "forecast": self._forecast(rec)})

    def manual_forecast(self, session, launch_id, data, **_):
        rec = self.s.forecasts.enter_manual(session, int(launch_id), data.get("ground_wind"), data.get("altitude_wind"),
                                            data.get("temperature"), data.get("cloud_base"),
                                            bool(data.get("thunderstorm")))
        return _json(200, {"message": f"Ручной прогноз сохранен: {rec.status.title}", "forecast": self._forecast(rec)})

    # ------------------------------------------------------------------ отчёты
    def report_list(self, session, **_):
        return _json(200, [{"key": k, "title": t} for k, t in self.s.reports.available(session)])

    def _build(self, session, key, query):
        period = Period(_parse_date(query.get("from"), "Начало периода"), _parse_date(query.get("to"), "Конец периода"))
        return self.s.reports.build(session, key, period, _opt_id(query.get("spaceport")), query.get("reason") or None)

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


class _Server(HTTPServer):
    """HTTP-сервер с таймером: раз в `forecast_interval` секунд запрашивает прогнозы (Т4.1).

    `service_actions` вызывается из цикла `serve_forever` в том же потоке, поэтому
    одно соединение SQLite не используется из двух потоков одновременно.
    """

    def __init__(self, address, handler, services: Services, forecast_interval: float = 60.0):
        super().__init__(address, handler)
        self._services = services
        self._interval = forecast_interval
        self._last = 0.0

    def service_actions(self):
        if self._interval and time.monotonic() - self._last >= self._interval:
            self._last = time.monotonic()
            try:
                for rec in self._services.forecasts.run_auto():
                    print(f"Прогноз для пуска №{rec.launch_id}: {rec.summary()}")
            except Exception as e:          # таймер не должен останавливать сервер
                print(f"Ошибка автоматического запроса прогноза: {e}")


def run_server(services: Services, host: str = "127.0.0.1", port: int = 8000, open_browser: bool = False,
               forecast_interval: float = 60.0) -> None:
    """Однопоточный сервер: соединение SQLite используется в одном потоке."""
    _Handler.api = WebApi(services)
    server = _Server((host, port), _Handler, services, forecast_interval)
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
