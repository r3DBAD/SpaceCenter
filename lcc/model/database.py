"""Подключение к SQLite и управление транзакциями."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

SCHEMA = Path(__file__).with_name("schema.sql")

DT_FORMAT = "%Y-%m-%dT%H:%M"


def dt_to_db(value: datetime | None) -> str | None:
    return None if value is None else value.strftime(DT_FORMAT)


def dt_from_db(value: str | None) -> datetime | None:
    return None if value is None else datetime.strptime(value, DT_FORMAT)


def date_to_db(value: date) -> str:
    return value.isoformat()


def date_from_db(value: str) -> date:
    return date.fromisoformat(value)


class Database:
    """Обёртка над соединением SQLite.

    * внешние ключи включены (``PRAGMA foreign_keys``);
    * все запросы репозиториев параметризованы (``?``), конкатенации SQL нет;
    * :meth:`transaction` — атомарная группа операций (откат при исключении).
    """

    def __init__(self, path: str | Path = ":memory:"):
        self._conn = sqlite3.connect(str(path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._in_tx = False

    def init_schema(self) -> None:
        self._conn.executescript(SCHEMA.read_text(encoding="utf-8"))

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        cur = self._conn.execute(sql, params)
        if not self._in_tx:
            self._conn.commit()
        return cur

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        return self._conn.execute(sql, params).fetchall()

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

    def close(self) -> None:
        self._conn.close()
