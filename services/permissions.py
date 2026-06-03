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

def is_coach(user: object) -> bool:
    return isinstance(user, Coach)


def is_admin(user: object) -> bool:
    return isinstance(user, Admin)


def is_athlete(user: object) -> bool:
    return isinstance(user, Athlete)


def is_staff(user: object) -> bool:
    return is_coach(user) or is_admin(user)


def can_manage_global_freeze(user: object) -> bool:
    """Массовая заморозка клуба — только администратор."""
    return is_admin(user)


GLOBAL_FREEZE_ADMIN_ONLY_MESSAGE = (
    "❌ Массовая заморозка клуба доступна только администратору.\n\n"
    "Для своей группы: «🚫 Отмена тренировки» или заморозка в карточке спортсмена."
)

COACH_ONLY_MESSAGE = (
    "❌ Эта функция доступна только тренерам. "
    "Администратор использует своё меню (кнопки клуба)."
)

COACH_ABSENCE_DENIED_MESSAGE = (
    "❌ Отмену тренировок настраивают тренер (своя группа) или администратор клуба."
)


def can_access_coach_menu(user: object) -> bool:
    """Главное меню тренера и /menu для операционки."""
    return is_coach(user)


def can_use_coach_operational_tools(user: object) -> bool:
    """Добавление спортсменов, посещения, календарь, статистика."""
    return is_coach(user)


def can_manage_coach_absence_flow(user: object) -> bool:
    """Тренер — своё отсутствие; админ — выбор тренера."""
    return is_coach(user) or is_admin(user)


def can_access_subscription_for_staff(
    user: object, subscription: object
) -> bool:
    """Карточка/календарь абонемента: тренер — свои спортсмены, админ — любые."""
    if subscription is None:
        return False
    athlete = getattr(subscription, "athlete", None)
    if athlete is None:
        return is_staff(user)
    return can_edit_athlete(user, athlete)


def role_label_ru(role: str) -> str:
    return ROLE_LABEL_RU.get(role, role)


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
