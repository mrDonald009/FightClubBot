"""Единые подписи и иконки посещения: Был / Не был / Не отмечено (только явная отметка)."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session as OrmSession

from database.models import Attendance, Training
from utils.time_utils import (
    now_moscow,
)

def attendance_icon_for_slot(
    attendance: Optional[Attendance],
    training_start: datetime,
    *,
    now: Optional[datetime] = None,
    training_format: Optional[str] = None,
) -> str:
    """✅ был · ❌ не был · ⏳ нет явной отметки."""
    now = now or now_moscow()
    if attendance is not None:
        return "✅" if attendance.attended else "❌"
    return "⏳"


def attendance_label_ru_for_slot(
    attendance: Optional[Attendance],
    training_start: datetime,
    *,
    now: Optional[datetime] = None,
    with_note: bool = False,
    training_format: Optional[str] = None,
) -> str:
    """
    Короткая подпись для списков.
    with_note параметр сохранен для обратной совместимости интерфейсов.
    """
    now = now or now_moscow()
    if attendance is not None:
        return "Был" if attendance.attended else "Не был"
    return "Не отмечено"


def attendance_icon_for_training(
    attendance: Optional[Attendance],
    training: Training,
    *,
    now: Optional[datetime] = None,
) -> str:
    return attendance_icon_for_slot(
        attendance,
        training.training_date,
        now=now,
        training_format=getattr(training, "training_format", None),
    )


def attendance_label_ru_for_training(
    attendance: Optional[Attendance],
    training: Training,
    *,
    now: Optional[datetime] = None,
    with_note: bool = False,
) -> str:
    return attendance_label_ru_for_slot(
        attendance,
        training.training_date,
        now=now,
        with_note=with_note,
        training_format=getattr(training, "training_format", None),
    )


def is_effective_absent_no_row(
    attendance: Optional[Attendance],
    training_start: datetime,
    *,
    now: Optional[datetime] = None,
) -> bool:
    """Отсутствие фиксируется только явной отметкой attended=False."""
    if attendance is not None:
        return not attendance.attended
    return False


def is_effective_present(attendance: Optional[Attendance]) -> bool:
    return attendance is not None and bool(attendance.attended)


def is_pending_unmarked(
    attendance: Optional[Attendance],
    training_start: datetime,
    *,
    now: Optional[datetime] = None,
) -> bool:
    """Слот без записи всегда считается «не отмечено»."""
    if attendance is not None:
        return False
    return True


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
    """Имплицитные «не был» отключены: считаем только явные Attendance."""
    return 0
