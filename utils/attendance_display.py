"""Единые подписи и иконки посещения: только «Был» и «Не был»."""
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
    """✅ был · ❌ не был."""
    now = now or now_moscow()
    if attendance is not None and attendance.attended:
        return "✅"
    return "❌"


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
    if attendance is not None and attendance.attended:
        return "Был"
    return "Не был"


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
    """В модели отображения отсутствие без явной записи трактуем как «не был»."""
    if attendance is not None:
        return not attendance.attended
    return True


def is_effective_present(attendance: Optional[Attendance]) -> bool:
    return attendance is not None and bool(attendance.attended)


def is_pending_unmarked(
    attendance: Optional[Attendance],
    training_start: datetime,
    *,
    now: Optional[datetime] = None,
) -> bool:
    """Статус «не отмечено» не используется."""
    return False


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
