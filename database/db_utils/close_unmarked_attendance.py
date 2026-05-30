"""
Фиксация в БД «не отмечено» после конца пары: создаётся Attendance (attended=False),
как в UI после окончания слота (без дополнительной задержки). Списание остатка — как при ручном «не был».

После окончания слота lock_attendances_for_ended_trainings выставляет locked_at у уже
созданных отметок и списывает остаток за «Был».
"""
import logging
from datetime import datetime, timedelta
from typing import Set

from sqlalchemy import func
from sqlalchemy.orm import Session

from database.models import Athlete, Attendance, Subscription, Training
from utils.subscription_checker import SubscriptionChecker
from utils.time_utils import (
    ATTENDANCE_UNMARKED_TO_ABSENT_AFTER_TRAINING_END,
    INDIVIDUAL_TRAINING_DURATION,
    TRAINING_DURATION,
    now_moscow,
    training_slot_end_time,
)

from .freeze_personal import is_training_in_athlete_personal_freeze
from .global_freeze import is_training_in_global_freeze
from .training_slots import (
    TRAINING_FORMAT_INDIVIDUAL,
    dedupe_individual_trainings_by_slot,
    individual_slot_training_ids,
)

logger = logging.getLogger(__name__)


def lock_attendances_after_calendar_day_end(session: Session) -> int:
    """
    Фиксация отметок после окончания календарного дня тренировки (Москва).

    Все attendances за даты строго раньше «сегодня» получают locked_at.
    В течение дня тренировки тренер может менять «был/не был»; с полуночи — нет.
    Для «Был» при фиксации списывается остаток абонемента (если ещё не списан).
    """
    now = now_moscow()
    today = now.date()
    rows = (
        session.query(Attendance)
        .join(Training, Training.id == Attendance.training_id)
        .filter(
            Training.is_cancelled.is_(False),
            func.date(Training.training_date) < today,
            Attendance.locked_at.is_(None),
        )
        .all()
    )
    n = 0
    for att in rows:
        att.locked_at = now
        training = session.query(Training).filter_by(id=att.training_id).first()
        subscription = (
            session.query(Subscription).filter_by(id=att.subscription_id).first()
            if att.subscription_id
            else None
        )
        if training and subscription:
            apply_trainings_remaining_on_present_after_lock(
                session, att, training, subscription
            )
        n += 1
    if n:
        logger.info(
            "lock_attendances_after_calendar_day_end: locked_at для %s отметок (дни до %s)",
            n,
            today.isoformat(),
        )
    return n


def lock_attendances_for_ended_trainings(session: Session) -> int:
    """
    Устаревшая фиксация по концу слота (1 ч / 90 мин). Предпочтительно
    lock_attendances_after_calendar_day_end — фиксация в полночь после дня тренировки.
    """
    now = now_moscow()
    min_duration = min(TRAINING_DURATION, INDIVIDUAL_TRAINING_DURATION)
    boundary = now - min_duration
    q = (
        session.query(Attendance)
        .join(Training, Training.id == Attendance.training_id)
        .filter(
            Training.is_cancelled.is_(False),
            Training.training_date <= boundary,
            Attendance.locked_at.is_(None),
        )
    )
    n = 0
    for att in q:
        training = session.query(Training).filter_by(id=att.training_id).first()
        if training and now < training_slot_end_time(
            training.training_date, getattr(training, "training_format", None)
        ):
            continue
        tid = att.training_id
        sid = att.subscription_id
        att.locked_at = now
        if training is None:
            training = session.query(Training).filter_by(id=tid).first()
        subscription = session.query(Subscription).filter_by(id=sid).first()
        if training and subscription:
            apply_trainings_remaining_on_present_after_lock(
                session, att, training, subscription
            )
        n += 1
    return n


def _close_unmarked_individual_training(
    session: Session, training: Training, now: datetime
) -> int:
    """Одна индивидуальная пара: неявное посещение только у абонемента с тем же start_date."""
    rows = (
        session.query(Athlete, Subscription)
        .join(Subscription, Subscription.athlete_id == Athlete.id)
        .filter(
            Subscription.is_active.is_(True),
            Subscription.subscription_type == "individual",
            Subscription.sport_type == training.sport_type,
            Athlete.created_by == training.coach_id,
            func.strftime("%Y-%m-%d %H:%M", Subscription.start_date)
            == func.strftime("%Y-%m-%d %H:%M", training.training_date),
        )
        .order_by(Subscription.id.asc())
        .all()
    )
    if not rows:
        return 0
    slot_tids = individual_slot_training_ids(session, training)
    canonical_tid = min(slot_tids)
    inserted = 0
    for athlete, subscription in rows:
        if (
            session.query(Attendance.id)
            .filter(
                Attendance.athlete_id == athlete.id,
                Attendance.training_id.in_(slot_tids),
            )
            .first()
        ):
            continue
        status = SubscriptionChecker.get_subscription_status(subscription)
        if status != "active":
            continue
        if subscription.is_frozen:
            continue
        session.add(
            Attendance(
                athlete_id=athlete.id,
                training_id=canonical_tid,
                subscription_id=subscription.id,
                attended=False,
                marked_by=None,
                created_at=now,
                locked_at=now,
            )
        )
        if _should_deduct_on_system_absence(session, subscription, training, athlete.id):
            subscription.trainings_remaining -= 1
        inserted += 1
        logger.info(
            "implicit attendance (individual): training_id=%s athlete_id=%s sub_id=%s",
            canonical_tid,
            athlete.id,
            subscription.id,
        )
    return inserted


