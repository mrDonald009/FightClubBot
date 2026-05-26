"""Единая запись истории посещений (visit_history)."""
from datetime import datetime
from typing import Optional, Tuple

from sqlalchemy.orm import Session

from database.models import Attendance, VisitHistory
from utils.time_utils import now_moscow


def _default_status_label(status_code: str) -> str:
    if status_code == "present":
        return "✅ Был"
    return "❌ Не был"


def upsert_visit_history_for_training(
    session: Session,
    *,
    athlete_id: int,
    training_id: int,
    attendance: Optional[Attendance],
    status_code: Optional[str] = None,
    status_label: Optional[str] = None,
    source: str = "derived",
    recorded_at: Optional[datetime] = None,
) -> Tuple[VisitHistory, bool]:
    """
    Создать/обновить materialized visit_history для пары (athlete_id, training_id).

    Возвращает:
        (row, changed) где changed=True, если были изменения в БД.
    """
    now = recorded_at or now_moscow()
    resolved_status = status_code
    if not resolved_status:
        resolved_status = "present" if (attendance is not None and attendance.attended) else "absent"
    resolved_label = status_label or _default_status_label(resolved_status)

    subscription_id = attendance.subscription_id if attendance is not None else None
    attendance_id = attendance.id if attendance is not None else None

    row = (
        session.query(VisitHistory)
        .filter(
            VisitHistory.athlete_id == athlete_id,
            VisitHistory.training_id == training_id,
        )
        .first()
    )

    if row is None:
        row = VisitHistory(
            athlete_id=athlete_id,
            training_id=training_id,
            subscription_id=subscription_id,
            attendance_id=attendance_id,
            status_code=resolved_status,
            status_label=resolved_label,
            source=source,
            recorded_at=now,
        )
        session.add(row)
        return row, True

    changed = False
    if row.subscription_id != subscription_id:
        row.subscription_id = subscription_id
        changed = True
    if row.attendance_id != attendance_id:
        row.attendance_id = attendance_id
        changed = True
    if row.status_code != resolved_status:
        row.status_code = resolved_status
        changed = True
    if row.status_label != resolved_label:
        row.status_label = resolved_label
        changed = True
    if row.source != source:
        row.source = source
        changed = True
    if changed:
        row.updated_at = now
    return row, changed
