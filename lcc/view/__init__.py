"""Слой View: веб-интерфейс на Vue (каталог ``web/``) и экспорт отчётов в CSV.

Не импортирует :mod:`lcc.model`: получает данные только через JSON API контроллера.
"""
from .csv_export import CsvExporter

__all__ = ["CsvExporter"]
