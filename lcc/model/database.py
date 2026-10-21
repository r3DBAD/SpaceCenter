"""Подключение к SQLite и управление транзакциями."""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Callable

from .migrations import SCHEMA_VERSION, is_lab3_database, migrate_v0_to_v2

SCHEMA = Path(__file__).with_name("schema.sql")

DT_FORMAT = "%Y-%m-%dT%H:%M"


def dt_to_db(value: datetime | None) -> str | None:
    return None if value is None else value.strftime(DT_FORMAT)


def dt_from_db(value: str | None) -> datetime | None:
    """ЛР5, оптимизация №2: fromisoformat написан на C, замер timeit: 0,10 мкс против 3,41 мкс
    у strptime (в 35 раз), который разбирает строку формата на Python. DT_FORMAT — это ISO 8601."""
    return None if value is None else datetime.fromisoformat(value)


def date_to_db(value: date) -> str:
    return value.isoformat()


def date_from_db(value: str) -> date:
    return date.fromisoformat(value)


class Database:
    """Обёртка над соединением SQLite.

    * внешние ключи включены (``PRAGMA foreign_keys``);
    * все запросы репозиториев параметризованы (``?``), конкатенации SQL нет;
    * :meth:`transaction` — атомарная группа операций (откат при исключении);
    * :meth:`after_transaction` — действие, которое выполнится после фиксации или
      отката (нужно для записи отказов в аудит, ЛР4).
    """

    def __init__(self, path: str | Path = ":memory:"):
        is_new_file = str(path) != ":memory:" and not Path(path).exists()
        self._conn = sqlite3.connect(str(path))
        if is_new_file and os.name == "posix":
            os.chmod(path, 0o600)          # БД содержит ФИО и хеши паролей — только владельцу
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._in_tx = False
        self._after_tx: list[Callable[[], None]] = []

    def init_schema(self) -> None:
        """Создать схему. База ЛР3 сначала переводится на текущую версию."""
        if is_lab3_database(self._conn):
            migrate_v0_to_v2(self._conn, SCHEMA)
        self._conn.executescript(SCHEMA.read_text(encoding="utf-8"))
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    @property
    def schema_version(self) -> int:
        return self.scalar("PRAGMA user_version")

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        cur = self._conn.execute(sql, params)
        if not self._in_tx:
            self._conn.commit()
        return cur

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        return self._conn.execute(sql, params).fetchall()

    def iterate(self, sql: str, params: tuple = ()):
        """Строки по одной, без fetchall: для больших выборок, которые обрабатываются потоком (ЛР5)."""
        yield from self._conn.execute(sql, params)

    def query_one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        return self._conn.execute(sql, params).fetchone()

    def scalar(self, sql: str, params: tuple = ()):
        row = self._conn.execute(sql, params).fetchone()
        return None if row is None else row[0]

    @contextmanager
    def transaction(self):
        if self._in_tx:            # вложенная транзакция — часть внешней
            yield
            return
        self._in_tx = True
        try:
            yield
            self._conn.commit()
        except BaseException:
            self._conn.rollback()
            raise
        finally:
            self._in_tx = False
            pending, self._after_tx = self._after_tx, []
            for action in pending:
                action()

    def after_transaction(self, action: Callable[[], None]) -> None:
        """Выполнить действие вне текущей транзакции: сразу или после ее завершения."""
        if self._in_tx:
            self._after_tx.append(action)
        else:
            action()

    def close(self) -> None:
        self._conn.close()
