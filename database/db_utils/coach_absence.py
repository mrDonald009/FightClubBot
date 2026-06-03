"""Отсутствие тренера: продление абонементов только его спортсменов, без списаний в период."""
import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from database.models import (
    Coach,
    CoachAbsence,
    CoachAbsenceApplication,
    Subscription,
)
from utils.training_manager import TrainingManager
from utils.time_utils import individual_training_end_time, now_moscow, training_end_time

from .freeze_personal import (
    _count_training_days_between,
    _recalculate_slot_subscription_dates,
)
from .global_freeze import list_active_global_freezes_overlapping_range
from database.models import Attendance, Training
from sqlalchemy import or_

from .remaining import sync_subscription_trainings_remaining

logger = logging.getLogger(__name__)


def purge_auto_attendances_during_coach_absence(
    session: Session, subscription: Subscription, coach_id: int
) -> int:
    """Удалить авто-списания на слоты в периоде отсутствия этого тренера."""
    q = (
        session.query(Attendance)
        .join(Training, Attendance.training_id == Training.id)
        .filter(
            Attendance.subscription_id == subscription.id,
            or_(Attendance.was_restored == False, Attendance.was_restored == None),
            Attendance.marked_by.is_(None),
        )
    )
    rows = []
    for att in q.all():
        if att.training and is_training_in_coach_absence(
            session, att.training.training_date, coach_id
        ):
            rows.append(att)
    for att in rows:
        session.delete(att)
    return len(rows)


def get_subscription_coach_id(subscription: Subscription) -> Optional[int]:
    """Тренер абонемента: ответственный или создавший спортсмена."""
    if subscription.responsible_coach_id:
        return subscription.responsible_coach_id
    athlete = subscription.athlete
    if athlete and athlete.created_by:
        return athlete.created_by
    return None


def subscription_belongs_to_coach(subscription: Subscription, coach_id: int) -> bool:
    sub_coach = get_subscription_coach_id(subscription)
    return sub_coach is not None and sub_coach == coach_id


def is_training_in_coach_absence(
    session: Session, training_datetime: datetime, coach_id: int
) -> bool:
    """Слот попадает в активное отсутствие указанного тренера."""
    if not coach_id:
        return False
    row = (
        session.query(CoachAbsence.id)
        .filter(
            CoachAbsence.is_active == True,
            CoachAbsence.coach_id == coach_id,
            CoachAbsence.start_date <= training_datetime,
            CoachAbsence.end_date >= training_datetime,
        )
        .first()
    )
    return row is not None


def is_training_in_coach_absence_for_subscription(
    session: Session, training_datetime: datetime, subscription: Subscription
) -> bool:
    coach_id = get_subscription_coach_id(subscription)
    if not coach_id:
        return False
    return is_training_in_coach_absence(session, training_datetime, coach_id)


def list_active_coach_absences_overlapping_range(
    session: Session,
    coach_id: int,
    start_date: datetime,
    end_date: datetime,
):
    freeze_start = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
    freeze_end = end_date.replace(hour=23, minute=59, second=59, microsecond=999999)
    return (
        session.query(CoachAbsence)
        .filter(CoachAbsence.coach_id == coach_id)
        .filter(CoachAbsence.is_active == True)
        .filter(CoachAbsence.start_date <= freeze_end)
        .filter(CoachAbsence.end_date >= freeze_start)
        .order_by(CoachAbsence.start_date.asc())
        .all()
    )


def _count_global_freeze_overlap_training_days(
    session: Session,
    period_start: datetime,
    period_end: datetime,
    sport_type: str,
    age_group: str,
) -> int:
    """Тренировочные дни периода, уже покрытые активной массовой заморозкой клуба."""
    total = 0
    for gf in list_active_global_freezes_overlapping_range(session, period_start, period_end):
        o_start = max(period_start, gf.start_date)
        o_end = min(period_end, gf.end_date)
        if o_end >= o_start:
            total += _count_training_days_between(o_start, o_end, sport_type, age_group)
    return total


def _extend_subscription_end_by_training_days(
    session: Session,
    subscription: Subscription,
    sport_type: str,
    age_group: str,
    training_days_count: int,
) -> int:
    """Продлить end_date (и start для single/individual). Возвращает фактически добавленные дни."""
    if training_days_count <= 0 or not subscription.end_date:
        return 0

    is_slot = subscription.subscription_type in ("single", "individual")
    schedule = TrainingManager.TRAINING_SCHEDULE.get(sport_type, {}).get(age_group)
    if not schedule:
        subscription.end_date = subscription.end_date + timedelta(days=training_days_count)
        return training_days_count

    days = schedule["days"]
    end_date_only = subscription.end_date.replace(hour=0, minute=0, second=0, microsecond=0)
    current_date = end_date_only + timedelta(days=1)
    added = 0
    searched = 0
    while added < training_days_count and searched < 180:
        if current_date.weekday() in days:
            added += 1
            if added == training_days_count:
                hour, minute = TrainingManager.get_hour_minute_for_weekday(
                    schedule, current_date.weekday()
                )
                training_start = current_date.replace(
                    hour=hour, minute=minute, second=0, microsecond=0
                )
                if subscription.subscription_type == "individual":
                    from database.db_utils.club_settings import (
                        get_individual_training_duration_minutes,
                    )

                    subscription.end_date = individual_training_end_time(
                        training_start,
                        get_individual_training_duration_minutes(session),
                    )
                else:
                    subscription.end_date = training_end_time(training_start)
                if is_slot:
                    subscription.start_date = training_start
                break
        current_date += timedelta(days=1)
        searched += 1
    return added


