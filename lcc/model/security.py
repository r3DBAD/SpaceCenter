"""Безопасность: хеширование паролей и сессия с проверкой прав."""
from __future__ import annotations

import hashlib
import hmac
import secrets

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
    """Аутентифицированный пользователь. Все сервисы Model проверяют права через :meth:`require`."""

    def __init__(self, staff):
        self._staff = staff

    @property
    def staff(self):
        return self._staff

    @property
    def staff_id(self) -> int:
        return self._staff.id

    def can(self, permission: Permission) -> bool:
        return permission in ROLE_PERMISSIONS[self._staff.role]

    def require(self, *permissions: Permission) -> None:
        """Требуется хотя бы одно из перечисленных прав."""
        if not any(self.can(p) for p in permissions):
            raise AccessDeniedError(
                f"Недостаточно прав: роль «{self._staff.role.title}» не может выполнить эту операцию")
