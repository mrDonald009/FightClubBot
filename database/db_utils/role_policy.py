"""Политика ролей в БД (без зависимости от services)."""
from __future__ import annotations

from typing import List

from sqlalchemy.orm import Session

from database.models import Admin, Assistant, Athlete, Coach

ROLE_COACH = "coach"
ROLE_ADMIN = "admin"
ROLE_ATHLETE = "athlete"

ACTIVE_ROLES = (ROLE_COACH, ROLE_ADMIN, ROLE_ATHLETE)
STAFF_ROLES = (ROLE_COACH, ROLE_ADMIN)


def roles_for_telegram_id(session: Session, telegram_id: int) -> List[str]:
    roles: List[str] = []
    if session.query(Coach).filter_by(telegram_id=telegram_id).first():
        roles.append(ROLE_COACH)
    if session.query(Admin).filter_by(telegram_id=telegram_id).first():
        roles.append(ROLE_ADMIN)
    if session.query(Athlete).filter_by(telegram_id=telegram_id).first():
        roles.append(ROLE_ATHLETE)
    return roles


def assert_can_assign_role(session: Session, telegram_id: int, target_role: str) -> None:
    if target_role not in ACTIVE_ROLES:
        raise ValueError(
            "Неподдерживаемая роль: {}. Допустимы: {}".format(
                target_role, ", ".join(ACTIVE_ROLES)
            )
        )
    existing = roles_for_telegram_id(session, telegram_id)
    if target_role in existing:
        return
    merged = existing + [target_role]
    if ROLE_COACH in merged and ROLE_ADMIN in merged:
        raise ValueError(
            "telegram_id={}: нельзя совмещать тренера и администратора".format(
                telegram_id
            )
        )
    if target_role in STAFF_ROLES and ROLE_ATHLETE in existing:
        raise ValueError(
            "telegram_id={}: staff-роль несовместима со спортсменом с тем же id".format(
                telegram_id
            )
        )
    if target_role == ROLE_ATHLETE and any(r in existing for r in STAFF_ROLES):
        raise ValueError(
            "telegram_id={}: для staff-аккаунта нельзя создать строку спортсмена".format(
                telegram_id
            )
        )


def validate_staff_roles_consistency(session: Session) -> List[str]:
    warnings: List[str] = []

    coaches = (
        session.query(Coach)
        .filter(Coach.telegram_id.isnot(None))
        .all()
    )
    for coach in coaches:
        tid = coach.telegram_id
        admin = session.query(Admin).filter_by(telegram_id=tid).first()
        if admin:
            warnings.append(
                "DUAL_STAFF telegram_id={}: coach id={} и admin id={} — "
                "удалите одну из записей".format(tid, coach.id, admin.id)
            )

    assistant_count = session.query(Assistant).count()
    if assistant_count:
        warnings.append(
            "LEGACY_ASSISTANT: в assistants {} записей (роль не используется)".format(
                assistant_count
            )
        )

    for admin in session.query(Admin).filter(Admin.telegram_id.isnot(None)).all():
        athlete = session.query(Athlete).filter_by(telegram_id=admin.telegram_id).first()
        if athlete:
            warnings.append(
                "STAFF_ATHLETE_CONFLICT telegram_id={}: admin id={} и athlete id={}".format(
                    admin.telegram_id, admin.id, athlete.id
                )
            )

    for coach in coaches:
        athlete = session.query(Athlete).filter_by(telegram_id=coach.telegram_id).first()
        if athlete:
            warnings.append(
                "STAFF_ATHLETE_CONFLICT telegram_id={}: coach id={} и athlete id={}".format(
                    coach.telegram_id, coach.id, athlete.id
                )
            )

    return warnings
