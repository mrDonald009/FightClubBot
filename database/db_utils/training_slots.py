"""Слоты тренера: пересечение групповых и индивидуальных тренировок (один coach_id, один sport_type)."""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from database.models import Athlete, Attendance, Subscription, Training
from utils.age_groups import AGE_GROUP_CHILDREN, AGE_GROUP_MIDDLE, normalize_age_group
from utils.time_utils import (
    ACTIVATION_GRACE_AFTER_START,
    individual_training_end_time,
    training_end_time,
    training_slot_end_time,
)

TRAINING_FORMAT_GROUP = "group"
TRAINING_FORMAT_INDIVIDUAL = "individual"

MAX_INDIVIDUAL_SAME_SLOT = 4

# Окно записи на индивидуальную тренировку (время начала слота, шаг 30 мин).
# INDIVIDUAL_DAY_END_HOUR — последний допустимый старт (включительно); при длительности 1 ч
# пара заканчивается в (END_HOUR + 1):00, напр. 17:00–18:00 в субботу после утренних групп тайского.
INDIVIDUAL_DAY_START_HOUR = 8
INDIVIDUAL_DAY_END_HOUR = 17
INDIVIDUAL_SLOT_STEP_MINUTES = 30

# Групповые занятия по расписанию клуба (блокируют индивидуальные слоты у любого тренера).
SCHEDULED_GROUP_SPORTS = ("MMA", "Тайский Бокс")

# Индивидуальный слот не делится на детей/взрослых: в trainings.age_group храним одно значение.
INDIVIDUAL_TRAINING_AGE_GROUP_STORED = "adults"

logger = logging.getLogger(__name__)


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
    Интервалы групповых занятий MMA и тайского бокса (все возрастные группы)
    на календарный день по TRAINING_SCHEDULE (длительность групповой пары).
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


def is_group_training(training: Training) -> bool:
    fmt = (getattr(training, "training_format", None) or "").strip().lower()
    return fmt != TRAINING_FORMAT_INDIVIDUAL


def expected_group_training_start(
    training_date: datetime,
    sport_type: str,
    age_group: str,
) -> Optional[datetime]:
    """Начало групповой пары по TRAINING_SCHEDULE или None (в этот день недели слота нет)."""
    from utils.training_manager import TrainingManager

    schedule_map = TrainingManager.TRAINING_SCHEDULE.get(sport_type) or {}
    schedule = schedule_map.get(age_group)
    if not schedule:
        return None
    weekday = training_date.weekday()
    if weekday not in (schedule.get("days") or []):
        return None
    hour, minute = TrainingManager.get_hour_minute_for_weekday(schedule, weekday)
    return training_date.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _find_group_training_at_time(
    session: Session,
    *,
    sport_type: str,
    age_group: str,
    target_start: datetime,
    coach_id: Optional[int],
    exclude_id: Optional[int],
) -> Optional[Training]:
    day = target_start.date()
    day_start = datetime.combine(day, time(0, 0, 0))
    day_end = day_start + timedelta(days=1)
    q = session.query(Training).filter(
        Training.sport_type == sport_type,
        Training.age_group == age_group,
        Training.is_cancelled.is_(False),
        Training.training_date >= day_start,
        Training.training_date < day_end,
    )
    if coach_id is not None:
        q = q.filter(Training.coach_id == coach_id)
    if exclude_id is not None:
        q = q.filter(Training.id != exclude_id)
    for tr in q.order_by(Training.id.asc()).all():
        if not is_group_training(tr):
            continue
        if (
            tr.training_date.hour == target_start.hour
            and tr.training_date.minute == target_start.minute
        ):
            return tr
    return None


def children_group_at_wrong_schedule_time(training: Training) -> bool:
    """Групповая «Детская» не в слоте TRAINING_SCHEDULE для children."""
    if not is_group_training(training):
        return False
    age_group = normalize_age_group(training.age_group)
    if age_group != AGE_GROUP_CHILDREN:
        return False
    sport_type = (training.sport_type or "").strip()
    if not sport_type:
        return False
    expected_children = expected_group_training_start(
        training.training_date, sport_type, AGE_GROUP_CHILDREN
    )
    current = training.training_date.replace(second=0, microsecond=0)
    if expected_children is None:
        return True
    return current != expected_children


