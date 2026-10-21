"""Журнал аудита (ЛР4, Т3.3).

Запись делается в Model, а не в Controller: действие, вызванное в обход веб-интерфейса,
тоже попадает в журнал (docs/07_lab4_analysis.md, 7.3.3). Обычные действия пишутся
в той же транзакции, что и само действие. Отказ в доступе пишется после отката,
иначе откат удалил бы и запись об отказе.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .enums import Permission, Role
from .security import Session
from .validation import optional_text, require_text

#: Чьи действия записываются. Требование говорит только об инспекторе безопасности.
AUDITED_ROLES = frozenset({Role.SAFETY_INSPECTOR})


@dataclass(frozen=True)
class AuditRecord:
    recorded_at: datetime
    staff_id: int
    login: str
    role: Role
    action: str
    object_type: str
    object_ref: str
    outcome: str        # ok | denied
    details: str


class AuditLogger:
    def __init__(self, repos, clock):
        self._repos = repos
        self._clock = clock

    def _now(self) -> datetime:
        return self._clock().replace(second=0, microsecond=0)

    @staticmethod
    def is_audited(session: Session) -> bool:
        return session.staff.role in AUDITED_ROLES

    def record(self, session: Session, action: str, object_type: str, object_ref: str = "",
               details: str = "") -> None:
        """Записать успешное действие (если роль сотрудника аудируется)."""
        if self.is_audited(session):
            self._write(session, action, object_type, object_ref, "ok", details)

    def denied(self, session: Session, message: str, object_type: str, object_ref: str) -> None:
        """Обработчик отказа для :class:`Session`: запись делается вне текущей транзакции."""
        if self.is_audited(session):
            self._repos.db.after_transaction(
                lambda: self._write(session, "Отказ в доступе", object_type, object_ref, "denied", message))

    def _write(self, session, action, object_type, object_ref, outcome, details) -> None:
        st = session.staff
        self._repos.audit.add(AuditRecord(self._now(), st.id, st.login, st.role, require_text(action, "Действие", 120),
                                          require_text(object_type, "Объект", 60),
                                          optional_text(object_ref, "Ссылка", 120), outcome,
                                          optional_text(details, "Подробности", 500)))

    def list_records(self, session: Session, date_from: datetime, date_to: datetime,
                     login: str = "") -> list[AuditRecord]:
        """Просмотр журнала аудита — только руководитель центра (П12)."""
        session.require(Permission.VIEW_AUDIT)
        return self._repos.audit.list(date_from, date_to, (login or "").strip().lower() or None)
