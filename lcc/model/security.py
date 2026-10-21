"""Безопасность: хеширование паролей и сессия с проверкой прав."""
from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Callable

from .enums import ROLE_PERMISSIONS, Permission
from .errors import AccessDeniedError, ValidationError


class PasswordHasher:
    """PBKDF2-HMAC-SHA256 с индивидуальной солью. Пароли в открытом виде не хранятся."""

    ITERATIONS = 200_000
    MIN_LENGTH = 8

    @classmethod
    def validate_strength(cls, password: str) -> None:
        if not isinstance(password, str) or len(password) < cls.MIN_LENGTH:
            raise ValidationError(f"Пароль должен содержать не менее {cls.MIN_LENGTH} символов")
        if password.isdigit() or password.isalpha():
            raise ValidationError("Пароль должен содержать и буквы, и цифры")

    @classmethod
    def hash(cls, password: str, salt: str | None = None) -> tuple[str, str]:
        """Вернуть (hash_hex, salt_hex)."""
        salt = salt or secrets.token_hex(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), cls.ITERATIONS)
        return digest.hex(), salt

    @classmethod
    def verify(cls, password: str, password_hash: str, salt: str) -> bool:
        candidate, _ = cls.hash(password, salt)
        return hmac.compare_digest(candidate, password_hash)   # сравнение за постоянное время


class Session:
    """Аутентифицированный пользователь. Все сервисы Model проверяют права через :meth:`require`.

    ЛР4: у сессии есть область видимости. Сотрудник космодрома видит только его данные
    (:meth:`ensure_spaceport`), роли уровня центра видят все. Отказы передаются в
    `on_denied`, чтобы аудит записал попытку (Т3.3, П12).
    """

    def __init__(self, staff, on_denied: Callable[["Session", str, str, str], None] | None = None):
        self._staff = staff
        self._on_denied = on_denied

    @property
    def staff(self):
        return self._staff

    @property
    def staff_id(self) -> int:
        return self._staff.id

    @property
    def spaceport_id(self) -> int | None:
        """Космодром сотрудника; None — роль уровня центра."""
        return self._staff.spaceport_id

    @property
    def is_global(self) -> bool:
        return self._staff.spaceport_id is None

    def scope(self, requested: int | None = None) -> int | None:
        """Фильтр по космодрому для выборок: свой космодром или выбранный руководителем центра."""
        if self.is_global:
            return requested
        if requested is not None:
            self.ensure_spaceport(requested)
        return self._staff.spaceport_id

    def can(self, permission: Permission) -> bool:
        return permission in ROLE_PERMISSIONS[self._staff.role]

    def require(self, *permissions: Permission) -> None:
        """Требуется хотя бы одно из перечисленных прав."""
        if not any(self.can(p) for p in permissions):
            self._deny(f"Недостаточно прав: роль «{self._staff.role.title}» не может выполнить эту операцию",
                       "право", " или ".join(p.value for p in permissions))

    def ensure_spaceport(self, spaceport_id: int) -> None:
        """Объект должен относиться к космодрому сотрудника (роли центра проходят всегда)."""
        if not self.is_global and spaceport_id != self._staff.spaceport_id:
            self._deny("Нет доступа к данным другого космодрома", "космодром", f"№{spaceport_id}")

    def _deny(self, message: str, object_type: str, object_ref: str) -> None:
        if self._on_denied is not None:
            self._on_denied(self, message, object_type, object_ref)
        raise AccessDeniedError(message)
