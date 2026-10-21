"""Точка входа: ``python -m lcc [--db PATH] [--demo | --init] [--port N] [--no-browser] [--meteo-stub | --meteo-url URL]``."""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

from .controller import run_server
from .model import Database, Repositories, Services
from .model.enums import Role
from .model.errors import DomainError
from .model.meteo_client import HttpMeteoProvider


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lcc", description="АИС «Центр управления космическими пусками»")
    parser.add_argument("--db", default="demo.db", help="файл базы данных SQLite (по умолчанию demo.db)")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--demo", action="store_true", help="только создать демо-базу и выйти")
    group.add_argument("--init", action="store_true", help="создать пустую рабочую базу с учётной записью руководителя")
    parser.add_argument("--host", default="127.0.0.1", help="адрес веб-сервера (по умолчанию только этот компьютер)")
    parser.add_argument("--port", type=int, default=8000, help="порт веб-сервера")
    parser.add_argument("--no-browser", action="store_true", help="не открывать браузер автоматически")
    meteo = parser.add_mutually_exclusive_group()
    meteo.add_argument("--meteo-url", help="адрес внешней метеослужбы, например http://meteo.local:8080")
    meteo.add_argument("--meteo-stub", action="store_true", help="запустить учебную заглушку метеослужбы на порту 8001")
    parser.add_argument("--meteo-timeout", type=float, default=HttpMeteoProvider.TIMEOUT,
                        help="таймаут ответа метеослужбы, с (по умолчанию 30)")
    parser.add_argument("--forecast-interval", type=float, default=60.0,
                        help="как часто проверять пуски, которым нужен прогноз, с (0 — не проверять)")
    args = parser.parse_args(argv)

    from .seed import DEMO_PASSWORD, DEMO_USERS, seed_demo
    if args.demo or (not args.init and not Path(args.db).exists()):
        if Path(args.db).exists():
            print(f"Файл {args.db} уже существует — укажите другой --db или удалите файл")
            return 1
        seed_demo(args.db)
        print(f"Создана демо-база {args.db}. Логины: {', '.join(u[0] for u in DEMO_USERS)}; пароль {DEMO_PASSWORD}")
        if args.demo:
            return 0

    meteo_url = args.meteo_url
    if args.meteo_stub:
        from .meteo_stub import start_stub
        start_stub(8001)
        meteo_url = "http://127.0.0.1:8001"
        print(f"Запущена заглушка метеослужбы: {meteo_url}")
    provider = HttpMeteoProvider(meteo_url, args.meteo_timeout) if meteo_url else None
    if provider is None:
        print("Внешняя метеослужба не задана: прогноз за 24 ч вводит метеоролог вручную")

    db = Database(args.db)
    db.init_schema()          # база ЛР3 переводится на новую схему автоматически
    services = Services(Repositories(db), meteo=provider)

    if args.init:
        print("Первичная настройка: учётная запись руководителя центра")
        try:
            services.staff.create_staff(None, input("Логин: "), input("ФИО: "), Role.HEAD,
                                        getpass.getpass("Пароль (≥ 8 символов, буквы и цифры): "))
        except DomainError as e:
            print(f"Ошибка: {e}")
            return 1
        print("Руководитель создан.")

    try:
        run_server(services, args.host, args.port, open_browser=not args.no_browser,
                   forecast_interval=args.forecast_interval)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
