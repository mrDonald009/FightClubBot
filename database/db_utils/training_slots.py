"""Слоты тренера: пересечение групповых и индивидуальных тренировок (один coach_id, один sport_type)."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from database.models import Subscription, Training
from utils.time_utils import ACTIVATION_GRACE_AFTER_START, training_end_time

TRAINING_FORMAT_GROUP = "group"
TRAINING_FORMAT_INDIVIDUAL = "individual"

MAX_INDIVIDUAL_SAME_SLOT = 4

# Окно записи на индивидуальную тренировку (время начала слота, шаг 30 мин).
INDIVIDUAL_DAY_START_HOUR = 9
INDIVIDUAL_DAY_END_HOUR = 22
INDIVIDUAL_SLOT_STEP_MINUTES = 30

# Групповые занятия по расписанию клуба (блокируют индивидуальные слоты у любого тренера).
SCHEDULED_GROUP_SPORTS = ("MMA", "Тайский Бокс")

# Индивидуальный слот не делится на детей/взрослых: в trainings.age_group храним одно значение.
INDIVIDUAL_TRAINING_AGE_GROUP_STORED = "adults"


def individual_slot_training_ids(session: Session, training: Training) -> List[int]:
    """Все id trainings одного индивидуального слота (тот же тренер, вид спорта, старт). Для групповых — [training.id]."""
    fmt = (getattr(training, "training_format", None) or "").strip().lower()
    if fmt != TRAINING_FORMAT_INDIVIDUAL:
        return [training.id]
    q = (
        session.query(Training.id)
        .filter(
            Training.sport_type == training.sport_type,
            Training.training_date == training.training_date,
            Training.is_cancelled.is_(False),
            Training.training_format == TRAINING_FORMAT_INDIVIDUAL,
        )
        .order_by(Training.id.asc())
    )
    cid = getattr(training, "coach_id", None)
    if cid is not None:
        q = q.filter(Training.coach_id == cid)
    ids = [row[0] for row in q.all()]
    return ids if ids else [training.id]


def dedupe_individual_trainings_by_slot(trainings: List[Training]) -> List[Training]:
    """Одна строка UI/логики на индивидуальный слот (coach_id + sport + момент старта)."""
    individuals_by_key: Dict[Tuple[Optional[int], str, datetime], Training] = {}
    non_ind: List[Training] = []
    for t in sorted(trainings, key=lambda x: (x.training_date, x.id)):
        fmt = (getattr(t, "training_format", None) or "").strip().lower()
        if fmt != TRAINING_FORMAT_INDIVIDUAL:
            non_ind.append(t)
            continue
        key = (getattr(t, "coach_id", None), t.sport_type, t.training_date)
        prev = individuals_by_key.get(key)
        if prev is None or t.id < prev.id:
            individuals_by_key[key] = t
    merged = list(individuals_by_key.values())
    return sorted(non_ind + merged, key=lambda x: x.training_date)


def _intervals_overlap(a0: datetime, a1: datetime, b0: datetime, b1: datetime) -> bool:
    return a0 < b1 and b0 < a1


def scheduled_group_training_intervals(day: date) -> List[Tuple[datetime, datetime]]:
    """
    Интервалы групповых занятий MMA и тайского бокса (детская + взрослая группа)
    на календарный день по TRAINING_SCHEDULE.
    """
    from utils.training_manager import TrainingManager

    weekday = day.weekday()
    intervals: List[Tuple[datetime, datetime]] = []
    for sport_type in SCHEDULED_GROUP_SPORTS:
        sport_sched = TrainingManager.TRAINING_SCHEDULE.get(sport_type) or {}
        for schedule in sport_sched.values():
            days = schedule.get("days") or []
            if weekday not in days:
                continue
            hour, minute = TrainingManager.get_hour_minute_for_weekday(schedule, weekday)
            start = datetime.combine(day, time(hour, minute, 0))
            intervals.append((start, training_end_time(start)))
    return intervals


def _overlaps_any_interval(
    start: datetime, end: datetime, intervals: List[Tuple[datetime, datetime]]
) -> bool:
    for b0, b1 in intervals:
        if _intervals_overlap(start, end, b0, b1):
            return True
    return False


def coach_trainings_on_calendar_day(
    session: Session,
    coach_id: int,
    day: date,
) -> List[Training]:
    """Все неотменённые тренировки тренера в календарный день (все виды спорта)."""
    day_start = datetime.combine(day, time(0, 0, 0))
    day_end = day_start + timedelta(days=1)
    return (
        session.query(Training)
        .filter(
            Training.coach_id == coach_id,
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
    True, если индивидуальный слот [start, end) недоступен:
    - пересечение с групповым расписанием MMA / тайского бокса;
    - пересечение с групповой тренировкой тренера в БД;
    - пересечение с другим индивидуальным слотом того же тренера;
    - уже MAX_INDIVIDUAL_SAME_SLOT активных individual-подписок на этот старт и вид спорта.
    """
    if not coach_id or not sport_type:
        return True
    end_dt = end if end is not None else training_end_time(start)
    day = start.date()

    if _overlaps_any_interval(start, end_dt, scheduled_group_training_intervals(day)):
        return True

    for t in coach_trainings_on_calendar_day(session, coach_id, day):
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
    step_minutes: int = INDIVIDUAL_SLOT_STEP_MINUTES,
    day_start_hour: int = INDIVIDUAL_DAY_START_HOUR,
    day_end_hour: int = INDIVIDUAL_DAY_END_HOUR,
    now_cutoff: Optional[datetime] = None,
) -> List[datetime]:
    """
    Старты индивидуальной тренировки (1,5 ч) в любой день недели:
    с day_start_hour до day_end_hour включительно, шаг step_minutes,
    без пересечения с групповым расписанием MMA/тайского бокса и занятыми слотами тренера.
    Слот доступен до (начало + ACTIVATION_GRACE_AFTER_START), как у групповой активации.
    """
    out: List[datetime] = []
    if step_minutes <= 0:
        step_minutes = INDIVIDUAL_SLOT_STEP_MINUTES
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
