"""Роли и проверки доступа: coach, admin, athlete (без dual coach+admin)."""
from __future__ import annotations

import logging
from typing import List, Union

from sqlalchemy.orm import Session

from database.db_utils.role_policy import (
    ACTIVE_ROLES,
    ROLE_ADMIN,
    ROLE_ATHLETE,
    ROLE_COACH,
    STAFF_ROLES,
    assert_can_assign_role,
    roles_for_telegram_id,
    validate_staff_roles_consistency,
)
from database.models import Admin, Athlete, Coach

logger = logging.getLogger(__name__)

StaffUser = Union[Coach, Admin]
AppUser = Union[Coach, Admin, Athlete]

ROLE_LABEL_RU = {
    ROLE_COACH: "тренер",
    ROLE_ADMIN: "администратор",
    ROLE_ATHLETE: "спортсмен",
}

# «Выберите действие в меню …» — родительный падеж
ROLE_MENU_GENITIVE_RU = {
    ROLE_COACH: "тренера",
    ROLE_ADMIN: "администратора",
    ROLE_ATHLETE: "спортсмена",
}


def is_coach(user: object) -> bool:
    return isinstance(user, Coach)


def is_admin(user: object) -> bool:
    return isinstance(user, Admin)


def is_athlete(user: object) -> bool:
    return isinstance(user, Athlete)


def is_staff(user: object) -> bool:
    return is_coach(user) or is_admin(user)


def role_label_ru(role: str) -> str:
    return ROLE_LABEL_RU.get(role, role)


def role_menu_genitive_ru(role: str) -> str:
    """Подпись роли для «Выберите действие в меню …»."""
    return ROLE_MENU_GENITIVE_RU.get(role, role)


def can_edit_athlete(user: object, athlete: Athlete) -> bool:
    if is_admin(user):
        return True
    if is_coach(user) and athlete.created_by == user.id:
        return True
    return False


def has_dual_staff_role(session: Session, telegram_id: int) -> bool:
    r = roles_for_telegram_id(session, telegram_id)
    return ROLE_COACH in r and ROLE_ADMIN in r


def log_staff_roles_validation(session: Session) -> None:
    for msg in validate_staff_roles_consistency(session):
        logger.warning("⚠️ Роли: %s", msg)


def list_active_admin_telegram_ids(session: Session) -> List[int]:
    rows = session.query(Admin).filter(
        (Admin.is_active == True) | (Admin.is_active.is_(None))  # noqa: E712
    ).all()
    return [int(a.telegram_id) for a in rows if a.telegram_id is not None]


def daily_audit_recipient_ids(session: Session, config: object) -> List[int]:
    ids = list_active_admin_telegram_ids(session)
    if ids:
        return ids
    fallback = getattr(config, "ADMIN_TELEGRAM_ID", None)
    return [int(fallback)] if fallback is not None else []
