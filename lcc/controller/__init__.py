"""Слой Controller: JSON API — принимает запросы веб-интерфейса, вызывает Model, возвращает результат."""
from .web import WebApi, run_server

__all__ = ["WebApi", "run_server"]
