"""Отмена тренировки: UI и обёртки доменной логики (таблицы coach_absences)."""
import html
from datetime import datetime
from typing import List, Optional

from sqlalchemy.orm import Session

from database.db_utils import (
    apply_coach_absence,
    deactivate_coach_absence_and_migrate,
    list_active_coach_absences_overlapping_range,
)
from database.models import Coach, CoachAbsence
from utils.time_utils import now_moscow


def parse_ui_date(text: str) -> Optional[datetime]:
    try:
        return datetime.strptime(text.strip(), "%d.%m.%Y")
    except Exception:
        return None


def format_coach_absence_status_html(session: Session, coach_id: int) -> str:
    """Действующие периоды отмены тренировок тренера."""
    now = now_moscow()
    rows = (
        session.query(CoachAbsence)
        .filter(
            CoachAbsence.coach_id == coach_id,
            CoachAbsence.is_active == True,
            CoachAbsence.start_date <= now,
            CoachAbsence.end_date >= now,
        )
        .order_by(CoachAbsence.id.asc())
        .all()
    )
    if not rows:
        return "📭 <b>Сейчас нет действующей отмены тренировок.</b>"
    lines = ["📌 <b>Действует отмена тренировок:</b>"]
    for ca in rows:
        title = html.escape((ca.title or "").strip() or "без названия")
        ds = ca.start_date.strftime("%d.%m.%Y")
        de = ca.end_date.strftime("%d.%m.%Y")
        lines.append(f"• ID <code>{ca.id}</code> — <b>{title}</b>")
        lines.append(f"  <i>{ds} — {de}</i>")
    return "\n".join(lines)


def list_active_coach_absences(session: Session, coach_id: int) -> List[CoachAbsence]:
    return (
        session.query(CoachAbsence)
        .filter(CoachAbsence.coach_id == coach_id, CoachAbsence.is_active == True)
        .order_by(CoachAbsence.start_date.asc())
        .all()
    )


def list_active_coaches(session: Session) -> List[Coach]:
    return (
        session.query(Coach)
        .filter(Coach.is_active == True)
        .order_by(Coach.first_name.asc())
        .all()
    )


def format_coach_absence_history_html(session: Session, coach_id: int, limit: int = 10) -> str:
    rows = (
        session.query(CoachAbsence)
        .filter(CoachAbsence.coach_id == coach_id)
        .order_by(CoachAbsence.id.desc())
        .limit(limit)
        .all()
    )
    if not rows:
        return "📭 <b>История отмен тренировок пуста.</b>"
    lines = [f"📚 <b>История отмен тренировок</b> (последние {len(rows)}):"]
    for ca in rows:
        status = "🟢 Действует" if ca.is_active else "⚪ Отключено"
        title = html.escape((ca.title or "").strip() or "без названия")
        ds = ca.start_date.strftime("%d.%m.%Y")
        de = ca.end_date.strftime("%d.%m.%Y")
        lines.append(f"• <b>{title}</b> — {status}")
        lines.append(f"  <i>{ds} — {de}</i>")
    return "\n".join(lines)


def ca_button_label(ca_id: int, title: str) -> str:
    text = (title or "").strip() or "без названия"
    if len(text) > 28:
        text = text[:25] + "…"
    return f"#{ca_id} {text}"


def overlapping_coach_absence_ids(
    session: Session, coach_id: int, start_date: datetime, end_date: datetime, max_ids: int = 5
) -> Optional[str]:
    overlapping = list_active_coach_absences_overlapping_range(
        session, coach_id, start_date, end_date
    )
    if not overlapping:
        return None
    ids = ", ".join(str(ca.id) for ca in overlapping[:max_ids])
    if len(overlapping) > max_ids:
        ids += ", …"
    return ids


def apply_coach_absence_service(
    session: Session,
    coach_id: int,
    start_date: datetime,
    end_date: datetime,
    title: str,
    created_by: int,
) -> dict:
    return apply_coach_absence(
        session=session,
        coach_id=coach_id,
        start_date=start_date,
        end_date=end_date,
        title=title,
        created_by=created_by,
    )


def deactivate_coach_absence_service(session: Session, ca_id: int) -> dict:
    return deactivate_coach_absence_and_migrate(session, ca_id)
