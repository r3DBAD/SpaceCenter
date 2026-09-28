"""Экспорт отчёта в CSV (представление отчёта в виде файла)."""
from __future__ import annotations

import csv
import io
import re
from datetime import datetime
from pathlib import Path

_UNSAFE = re.compile(r"[^0-9A-Za-zА-Яа-яЁё_-]+")
_FORMULA_PREFIX = ("=", "+", "-", "@", "\t", "\r")


class CsvExporter:
    """Сохраняет :class:`ReportTable` в CSV (разделитель «;», UTF-8 с BOM — корректно открывается в Excel)."""

    def __init__(self, out_dir: str | Path = "reports_out"):
        self._dir = Path(out_dir)

    @staticmethod
    def _safe_cell(value) -> str:
        """Защита от CSV-инъекции: ячейка, начинающаяся с формулы, экранируется апострофом."""
        text = "" if value is None else str(value)
        if text.startswith(_FORMULA_PREFIX) and not _is_number(text):
            return "'" + text
        return text

    @classmethod
    def file_name(cls, report) -> str:
        name = _UNSAFE.sub("_", report.title).strip("_")[:60]      # имя файла без пути от пользователя
        return f"{name}_{datetime.now():%Y%m%d_%H%M%S}.csv"

    @classmethod
    def to_text(cls, report) -> str:
        """CSV-представление отчёта строкой (для веб-интерфейса — без записи на диск)."""
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
        w.writerow([report.title])
        w.writerow([f"Период: {report.period}"])
        w.writerow([cls._safe_cell(c) for c in report.columns])
        for row in report.rows:
            w.writerow([cls._safe_cell(v) for v in row])
        for note in report.notes:
            w.writerow([cls._safe_cell(note)])
        return buf.getvalue()

    def export(self, report) -> Path:
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._dir / self.file_name(report)
        path.write_text(self.to_text(report), encoding="utf-8-sig", newline="")
        return path


def _is_number(text: str) -> bool:
    try:
        float(text.replace(",", "."))
        return True
    except ValueError:
        return False
