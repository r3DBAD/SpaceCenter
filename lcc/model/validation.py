"""Функции проверки входных данных уровня Model.

Проверки выполняются в конструкторах и сеттерах сущностей, поэтому обойти их,
вызвав Model в обход View, невозможно.
"""
from __future__ import annotations

import math
from datetime import date, datetime

from .errors import ValidationError


def require_text(value, field: str, max_len: int = 200) -> str:
    """Непустая строка без управляющих символов, обрезанная по краям."""
    if not isinstance(value, str):
        raise ValidationError(f"Поле «{field}» должно быть строкой")
    value = value.strip()
    if not value:
        raise ValidationError(f"Поле «{field}» обязательно для заполнения")
    if len(value) > max_len:
        raise ValidationError(f"Поле «{field}» длиннее {max_len} символов")
    if any(ord(ch) < 32 for ch in value):
        raise ValidationError(f"Поле «{field}» содержит управляющие символы")
    return value


def optional_text(value, field: str, max_len: int = 500) -> str:
    """Необязательная строка: None и пустая строка превращаются в ''."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return ""
    return require_text(value, field, max_len)


def require_number(value, field: str, *, min_value: float | None = None,
                   max_value: float | None = None, strict_min: bool = False) -> float:
    """Конечное число в диапазоне [min_value; max_value] (или (min_value; …] при strict_min)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"Поле «{field}» должно быть числом")
    if math.isnan(value) or math.isinf(value):
        raise ValidationError(f"Поле «{field}» должно быть конечным числом")
    if min_value is not None:
        if strict_min and value <= min_value:
            raise ValidationError(f"Поле «{field}» должно быть больше {min_value:g}")
        if not strict_min and value < min_value:
            raise ValidationError(f"Поле «{field}» не может быть меньше {min_value:g}")
    if max_value is not None and value > max_value:
        raise ValidationError(f"Поле «{field}» не может быть больше {max_value:g}")
    return value


def require_int(value, field: str, *, min_value: int | None = None, max_value: int | None = None) -> int:
    """Целое число в диапазоне."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"Поле «{field}» должно быть целым числом")
    return int(require_number(value, field, min_value=min_value, max_value=max_value))


def require_datetime(value, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise ValidationError(f"Поле «{field}» должно быть датой и временем")
    return value.replace(second=0, microsecond=0, tzinfo=None)


def require_date(value, field: str) -> date:
    if isinstance(value, datetime) or not isinstance(value, date):
        raise ValidationError(f"Поле «{field}» должно быть датой")
    return value


def require_enum(value, enum_cls, field: str):
    """Значение перечисления; допускается передать само значение (строку)."""
    if isinstance(value, enum_cls):
        return value
    try:
        return enum_cls(value)
    except ValueError:
        allowed = ", ".join(e.value for e in enum_cls)
        raise ValidationError(f"Поле «{field}»: недопустимое значение «{value}» (допустимо: {allowed})") from None
