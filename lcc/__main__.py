"""Точка входа: ``python -m lcc [--db PATH] [--demo | --init] [--port N] [--no-browser]``."""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

from .controller import run_server
from .model import Database, Repositories, Services
from .model.enums import Role
from .model.errors import DomainError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lcc", description="АИС «Центр управления космическими пусками»")
    parser.add_argument("--db", default="demo.db", help="файл базы данных SQLite (по умолчанию demo.db)")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--demo", action="store_true", help="только создать демо-базу и выйти")
    group.add_argument("--init", action="store_true", help="создать пустую рабочую базу с учётной записью руководителя")
    parser.add_argument("--host", default="127.0.0.1", help="адрес веб-сервера (по умолчанию только этот компьютер)")
    parser.add_argument("--port", type=int, default=8000, help="порт веб-сервера")
    parser.add_argument("--no-browser", action="store_true", help="не открывать браузер автоматически")
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

    db = Database(args.db)
    db.init_schema()
    services = Services(Repositories(db))

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
        run_server(services, args.host, args.port, open_browser=not args.no_browser)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