def inspect_misplaced_children_group_trainings(session: Session) -> List[Dict[str, Any]]:
    """Список групповых «Детская» не на своём времени (для проверки перед правкой)."""
    rows: List[Dict[str, Any]] = []
    for training in (
        session.query(Training)
        .filter(Training.is_cancelled.is_(False))
        .order_by(Training.training_date.asc(), Training.id.asc())
        .all()
    ):
        if not children_group_at_wrong_schedule_time(training):
            continue
        sport_type = (training.sport_type or "").strip()
        expected_middle = expected_group_training_start(
            training.training_date, sport_type, AGE_GROUP_MIDDLE
        )
        rows.append(
            {
                "id": training.id,
                "sport_type": sport_type,
                "coach_id": training.coach_id,
                "from": training.training_date.strftime("%Y-%m-%d %H:%M"),
                "to_age_group": AGE_GROUP_MIDDLE,
                "to_time": (
                    expected_middle.strftime("%Y-%m-%d %H:%M")
                    if expected_middle
                    else None
                ),
            }
        )
    return rows


def fix_misplaced_children_group_trainings(session: Session) -> Dict[str, Any]:
    """Переклассифицировать все групповые «Детская» не в своём слоте → «Средняя» (+ время)."""
    report: Dict[str, Any] = {"fixed": 0, "skipped": 0, "actions": []}
    ids = [row["id"] for row in inspect_misplaced_children_group_trainings(session)]
    for training_id in ids:
        training = session.query(Training).filter_by(id=training_id).first()
        if not training:
            continue
        before = (
            training.training_date.strftime("%Y-%m-%d %H:%M"),
            training.age_group,
        )
        sport_type = (training.sport_type or "").strip()
        expected_middle = expected_group_training_start(
            training.training_date, sport_type, AGE_GROUP_MIDDLE
        )
        if expected_middle is None:
            report["skipped"] += 1
            report["actions"].append(
                {"id": training_id, "action": "skip", "reason": "no_middle_slot_this_day"}
            )
            continue
        result = reconcile_group_training_to_schedule(
            session, training, expected_start=expected_middle
        )
        report["fixed"] += 1
        report["actions"].append(
            {
                "id": training_id,
                "result_id": result.id,
                "from": f"{before[0]} children",
                "to": f"{result.training_date.strftime('%Y-%m-%d %H:%M')} {result.age_group}",
            }
        )
    session.flush()
    return report


def _merge_attendances_to_training(
    session: Session,
    *,
    from_training_id: int,
    to_training_id: int,
) -> None:
    for att in session.query(Attendance).filter(Attendance.training_id == from_training_id).all():
        dup = (
            session.query(Attendance)
            .filter(
                Attendance.athlete_id == att.athlete_id,
                Attendance.training_id == to_training_id,
            )
            .first()
        )
        if dup:
            session.delete(att)
        else:
            att.training_id = to_training_id


def reconcile_group_training_to_schedule(
    session: Session,
    training: Training,
    *,
    expected_start: Optional[datetime] = None,
) -> Training:
    """
    Привести групповую запись к актуальному времени расписания.

    Если в целевом слоте уже есть пара — attendances переносятся, дубликат удаляется.
    """
    if not is_group_training(training):
        return training

    age_group = normalize_age_group(training.age_group) or training.age_group
    if age_group and age_group != training.age_group:
        training.age_group = age_group
    sport_type = (training.sport_type or "").strip()
    if not sport_type or not age_group:
        return training

    if children_group_at_wrong_schedule_time(training):
        expected_middle = expected_group_training_start(
            training.training_date, sport_type, AGE_GROUP_MIDDLE
        )
        if expected_middle is None:
            logger.warning(
                "Групповая children training_id=%s не в своём слоте, "
                "но средняя группа в этот день не scheduled — пропуск",
                training.id,
            )
            return training
        logger.info(
            "Групповая training_id=%s: Детская не в своём времени → Средняя %s",
            training.id,
            expected_middle.strftime("%Y-%m-%d %H:%M"),
        )
        training.age_group = AGE_GROUP_MIDDLE
        age_group = AGE_GROUP_MIDDLE
        expected_start = expected_middle

    expected = expected_start or expected_group_training_start(
        training.training_date, sport_type, age_group
    )
    if expected is None:
        return training

    current = training.training_date.replace(second=0, microsecond=0)
    if current == expected:
        return training

    existing = _find_group_training_at_time(
        session,
        sport_type=sport_type,
        age_group=age_group,
        target_start=expected,
        coach_id=training.coach_id,
        exclude_id=training.id,
    )
    if existing is not None:
        logger.info(
            "Групповая training_id=%s объединена с training_id=%s (%s %s → %s)",
            training.id,
            existing.id,
            sport_type,
            age_group,
            expected.strftime("%H:%M"),
        )
        _merge_attendances_to_training(
            session, from_training_id=training.id, to_training_id=existing.id
        )
        session.delete(training)
        session.flush()
        return existing

    logger.info(
        "Групповая training_id=%s перенесена на расписание: %s → %s",
        training.id,
        current.strftime("%Y-%m-%d %H:%M"),
        expected.strftime("%Y-%m-%d %H:%M"),
    )
    training.training_date = expected
    session.flush()
    return training