def apply_coach_absence(
    session: Session,
    coach_id: int,
    start_date: datetime,
    end_date: datetime,
    title: str,
    created_by: int = None,
) -> dict:
    """Отсутствие тренера: продление абонементов его спортсменов на пропущенные трен. дни."""
    if end_date < start_date:
        return {"success": False, "message": "Дата окончания меньше даты начала"}

    coach = session.query(Coach).filter_by(id=coach_id).first()
    if not coach:
        return {"success": False, "message": "Тренер не найден"}

    freeze_start = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
    freeze_end = end_date.replace(hour=23, minute=59, second=59, microsecond=999999)

    overlapping = list_active_coach_absences_overlapping_range(
        session, coach_id, start_date, end_date
    )
    if overlapping:
        ids = ", ".join(str(ca.id) for ca in overlapping[:5])
        if len(overlapping) > 5:
            ids += ", …"
        return {
            "success": False,
            "message": (
                "Период пересекается с уже зарегистрированной отменой тренировок "
                f"(ID: {ids})."
            ),
        }

    absence = CoachAbsence(
        coach_id=coach_id,
        title=title.strip() or "Отмена тренировки",
        start_date=freeze_start,
        end_date=freeze_end,
        is_active=True,
        created_by=created_by,
        created_at=now_moscow(),
    )
    session.add(absence)
    session.flush()

    updated = 0
    skipped = 0
    total_training_days_added = 0

    active_subscriptions = session.query(Subscription).filter(
        Subscription.is_active == True
    ).all()

    for subscription in active_subscriptions:
        if not subscription_belongs_to_coach(subscription, coach_id):
            continue

        existing = session.query(CoachAbsenceApplication).filter_by(
            coach_absence_id=absence.id,
            subscription_id=subscription.id,
        ).first()
        if existing:
            skipped += 1
            continue

        athlete = subscription.athlete
        if not athlete:
            skipped += 1
            continue

        sport_type = subscription.sport_type or athlete.sport_type
        age_group = athlete.age_group
        if not sport_type or not age_group or not subscription.end_date:
            skipped += 1
            continue

        period_start = max(subscription.start_date or freeze_start, freeze_start)
        period_end = min(subscription.end_date, freeze_end)
        if period_end < period_start:
            app = CoachAbsenceApplication(
                coach_absence_id=absence.id,
                subscription_id=subscription.id,
                training_days_added=0,
                old_end_date=subscription.end_date,
                new_end_date=subscription.end_date,
                old_start_date=subscription.start_date,
                new_start_date=subscription.start_date,
                created_at=now_moscow(),
            )
            session.add(app)
            skipped += 1
            continue

        training_days_count = _count_training_days_between(
            period_start, period_end, sport_type, age_group
        )

        overlap_personal = 0
        if subscription.is_frozen and subscription.frozen_from and subscription.frozen_until:
            overlap_start = max(period_start, subscription.frozen_from)
            overlap_end = min(period_end, subscription.frozen_until)
            if overlap_end >= overlap_start:
                overlap_personal = _count_training_days_between(
                    overlap_start, overlap_end, sport_type, age_group
                )

        overlap_gf = _count_global_freeze_overlap_training_days(
            session, period_start, period_end, sport_type, age_group
        )

        effective_training_days = max(
            training_days_count - overlap_personal - overlap_gf, 0
        )
        old_end_date = subscription.end_date
        old_start_date = subscription.start_date
        new_end_date = old_end_date
        new_start_date = old_start_date

        if effective_training_days > 0:
            added = _extend_subscription_end_by_training_days(
                session,
                subscription,
                sport_type,
                age_group,
                effective_training_days,
            )
            new_end_date = subscription.end_date
            new_start_date = subscription.start_date
            purge_auto_attendances_during_coach_absence(session, subscription, coach_id)
            sync_subscription_trainings_remaining(
                session, subscription, reason="after_coach_absence"
            )
            updated += 1
            total_training_days_added += added
        else:
            skipped += 1

        app = CoachAbsenceApplication(
            coach_absence_id=absence.id,
            subscription_id=subscription.id,
            training_days_added=effective_training_days,
            old_end_date=old_end_date,
            new_end_date=new_end_date,
            old_start_date=old_start_date,
            new_start_date=new_start_date,
            created_at=now_moscow(),
        )
        session.add(app)

    session.commit()

    coach_label = (coach.first_name or "").strip() or f"ID {coach_id}"
    logger.info(
        "Coach absence apply: id=%s coach=%s range=%s..%s updated=%s skipped=%s days=%s",
        absence.id,
        coach_id,
        freeze_start.strftime("%Y-%m-%d"),
        freeze_end.strftime("%Y-%m-%d"),
        updated,
        skipped,
        total_training_days_added,
    )
    return {
        "success": True,
        "message": "Отмена тренировок зарегистрирована",
        "coach_absence_id": absence.id,
        "coach_name": coach_label,
        "updated_subscriptions": updated,
        "skipped_subscriptions": skipped,
        "total_training_days_added": total_training_days_added,
        "start_date": freeze_start,
        "end_date": freeze_end,
    }


