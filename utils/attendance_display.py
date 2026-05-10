"""Единые подписи и иконки посещения: Был / Не был / Не отмечено (до 24 ч после конца пары)."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session as OrmSession

from database.models import Attendance, Training
from utils.time_utils import (
    ATTENDANCE_UNMARKED_TO_ABSENT_AFTER_TRAINING_END,
    now_moscow,
    training_end_time,
)

def attendance_icon_for_slot(
    attendance: Optional[Attendance],
    training_start: datetime,
    *,
    now: Optional[datetime] = None,
) -> str:
    """✅ был · ❌ не был · ⏳ ещё можно считать «не отмечено»."""
    now = now or now_moscow()
    if attendance is not None:
        return "✅" if attendance.attended else "❌"
    end = training_end_time(training_start)
    if now > end + ATTENDANCE_UNMARKED_TO_ABSENT_AFTER_TRAINING_END:
        return "❌"
    return "⏳"


def attendance_label_ru_for_slot(
    attendance: Optional[Attendance],
    training_start: datetime,
    *,
    now: Optional[datetime] = None,
    with_note: bool = False,
) -> str:
    """
    Короткая подпись для списков.
    with_note=True — добавить пояснение для случая «не был без записи в срок».
    """
    now = now or now_moscow()
    if attendance is not None:
        return "Был" if attendance.attended else "Не был"
    end = training_end_time(training_start)
    if now > end + ATTENDANCE_UNMARKED_TO_ABSENT_AFTER_TRAINING_END:
        if with_note:
            return "Не был <i>(нет записи в срок)</i>"
        return "Не был"
    return "Не отмечено"


def attendance_icon_for_training(
    attendance: Optional[Attendance],
    training: Training,
    *,
    now: Optional[datetime] = None,
) -> str:
    return attendance_icon_for_slot(attendance, training.training_date, now=now)


def attendance_label_ru_for_training(
    attendance: Optional[Attendance],
    training: Training,
    *,
    now: Optional[datetime] = None,
    with_note: bool = False,
) -> str:
    return attendance_label_ru_for_slot(
        attendance, training.training_date, now=now, with_note=with_note
    )


def is_effective_absent_no_row(
    attendance: Optional[Attendance],
    training_start: datetime,
    *,
    now: Optional[datetime] = None,
) -> bool:
    """Нет строки attendances, но дедлайн «не отмечено» прошёл → считаем отсутствием."""
    if attendance is not None:
        return not attendance.attended
    now = now or now_moscow()
    end = training_end_time(training_start)
    return now > end + ATTENDANCE_UNMARKED_TO_ABSENT_AFTER_TRAINING_END


def is_effective_present(attendance: Optional[Attendance]) -> bool:
    return attendance is not None and bool(attendance.attended)


def is_pending_unmarked(
    attendance: Optional[Attendance],
    training_start: datetime,
    *,
    now: Optional[datetime] = None,
) -> bool:
    """Слот ещё в окне «не отмечено»."""
    if attendance is not None:
        return False
    now = now or now_moscow()
    end = training_end_time(training_start)
    return now <= end + ATTENDANCE_UNMARKED_TO_ABSENT_AFTER_TRAINING_END


def count_implicit_absent_slots(
    session: OrmSession,
    athlete_id: int,
    sport_type: str,
    age_group: str,
    period_start: datetime,
    period_end: datetime,
    *,
    now: Optional[datetime] = None,
) -> int:
    """
    Слоты Training за период без строки Attendance у спортсмена,
    если уже прошло 24 ч после окончания пары (считаем «не был»).
    """
    now = now or now_moscow()
    past_slots = (
        session.query(Training)
        .filter(
            Training.sport_type == sport_type,
            Training.age_group == age_group,
            Training.training_date >= period_start,
            Training.training_date <= period_end,
            Training.is_cancelled == False,
        )
        .all()
    )
    covered_tids = {
        row[0]
        for row in session.query(Attendance.training_id)
        .join(Training, Attendance.training_id == Training.id)
        .filter(
            Attendance.athlete_id == athlete_id,
            Training.training_date >= period_start,
            Training.training_date <= period_end,
        )
        .distinct()
        .all()
        if row[0] is not None
    }
    implicit = 0
    for t in past_slots:
        if t.id in covered_tids:
            continue
        if now > training_end_time(t.training_date) + ATTENDANCE_UNMARKED_TO_ABSENT_AFTER_TRAINING_END:
            implicit += 1
    return implicit
