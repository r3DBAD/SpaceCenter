"""Исключения предметной области.

Все ошибки Model наследуются от :class:`DomainError`, поэтому Controller может
перехватить их одним обработчиком и передать понятное сообщение во View.
"""


class DomainError(Exception):
    """Базовая ошибка предметной области."""


class ValidationError(DomainError):
    """Недопустимое значение поля (пустая строка, отрицательное число и т. п.)."""


class BusinessRuleError(DomainError):
    """Нарушение бизнес-правила (лимит площадки, порядок этапов, просроченное топливо…)."""


class AccessDeniedError(DomainError):
    """У текущего пользователя нет права на операцию."""


class NotFoundError(DomainError):
    """Запрошенный объект не найден."""


class AuthenticationError(DomainError):
    """Неверный логин/пароль или учётная запись заблокирована."""
