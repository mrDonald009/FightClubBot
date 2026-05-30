import logging
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy import and_, exists, not_, or_, select
from database.models import Attendance, CoachAbsence, GlobalFreeze, Subscription, Training
from utils.time_utils import TRAINING_DURATION, now_moscow

logger = logging.getLogger(__name__)


def _coach_absence_covers_training_exists(coach_id):
    """EXISTS: слот в периоде активного отсутствия тренера coach_id."""
    if not coach_id:
        return None
    return exists(
        select(1).select_from(CoachAbsence).where(
            CoachAbsence.is_active == True,
            CoachAbsence.coach_id == coach_id,
            CoachAbsence.start_date <= Training.training_date,
            CoachAbsence.end_date >= Training.training_date,
        )
    )


def _active_global_freeze_covers_training_exists():
    """Коррелируемый EXISTS: слот Training.training_date попадает в активную массовую заморозку."""
    return exists(
        select(1).select_from(GlobalFreeze).where(
            GlobalFreeze.is_active == True,
            GlobalFreeze.start_date <= Training.training_date,
            GlobalFreeze.end_date >= Training.training_date,
        )
    )


def purge_auto_attendances_during_active_global_freeze(
    session: Session, subscription: Subscription
) -> int:
    """
    Удалить авто-списания (marked_by IS NULL), ошибочно созданные на слоты в периоде массовой заморозки.
    Не зависит от обхода «до сегодня» в migrate.
    """
    q = (
        session.query(Attendance)
        .join(Training, Attendance.training_id == Training.id)
        .filter(
            Attendance.subscription_id == subscription.id,
            or_(Attendance.was_restored == False, Attendance.was_restored == None),
            Attendance.marked_by.is_(None),
            _active_global_freeze_covers_training_exists(),
        )
    )
    rows = q.all()
    for att in rows:
        session.delete(att)
    return len(rows)


def calculate_actual_trainings_remaining(session: Session, subscription: Subscription) -> Optional[int]:
    """
    Source of truth для расчета остатка тренировок.
    Учитывает только завершенные и невосстановленные тренировки в пределах периода абонемента.
    Слоты, попадающие в активную массовую заморозку, в «использованные» не входят (как при авто-списании).
    """
    if subscription.trainings_total is None:
        return None

    current_time = now_moscow()
    completion_cutoff = current_time - TRAINING_DURATION

    filters = [
        Attendance.subscription_id == subscription.id,
        # Тренировка считается использованной только после ее завершения
        Training.training_date <= completion_cutoff,
        or_(Attendance.was_restored == False, Attendance.was_restored == None),
    ]
    if subscription.start_date:
        filters.append(Training.training_date >= subscription.start_date)
    if subscription.end_date:
        filters.append(Training.training_date <= subscription.end_date)
    if subscription.is_frozen and subscription.frozen_from and subscription.frozen_until:
        filters.append(
            or_(
                Training.training_date < subscription.frozen_from,
                Training.training_date > subscription.frozen_until
            )
        )

    # Слоты в периоде активной массовой заморозки не должны списываться (см. auto_deduct_daily_trainings).
    filters.append(not_(_active_global_freeze_covers_training_exists()))

    from .coach_absence import get_subscription_coach_id

    sub_coach_id = get_subscription_coach_id(subscription)
    ca_exists = _coach_absence_covers_training_exists(sub_coach_id)
    if ca_exists is not None:
        filters.append(not_(ca_exists))

    used_count = session.query(Attendance).join(
        Training, Attendance.training_id == Training.id
    ).filter(and_(*filters)).count()

    return max(subscription.trainings_total - used_count, 0)


def sync_subscription_trainings_remaining(
    session: Session,
    subscription: Subscription,
    reason: str = None,
) -> bool:
    """
    Защитная синхронизация остатка.
    Возвращает True, если значение trainings_remaining было изменено.
    """
    new_remaining = calculate_actual_trainings_remaining(session, subscription)
    if new_remaining is None:
        return False
    prev_remaining = subscription.trainings_remaining
    if prev_remaining != new_remaining:
        subscription.trainings_remaining = new_remaining
        reason_text = f" ({reason})" if reason else ""
        logger.info(
            f"Синхронизация trainings_remaining для subscription #{subscription.id}: "
            f"{prev_remaining} -> {new_remaining}{reason_text}"
        )
        return True
    return False
