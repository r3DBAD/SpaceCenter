"""Масса топлива с точностью 0,1 кг (требование Т5.1, ЛР4).

В ЛР3 масса хранилась в тоннах как REAL. Сумма дробных чисел накапливает ошибку,
а остаток округлялся до 0,001 т, то есть до 1 кг. Теперь масса хранится целым
числом гектограммов (1 гг = 100 г = 0,1 кг), и сложение всегда точное.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .errors import ValidationError


@dataclass(frozen=True, order=True)
class Mass:
    """Объект-значение: масса в гектограммах. Снаружи работаем в килограммах."""
    hg: int

    PER_KG = 10

    def __post_init__(self):
        if isinstance(self.hg, bool) or not isinstance(self.hg, int):
            raise ValidationError("Масса хранится целым числом гектограммов")

    @classmethod
    def from_kg(cls, value, field: str = "Масса, кг") -> "Mass":
        """Разобрать число или строку в кг. Больше одного знака после запятой нельзя."""
        if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
            raise ValidationError(f"Поле «{field}» должно быть числом")
        try:
            kg = Decimal(str(value).strip().replace(",", "."))
        except InvalidOperation:
            raise ValidationError(f"Поле «{field}» должно быть числом") from None
        if not kg.is_finite():
            raise ValidationError(f"Поле «{field}» должно быть конечным числом")
        hg = kg * cls.PER_KG
        if hg != hg.to_integral_value():
            raise ValidationError(f"Поле «{field}»: точность учета топлива 0,1 кг")
        return cls(int(hg))

    @classmethod
    def zero(cls) -> "Mass":
        return cls(0)

    @property
    def kg(self) -> float:
        return self.hg / self.PER_KG

    def __add__(self, other: "Mass") -> "Mass":
        return Mass(self.hg + other.hg)

    def __sub__(self, other: "Mass") -> "Mass":
        return Mass(self.hg - other.hg)

    def __str__(self) -> str:
        """1234567 гг → «123 456,7 кг»."""
        return f"{self.kg:,.1f}".replace(",", " ").replace(".", ",") + " кг"
