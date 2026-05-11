"""Слоты тренера: пересечение групповых и индивидуальных тренировок (один coach_id, один sport_type)."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session

from database.models import Subscription, Training
from utils.time_utils import ACTIVATION_GRACE_AFTER_START, training_end_time

TRAINING_FORMAT_GROUP = "group"
TRAINING_FORMAT_INDIVIDUAL = "individual"

MAX_INDIVIDUAL_SAME_SLOT = 4


def _intervals_overlap(a0: datetime, a1: datetime, b0: datetime, b1: datetime) -> bool:
    return a0 < b1 and b0 < a1


def coach_trainings_on_calendar_day(
    session: Session,
    coach_id: int,
    sport_type: str,
    day: date,
) -> List[Training]:
    """Все неотменённые тренировки тренера по виду спорта в календарный день (локальное время слотов)."""
    day_start = datetime.combine(day, time(0, 0, 0))
    day_end = day_start + timedelta(days=1)
    return (
        session.query(Training)
        .filter(
            Training.coach_id == coach_id,
            Training.sport_type == sport_type,
            Training.is_cancelled.is_(False),
            Training.training_date >= day_start,
            Training.training_date < day_end,
        )
        .order_by(Training.training_date.asc())
        .all()
)


def individual_slot_conflicts(
    session: Session,
    coach_id: int,
    sport_type: str,
    start: datetime,
    *,
    end: Optional[datetime] = None,
    ignore_training_id: Optional[int] = None,
) -> bool:
    """
    True, если интервал [start, end) пересекается с групповым слотом этого тренера,
    с индивидуальным слотом другого времени, или если кол-во активных индивидуальных
    подписок на этот слот >= MAX_INDIVIDUAL_SAME_SLOT.

    Training-запись для индивидуальных теперь переиспользуется (одна на слот),
    поэтому лимит считается по подпискам (Subscription), а не по Training.
    """
    if not coach_id or not sport_type:
        return True
    end_dt = end if end is not None else training_end_time(start)
    day = start.date()
    for t in coach_trainings_on_calendar_day(session, coach_id, sport_type, day):
        if ignore_training_id is not None and t.id == ignore_training_id:
            continue
        t0 = t.training_date
        t1 = training_end_time(t0)
        if not _intervals_overlap(start, end_dt, t0, t1):
            continue
        fmt = (getattr(t, "training_format", None) or "").strip().lower()
        if fmt != TRAINING_FORMAT_INDIVIDUAL:
            return True
        if t0 != start:
            return True
    booked = (
        session.query(Subscription)
        .filter(
            Subscription.subscription_type == "individual",
            Subscription.is_active.is_(True),
            Subscription.sport_type == sport_type,
            Subscription.start_date == start,
        )
        .count()
    )
    return booked >= MAX_INDIVIDUAL_SAME_SLOT


def iter_allowed_individual_starts(
    session: Session,
    coach_id: int,
    sport_type: str,
    day: date,
    *,
    step_minutes: int = 30,
    day_start_hour: int = 8,
    day_end_hour: int = 21,
    now_cutoff: Optional[datetime] = None,
) -> List[datetime]:
    """
    Старты индивидуальной тренировки длительностью как у групповой (TRAINING_DURATION),
    без пересечений с существующими слотами тренера.
    Слот доступен до (начало + ACTIVATION_GRACE_AFTER_START), как у групповой активации.
    """
    out: List[datetime] = []
    if step_minutes <= 0:
        step_minutes = 30
    now_ts = now_cutoff or datetime.combine(day, time(0, 0, 0))
    t = datetime.combine(day, time(day_start_hour, 0, 0, 0))
    last_start = datetime.combine(day, time(day_end_hour, 0, 0, 0))
    while t <= last_start:
        slot_deadline = t + ACTIVATION_GRACE_AFTER_START
        if now_ts <= slot_deadline and not individual_slot_conflicts(
            session, coach_id, sport_type, t
        ):
            out.append(t)
        t += timedelta(minutes=step_minutes)
    return out


def moscow_day_bounds_for_freeze_check(selected_day: date) -> Tuple[datetime, datetime]:
    """Границы календарного дня для проверки массовой заморозки (день целиком)."""
    d0 = datetime.combine(selected_day, time(0, 0, 0))
    d1 = datetime.combine(selected_day, time(23, 59, 59))
    return d0, d1