def _subscription_has_other_active_ca_extensions(
    session: Session, subscription_id: int, exclude_ca_id: int
) -> bool:
    return (
        session.query(CoachAbsenceApplication.id)
        .join(CoachAbsence, CoachAbsenceApplication.coach_absence_id == CoachAbsence.id)
        .filter(
            CoachAbsenceApplication.subscription_id == subscription_id,
            CoachAbsenceApplication.training_days_added > 0,
            CoachAbsence.is_active == True,
            CoachAbsence.id != exclude_ca_id,
        )
        .first()
        is not None
    )


def deactivate_coach_absence_and_migrate(session: Session, ca_id: int) -> dict:
    """Деактивировать отсутствие тренера и пересчитать затронутые абонементы."""
    ca = session.query(CoachAbsence).filter_by(id=ca_id).first()
    if not ca:
        return {
            "success": False,
            "message": f"Период отмены с ID={ca_id} не найден",
            "migrated": 0,
        }

    if not ca.is_active:
        return {
            "success": True,
            "already_inactive": True,
            "message": f"Период отмены #{ca_id} уже отключён",
            "migrated": 0,
            "checked": 0,
            "synced": 0,
            "coach_absence_id": ca_id,
        }

    now = now_moscow()
    if ca.end_date and ca.end_date < (now - timedelta(days=30)):
        return {
            "success": False,
            "message": (
                f"Период отмены #{ca_id} завершился более 30 дней назад. "
                "Используйте ручной регламент."
            ),
            "migrated": 0,
            "checked": 0,
            "synced": 0,
        }

    ca.is_active = False
    session.commit()

    early_cancel = bool(ca.end_date and now < ca.end_date)
    subscription_ids = [
        x[0]
        for x in session.query(CoachAbsenceApplication.subscription_id)
        .filter(CoachAbsenceApplication.coach_absence_id == ca_id)
        .distinct()
        .all()
    ]

    from .migrate import migrate_existing_subscription

    migrated = 0
    checked = 0
    synced = 0
    updated = 0
    reverted = 0

    for sid in subscription_ids:
        sub = session.query(Subscription).filter_by(id=sid).first()
        if not sub:
            continue
        checked += 1
        sub_updated = False
        preserve_end_date = False
        app = (
            session.query(CoachAbsenceApplication)
            .filter_by(coach_absence_id=ca_id, subscription_id=sid)
            .first()
        )
        if (
            early_cancel
            and app
            and (app.training_days_added or 0) > 0
            and app.old_end_date
            and not _subscription_has_other_active_ca_extensions(session, sid, ca_id)
        ):
            if sub.end_date != app.old_end_date:
                sub.end_date = app.old_end_date
                sub_updated = True
            if app.old_start_date is not None and sub.start_date != app.old_start_date:
                sub.start_date = app.old_start_date
                sub_updated = True
            preserve_end_date = True
            reverted += 1

        athlete = sub.athlete
        if athlete and athlete.sport_type and athlete.age_group:
            sport_type = sub.sport_type or athlete.sport_type
            _recalculate_slot_subscription_dates(
                session, sub, sport_type, athlete.age_group
            )

        if sub.subscription_type == "monthly":
            migrate_result = migrate_existing_subscription(
                session, sid, preserve_end_date=preserve_end_date
            )
            migrated += 1
            if migrate_result.get("success") and (
                migrate_result.get("message") == "Абонемент обновлен"
                or migrate_result.get("changes")
            ):
                sub_updated = True
        if sync_subscription_trainings_remaining(
            session, sub, reason="after_coach_absence_deactivate"
        ):
            synced += 1
            sub_updated = True
        if sub_updated:
            updated += 1

    if synced > 0 or reverted > 0:
        session.commit()

    return {
        "success": True,
        "already_inactive": False,
        "message": "Отключено",
        "migrated": migrated,
        "checked": checked,
        "synced": synced,
        "updated_subscriptions": updated,
        "skipped_subscriptions": max(checked - updated, 0),
        "title": ca.title,
        "coach_absence_id": ca_id,
    }