def find_group_training_on_calendar_day(
    session: Session,
    *,
    sport_type: str,
    age_group: str,
    day: date,
    coach_id: Optional[int] = None,
) -> Optional[Training]:
    """
    Групповая тренировка на календарный день по расписанию.

    Сначала ищем слот в актуальное время; legacy-запись в тот же день
    автоматически переносится на время из TRAINING_SCHEDULE.
    """
    age_group = normalize_age_group(age_group) or age_group
    sport_type = (sport_type or "").strip()
    if not sport_type or not age_group:
        return None

    anchor = datetime.combine(day, time(0, 0, 0))
    expected = expected_group_training_start(anchor, sport_type, age_group)
    if expected is None:
        return None

    exact = _find_group_training_at_time(
        session,
        sport_type=sport_type,
        age_group=age_group,
        target_start=expected,
        coach_id=coach_id,
        exclude_id=None,
    )
    if exact is not None:
        return exact

    day_start = datetime.combine(day, time(0, 0, 0))
    day_end = day_start + timedelta(days=1)
    q = (
        session.query(Training)
        .filter(
            Training.sport_type == sport_type,
            Training.age_group == age_group,
            Training.is_cancelled.is_(False),
            Training.training_date >= day_start,
            Training.training_date < day_end,
        )
        .order_by(Training.training_date.asc(), Training.id.asc())
    )
    if coach_id is not None:
        q = q.filter(Training.coach_id == coach_id)
    for legacy in q.all():
        if not is_group_training(legacy):
            continue
        leg_age = normalize_age_group(legacy.age_group) or legacy.age_group
        if leg_age != age_group:
            continue
        if children_group_at_wrong_schedule_time(legacy):
            reconcile_group_training_to_schedule(session, legacy)
            return None
        return reconcile_group_training_to_schedule(
            session, legacy, expected_start=expected
        )
    return None


def individual_slot_has_links(
    session: Session,
    training: Training,
    *,
    coach_id: Optional[int] = None,
) -> bool:
    """
    Есть ли у индивидуального слота реальные связи:
    - individual-подписка на этот старт (включая уже завершённые/неактивные);
    - или attendance в одном из training_id этого слота.
    """
    fmt = (getattr(training, "training_format", None) or "").strip().lower()
    if fmt != TRAINING_FORMAT_INDIVIDUAL:
        return True

    slot_ids = individual_slot_training_ids(session, training)
    slot_key = training.training_date.strftime("%Y-%m-%d %H:%M")

    sub_q = (
        session.query(Subscription.id)
        .join(Athlete, Subscription.athlete_id == Athlete.id)
        .filter(
            Subscription.subscription_type == "individual",
            Subscription.sport_type == training.sport_type,
            func.strftime("%Y-%m-%d %H:%M", Subscription.start_date) == slot_key,
        )
    )
    if coach_id is not None:
        sub_q = sub_q.filter(
            (Subscription.responsible_coach_id == coach_id) | (Athlete.created_by == coach_id)
        )
    if sub_q.first() is not None:
        return True

    att_q = (
        session.query(Attendance.id)
        .join(Athlete, Attendance.athlete_id == Athlete.id)
        .filter(Attendance.training_id.in_(slot_ids))
    )
    if coach_id is not None:
        att_q = att_q.filter(Athlete.created_by == coach_id)
    return att_q.first() is not None


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
    end_dt = end if end is not None else individual_training_end_time(start)
    day = start.date()

    if _overlaps_any_interval(start, end_dt, scheduled_group_training_intervals(day)):
        return True

    for t in coach_trainings_on_calendar_day(session, coach_id, day):
        if ignore_training_id is not None and t.id == ignore_training_id:
            continue
        t0 = t.training_date
        t1 = training_slot_end_time(t0, getattr(t, "training_format", None))
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
    Старты индивидуальной тренировки (1 ч) в любой день недели:
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
