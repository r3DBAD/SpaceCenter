"""Учебная заглушка внешней метеослужбы космодрома (только для демонстрации, не часть системы).

Отвечает на ``GET /forecast?site=VOS&time=2026-10-22T06:00`` в формате, описанном
в lcc/model/meteo_client.py. Для Плесецка (PLS) отдает штормовой ветер, чтобы было
видно предупреждение о переносе. Режимы для проверки отказов: ``--delay 40`` (ответ
позже таймаута 30 с) и ``--down`` (HTTP 503).

Отдельный запуск: ``python -m lcc.meteo_stub --port 8001``.
"""
from __future__ import annotations

import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

#: Профили погоды по кодам космодромов: (ветер у земли, ветер на 10 км, температура, гроза)
PROFILES = {"VOS": (6.0, 22.0, 3.0, False), "PLS": (17.5, 33.0, -6.0, False)}
DEFAULT = (5.0, 18.0, 8.0, False)


class _Handler(BaseHTTPRequestHandler):
    delay = 0.0
    down = False

    def do_GET(self):
        url = urlparse(self.path)
        if url.path != "/forecast":
            return self._send(404, {"error": "not found"})
        if self.down:
            return self._send(503, {"error": "service unavailable"})
        time.sleep(self.delay)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        ground, upper, temp, storm = PROFILES.get(q.get("site", ""), DEFAULT)
        self._send(200, {"valid_at": q.get("time", ""), "thunderstorm": storm, "cloud_base_m": 900,
                         "levels": [{"altitude_m": 0, "wind_ms": ground, "temperature_c": temp},
                                    {"altitude_m": 5000, "wind_ms": round(upper * 0.6, 1), "temperature_c": temp - 32},
                                    {"altitude_m": 10000, "wind_ms": upper, "temperature_c": temp - 60}]})

    def _send(self, status, data):
        body = json.dumps(data).encode()
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):     # клиент ушел по таймауту
            pass

    def log_message(self, fmt, *args):
        pass


def start_stub(port: int = 8001, delay: float = 0.0, down: bool = False) -> ThreadingHTTPServer:
    """Запустить заглушку в фоновом потоке. Возвращает сервер (для остановки — shutdown())."""
    handler = type("StubHandler", (_Handler,), {"delay": delay, "down": down})
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="Заглушка внешней метеослужбы")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--delay", type=float, default=0.0, help="задержка ответа, с")
    parser.add_argument("--down", action="store_true", help="отвечать 503")
    args = parser.parse_args()
    start_stub(args.port, args.delay, args.down)
    print(f"Метеослужба (заглушка): http://127.0.0.1:{args.port}/forecast  (Ctrl+C — остановить)")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