def apply_trainings_remaining_on_present_after_lock(
    session: Session,
    attendance: Attendance,
    training: Training,
    subscription: Subscription,
) -> None:
    """
    После выставления locked_at: если итог «Был», один раз списать остаток.
    Во время пары остаток не трогаем — только здесь и в ветках неявного «не был».
    """
    if not attendance.attended:
        return
    athlete_id = attendance.athlete_id
    training_in_freeze = is_training_in_global_freeze(
        session, training.training_date
    ) or (
        subscription.is_frozen
        and subscription.frozen_from
        and subscription.frozen_until
        and subscription.frozen_from <= training.training_date <= subscription.frozen_until
    ) or is_training_in_athlete_personal_freeze(session, athlete_id, training.training_date)
    if training_in_freeze:
        return
    if subscription.trainings_remaining is None or subscription.trainings_remaining <= 0:
        return
    subscription.trainings_remaining -= 1
    logger.info(
        "post-lock deduct (присутствие): attendance_id=%s sub_id=%s остаток=%s",
        attendance.id,
        subscription.id,
        subscription.trainings_remaining,
    )


def _should_deduct_on_system_absence(
    session: Session,
    subscription: Subscription,
    training: Training,
    athlete_id: int,
) -> bool:
    training_in_freeze = is_training_in_global_freeze(
        session, training.training_date
    ) or (
        subscription.is_frozen
        and subscription.frozen_from
        and subscription.frozen_until
        and subscription.frozen_from <= training.training_date <= subscription.frozen_until
    ) or is_training_in_athlete_personal_freeze(session, athlete_id, training.training_date)
    if training_in_freeze:
        return False
    if subscription.trainings_remaining is None or subscription.trainings_remaining <= 0:
        return False
    return True


def close_unmarked_attendance_after_grace(
    session: Session,
    *,
    lookback_days: int = 180,
) -> int:
    """
    Для тренировок, у которых пара уже закончилась (конец слота <= now с учётом grace),
    для спортсменов с активным абонементом на этот день — вставить строку attendances,
    если её ещё нет (attended=False, marked_by=None).

    Returns:
        Количество вставленных строк.
    """
    now = now_moscow()
    start_floor = now - timedelta(days=lookback_days)
    min_duration = min(TRAINING_DURATION, INDIVIDUAL_TRAINING_DURATION)
    latest_eligible_start = (
        now - min_duration - ATTENDANCE_UNMARKED_TO_ABSENT_AFTER_TRAINING_END
    )

    trainings = (
        session.query(Training)
        .filter(
            Training.is_cancelled == False,
            Training.training_date >= start_floor,
            Training.training_date <= latest_eligible_start,
        )
        .order_by(Training.id.asc())
        .all()
    )
    trainings = dedupe_individual_trainings_by_slot(trainings)

    inserted = 0
    for training in trainings:
        slot_end = training_slot_end_time(
            training.training_date, getattr(training, "training_format", None)
        )
        if now <= slot_end + ATTENDANCE_UNMARKED_TO_ABSENT_AFTER_TRAINING_END:
            continue
        if is_training_in_global_freeze(session, training.training_date):
            continue

        if getattr(training, "training_format", None) == TRAINING_FORMAT_INDIVIDUAL:
            inserted += _close_unmarked_individual_training(session, training, now)
            continue

        training_day = training.training_date.date()

        pairs = (
            session.query(Athlete, Subscription)
            .join(Subscription, Subscription.athlete_id == Athlete.id)
            .filter(
                Subscription.is_active == True,
                Subscription.sport_type == training.sport_type,
                Athlete.age_group == training.age_group,
                func.date(Subscription.start_date) <= training_day,
                func.date(Subscription.end_date) >= training_day,
                # На групповых слотах не используем individual-абонементы.
                func.lower(func.coalesce(func.trim(Subscription.subscription_type), ""))
                != "individual",
            )
            .order_by(Athlete.id.asc(), Subscription.id.asc())
            .all()
        )

        seen_athletes: Set[int] = set()
        for athlete, subscription in pairs:
            if athlete.id in seen_athletes:
                continue
            seen_athletes.add(athlete.id)

            if (
                session.query(Attendance.id)
                .filter_by(athlete_id=athlete.id, training_id=training.id)
                .first()
            ):
                continue

            status = SubscriptionChecker.get_subscription_status(subscription)
            if status != "active":
                continue

            if subscription.is_frozen:
                continue

            att = Attendance(
                athlete_id=athlete.id,
                training_id=training.id,
                subscription_id=subscription.id,
                attended=False,
                marked_by=None,
                created_at=now,
                locked_at=now,
            )
            session.add(att)

            if _should_deduct_on_system_absence(
                session, subscription, training, athlete.id
            ):
                subscription.trainings_remaining -= 1

            inserted += 1
            logger.info(
                "implicit attendance row: training_id=%s athlete_id=%s sub_id=%s",
                training.id,
                athlete.id,
                subscription.id,
            )

    return inserted
