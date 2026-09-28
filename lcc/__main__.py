"""Точка входа: ``python -m lcc [--db PATH] [--init | --demo]``."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .controller import AppController
from .model import Database, Repositories, Services
from .model.enums import Role
from .model.errors import DomainError
from .view import ConsoleView, CsvExporter


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lcc", description="АИС «Центр управления космическими пусками»")
    parser.add_argument("--db", default="lcc.db", help="файл базы данных SQLite (по умолчанию lcc.db)")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--init", action="store_true", help="создать пустую базу и учётную запись руководителя")
    group.add_argument("--demo", action="store_true", help="создать демо-базу с тестовыми данными")
    args = parser.parse_args(argv)
    view = ConsoleView()

    if args.demo:
        if Path(args.db).exists():
            view.show_error(f"Файл {args.db} уже существует — укажите другой --db или удалите файл")
            return 1
        from .seed import DEMO_PASSWORD, DEMO_USERS, seed_demo
        seed_demo(args.db)
        view.show_message(f"Демо-база создана: {args.db}")
        view.show_table("Демо-пользователи", ["Логин", "Пароль", "Роль"],
                        [[u[0], DEMO_PASSWORD, u[2].title] for u in DEMO_USERS])
        return 0

    db = Database(args.db)
    db.init_schema()
    services = Services(Repositories(db))

    if args.init:
        view.show_title("Первичная настройка: учётная запись руководителя центра")
        try:
            services.staff.create_staff(None, view.ask_text("Логин"), view.ask_text("ФИО"), Role.HEAD,
                                        view.ask_password("Пароль (≥ 8 символов, буквы и цифры)"))
        except DomainError as e:
            view.show_error(str(e))
            return 1
        view.show_message("Руководитель создан. Запустите программу без --init и войдите")
        return 0

    try:
        AppController(view, services, CsvExporter()).run()
    except (KeyboardInterrupt, EOFError):
        print("\nВыход")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
