"""Слой Model: сущности, бизнес-правила, доступ к данным, отчёты.

Не зависит от :mod:`lcc.view` и :mod:`lcc.controller`.
"""
from .database import Database
from .repositories import Repositories
from .services import Services


def open_model(path: str = ":memory:", clock=None) -> Services:
    """Создать БД (при необходимости — схему) и вернуть фасад сервисов."""
    db = Database(path)
    db.init_schema()
    repos = Repositories(db)
    return Services(repos, clock) if clock else Services(repos)


__all__ = ["Database", "Repositories", "Services", "open_model"]
