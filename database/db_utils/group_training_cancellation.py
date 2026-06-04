"""Отмена группового занятия (день + слот): is_cancelled и продление monthly как при заморозке."""
import logging
from datetime import datetime, timedelta
from typing import List, Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from database.models import (
    Athlete,
    Attendance,
    GroupTrainingCancellationApplication,
    Subscription,
    Training,
)
from utils.time_utils import now_moscow

from .coach_absence import (
    _extend_subscription_end_by_training_days,
    _subscription_has_other_active_ca_extensions,
    is_group_monthly_subscription,
    subscription_belongs_to_coach,
)
from .freeze_personal import _recalculate_slot_subscription_dates
from .global_freeze import is_training_in_global_freeze
from .remaining import sync_subscription_trainings_remaining
from .training_slots import (
    TRAINING_FORMAT_GROUP,
    find_group_training_on_calendar_day,
    is_group_training,
)

logger = logging.getLogger(__name__)


def resolve_or_create_group_training(
    session: Session,
    coach_id: int,
    sport_type: str,
    age_group: str,
    training_datetime: datetime,
) -> Optional[Training]:
    """Найти или создать групповую запись Training на слот."""
    sport_type = (sport_type or "").strip()
    if not sport_type or not coach_id:
        return None
    training = find_group_training_on_calendar_day(
        session,
        sport_type=sport_type,
        age_group=age_group,
        day=training_datetime.date(),
        coach_id=coach_id,
    )
    if training and training.training_date.replace(second=0, microsecond=0) == training_datetime.replace(
        second=0, microsecond=0
    ):
        return training
    q = (
        session.query(Training)
        .filter(
            Training.sport_type == sport_type,
            Training.age_group == age_group,
            Training.training_date == training_datetime,
            Training.coach_id == coach_id,
        )
        .first()
    )
    if q:
        if is_group_training(q) or not (getattr(q, "training_format", None) or "").strip():
            return q
        return None
    training = Training(
        sport_type=sport_type,
        age_group=age_group,
        training_date=training_datetime,
        coach_id=coach_id,
        is_cancelled=False,
        training_format=TRAINING_FORMAT_GROUP,
    )
    session.add(training)
    session.flush()
    return training


def _subscription_covers_training(subscription: Subscription, training_dt: datetime) -> bool:
    if not subscription.start_date or not subscription.end_date:
        return False
    return subscription.start_date <= training_dt <= subscription.end_date


def _purge_auto_attendance_for_training(
    session: Session, subscription: Subscription, training_id: int
) -> int:
    q = session.query(Attendance).filter(
        Attendance.subscription_id == subscription.id,
        Attendance.training_id == training_id,
        or_(Attendance.was_restored == False, Attendance.was_restored == None),
        Attendance.marked_by.is_(None),
    )
    rows = q.all()
    for att in rows:
        session.delete(att)
    return len(rows)


def apply_group_training_cancellation(
    session: Session,
    coach_id: int,
    sport_type: str,
    age_group: str,
    training_datetime: datetime,
    created_by: int = None,
) -> dict:
    """
    Отменить одно групповое занятие: пометить Training, продлить monthly абонементы
    спортсmenов тренера этой возрастной группы на 1 тренировочный день (если нет GF на слот).
    """
    training_datetime = training_datetime.replace(second=0, microsecond=0)
    training = resolve_or_create_group_training(
        session, coach_id, sport_type, age_group, training_datetime
    )
    if not training:
        return {"success": False, "message": "Не удалось найти или создать групповое занятие"}

    if training.is_cancelled:
        return {
            "success": False,
            "message": "Это занятие уже отменено",
            "training_id": training.id,
        }

    if is_training_in_global_freeze(session, training_datetime):
        return {
            "success": False,
            "message": (
                "На эту дату действует массовая заморозка клуба. "
                "Используйте регламент администратора."
            ),
        }

    training.is_cancelled = True
    session.flush()

    updated = 0
    skipped = 0

    active_subscriptions = session.query(Subscription).filter(
        Subscription.is_active == True
    ).all()

    for subscription in active_subscriptions:
        if not subscription_belongs_to_coach(subscription, coach_id):
            continue
        if not is_group_monthly_subscription(subscription):
            skipped += 1
            continue
        athlete = subscription.athlete
        if not athlete or normalize_athlete_age_group(athlete, age_group) is False:
            skipped += 1
            continue
        sub_sport = subscription.sport_type or athlete.sport_type
        if (sub_sport or "").strip() != (sport_type or "").strip():
            skipped += 1
            continue
        if not _subscription_covers_training(subscription, training_datetime):
            skipped += 1
            continue

        existing_app = session.query(GroupTrainingCancellationApplication).filter_by(
            training_id=training.id,
            subscription_id=subscription.id,
        ).first()
        if existing_app:
            skipped += 1
            continue

        old_end_date = subscription.end_date
        old_start_date = subscription.start_date
        added = _extend_subscription_end_by_training_days(
            session,
            subscription,
            sport_type,
            age_group,
            1,
        )
        app = GroupTrainingCancellationApplication(
            training_id=training.id,
            subscription_id=subscription.id,
            training_days_added=added,
            old_end_date=old_end_date,
            new_end_date=subscription.end_date,
            old_start_date=old_start_date,
            new_start_date=subscription.start_date,
            created_at=now_moscow(),
        )
        session.add(app)
        if added > 0:
            updated += 1
        _purge_auto_attendance_for_training(session, subscription, training.id)
        sync_subscription_trainings_remaining(
            session, subscription, reason="after_group_training_cancel"
        )

    session.commit()

    logger.info(
        "Group training cancel: training_id=%s coach=%s %s %s %s updated=%s skipped=%s by=%s",
        training.id,
        coach_id,
        sport_type,
        age_group,
        training_datetime.isoformat(),
        updated,
        skipped,
        created_by,
    )
    return {
        "success": True,
        "message": "Групповое занятие отменено",
        "training_id": training.id,
        "updated_subscriptions": updated,
        "skipped_subscriptions": skipped,
        "training_datetime": training_datetime,
    }


def normalize_athlete_age_group(athlete: Athlete, slot_age_group: str) -> bool:
    from utils.age_groups import normalize_age_group

    ag = normalize_age_group(athlete.age_group) or athlete.age_group
    slot_ag = normalize_age_group(slot_age_group) or slot_age_group
    return ag == slot_ag


def list_cancelled_group_trainings(
    session: Session, coach_id: int, limit: int = 10
) -> List[Training]:
    return (
        session.query(Training)
        .filter(
            Training.coach_id == coach_id,
            Training.is_cancelled == True,
        )
        .order_by(Training.training_date.desc())
        .limit(limit)
        .all()
    )


def _subscription_has_other_active_gtc_extensions(
    session: Session, subscription_id: int, exclude_training_id: int
) -> bool:
    """Есть ли другие активные точечные отмены с продлением по этому абонементу."""
    return (
        session.query(GroupTrainingCancellationApplication.id)
        .join(Training, GroupTrainingCancellationApplication.training_id == Training.id)
        .filter(
            GroupTrainingCancellationApplication.subscription_id == subscription_id,
            GroupTrainingCancellationApplication.training_days_added > 0,
            Training.is_cancelled == True,
            Training.id != exclude_training_id,
        )
        .first()
        is not None
    )


def list_rollbackable_cancelled_group_trainings(
    session: Session, coach_id: int, limit: int = 15
) -> List[Training]:
    """Отменённые групповые занятия, доступные для отката (не старше 30 дней)."""
    from .training_slots import TRAINING_FORMAT_INDIVIDUAL, is_group_training

    cutoff = now_moscow() - timedelta(days=30)
    rows = (
        session.query(Training)
        .filter(
            Training.coach_id == coach_id,
            Training.is_cancelled == True,
            Training.training_date >= cutoff,
        )
        .order_by(Training.training_date.desc())
        .limit(limit * 2)
        .all()
    )
    out = []
    for t in rows:
        fmt = (getattr(t, "training_format", None) or "").strip().lower()
        if fmt == TRAINING_FORMAT_INDIVIDUAL:
            continue
        if fmt or is_group_training(t):
            out.append(t)
        if len(out) >= limit:
            break
    return out


def revert_group_training_cancellation(
    session: Session, training_id: int, coach_id: int = None
) -> dict:
    """Снять отмену занятия и откатить продления monthly по записи аудита."""
    training = session.query(Training).filter_by(id=training_id).first()
    if not training:
        return {"success": False, "message": f"Занятие с ID={training_id} не найдено"}

    if coach_id is not None and training.coach_id != coach_id:
        return {"success": False, "message": "Занятие принадлежит другому тренеру"}

    if not training.is_cancelled:
        return {
            "success": True,
            "already_reverted": True,
            "message": "Занятие уже не отмечено как отменённое",
            "training_id": training_id,
            "checked": 0,
            "reverted": 0,
            "updated_subscriptions": 0,
        }

    now = now_moscow()
    if training.training_date and training.training_date < (now - timedelta(days=30)):
        return {
            "success": False,
            "message": (
                "Занятие отменено более 30 дней назад. "
                "Используйте ручной регламент."
            ),
        }

    training.is_cancelled = False
    session.flush()

    apps = (
        session.query(GroupTrainingCancellationApplication)
        .filter_by(training_id=training_id)
        .all()
    )
    has_audit = bool(apps)
    subscription_ids = list({a.subscription_id for a in apps})

    from .migrate import migrate_existing_subscription

    checked = 0
    reverted = 0
    updated = 0
    synced = 0
    migrated = 0

    for sid in subscription_ids:
        sub = session.query(Subscription).filter_by(id=sid).first()
        if not sub:
            continue
        checked += 1
        sub_updated = False
        preserve_end_date = False
        app = next((a for a in apps if a.subscription_id == sid), None)
        if (
            app
            and (app.training_days_added or 0) > 0
            and app.old_end_date
            and not _subscription_has_other_active_ca_extensions(session, sid, -1)
            and not _subscription_has_other_active_gtc_extensions(
                session, sid, training_id
            )
        ):
            if sub.end_date == app.new_end_date:
                sub.end_date = app.old_end_date
                sub_updated = True
                reverted += 1
                preserve_end_date = True
            if app.old_start_date is not None and sub.start_date == app.new_start_date:
                sub.start_date = app.old_start_date
                sub_updated = True

        athlete = sub.athlete
        if athlete and athlete.sport_type and athlete.age_group:
            sport_type = sub.sport_type or athlete.sport_type
            _recalculate_slot_subscription_dates(
                session, sub, sport_type, athlete.age_group
            )

        if sub.subscription_type == "monthly":
            migrate_existing_subscription(
                session, sid, preserve_end_date=preserve_end_date
            )
            migrated += 1

        if sync_subscription_trainings_remaining(
            session, sub, reason="after_group_training_cancel_revert"
        ):
            synced += 1
            sub_updated = True
        if sub_updated:
            updated += 1

    session.commit()

    msg = "Отмена занятия снята"
    if not has_audit:
        msg += " (продления не восстановлены — нет записи аудита)"

    return {
        "success": True,
        "already_reverted": False,
        "message": msg,
        "training_id": training_id,
        "checked": checked,
        "reverted": reverted,
        "migrated": migrated,
        "synced": synced,
        "updated_subscriptions": updated,
        "has_audit": has_audit,
    }
