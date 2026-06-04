"""Отмена тренировки: календарь, слоты дня, применение отмены группового занятия."""
from __future__ import annotations

import calendar as cal_mod
import html
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session, joinedload

from database.db_utils.group_training_cancellation import (
    apply_group_training_cancellation,
    list_cancelled_group_trainings,
    list_rollbackable_cancelled_group_trainings,
    revert_group_training_cancellation,
)
from database.models import Coach, Training
from services.attendance_training_flow import TodaySlotDisplay, build_attendance_slots_for_day
from utils.time_utils import now_moscow
from utils.training_slot_display import format_training_slot_line

from database.db_utils.training_slots import TRAINING_FORMAT_INDIVIDUAL


def format_unified_cancellation_menu_status_html(session: Session, coach_id: int) -> str:
    """Статус: активный период + недавние точечные отмены."""
    from services.coach_absence_service import format_coach_absence_status_html

    parts = [format_coach_absence_status_html(session, coach_id)]
    slot_block = format_cancellation_menu_status_html(session, coach_id)
    if "Нет недавних" not in slot_block:
        parts.append(slot_block)
    return "\n\n".join(parts)


def format_unified_cancellation_history_html(session: Session, coach_id: int) -> str:
    from services.coach_absence_service import format_coach_absence_history_html

    period = format_coach_absence_history_html(session, coach_id, limit=8)
    slots = format_cancellation_history_html(session, coach_id, limit=8)
    period_empty = "пуста" in period
    slots_empty = "пуста" in slots
    if period_empty and slots_empty:
        return "📭 <b>Архив отмен пуст.</b>"
    lines = ["📜 <b>Архив отмен</b>"]
    if not period_empty:
        lines.append("")
        lines.append("📅 <b>Периоды (праздники):</b>")
        for row in period.split("\n")[1:]:
            if row.strip():
                lines.append(row)
    if not slots_empty:
        lines.append("")
        lines.append("⏰ <b>Отдельные занятия:</b>")
        for row in slots.split("\n")[1:]:
            if row.strip():
                lines.append(row)
    return "\n".join(lines)


def format_cancellation_menu_status_html(session: Session, coach_id: int) -> str:
    """Ближайшие отменённые групповые занятия."""
    now = now_moscow()
    rows = (
        session.query(Training)
        .filter(
            Training.coach_id == coach_id,
            Training.is_cancelled == True,
            Training.training_date >= now - timedelta(days=1),
        )
        .order_by(Training.training_date.asc())
        .limit(5)
        .all()
    )
    group_rows = [
        t
        for t in rows
        if (getattr(t, "training_format", None) or "").strip().lower()
        != TRAINING_FORMAT_INDIVIDUAL
    ]
    if not group_rows:
        return "📭 <b>Нет недавних отменённых групповых занятий.</b>"
    lines = ["📌 <b>Отменённые групповые занятия:</b>"]
    for t in group_rows:
        line = format_training_slot_line(
            t.training_date,
            t.sport_type,
            age_group=t.age_group,
            is_individual=False,
            escape_html=True,
        )
        lines.append(f"• {line}")
    return "\n".join(lines)


def format_cancellation_history_html(session: Session, coach_id: int, limit: int = 15) -> str:
    rows = list_cancelled_group_trainings(session, coach_id, limit=limit)
    group_rows = [
        t
        for t in rows
        if (getattr(t, "training_format", None) or "").strip().lower()
        != TRAINING_FORMAT_INDIVIDUAL
    ]
    if not group_rows:
        return "📭 <b>Архив отмен групповых занятий пуст.</b>"
    lines = [f"📜 <b>Архив</b> (последние {len(group_rows)}):"]
    for t in group_rows:
        line = format_training_slot_line(
            t.training_date,
            t.sport_type,
            age_group=t.age_group,
            is_individual=False,
            escape_html=True,
        )
        lines.append(f"• {line}")
    return "\n".join(lines)


def group_slots_for_cancellation_day(
    session: Session,
    user: Coach,
    day: date,
) -> Tuple[List[TodaySlotDisplay], Dict[str, Dict[str, Any]]]:
    """Групповые слоты на день (без индивидуальных)."""
    rows, virtual = build_attendance_slots_for_day(session, user, day)
    group_rows = [r for r in rows if not r.is_individual_format]
    group_virtual = {}
    for r in group_rows:
        if r.is_virtual and r.virtual_token and r.virtual_token in virtual:
            group_virtual[r.virtual_token] = virtual[r.virtual_token]
    return group_rows, group_virtual


def build_cancellation_day_calendar(
    year: int,
    month: int,
    *,
    today: Optional[date] = None,
) -> Tuple[str, Any]:
    """Inline-календарь выбора дня (callback ca_cal_Y_M / ca_day_Y_M_D)."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    today = today or now_moscow().date()
    header = (
        f"📅 <b>Выберите день</b> отмены группового занятия\n"
        f"<i>{_month_title(year, month)}</i>"
    )
    keyboard = []
    day_names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    keyboard.append(
        [InlineKeyboardButton(f"{n}.", callback_data="ca_cal_empty") for n in day_names]
    )
    weeks = list(cal_mod.monthcalendar(year, month))
    while len(weeks) < 5:
        weeks.append([0, 0, 0, 0, 0, 0, 0])
    for week in weeks:
        row = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(" ", callback_data="ca_cal_empty"))
            else:
                d = date(year, month, day)
                label = f"{day}•" if d == today else str(day)
                row.append(
                    InlineKeyboardButton(
                        label,
                        callback_data=f"ca_day_{year}_{month}_{day}",
                    )
                )
        keyboard.append(row)
    prev_m, prev_y = (month - 1, year) if month > 1 else (12, year - 1)
    next_m, next_y = (month + 1, year) if month < 12 else (1, year + 1)
    keyboard.append(
        [
            InlineKeyboardButton("◀️", callback_data=f"ca_cal_{prev_y}_{prev_m}"),
            InlineKeyboardButton("Сегодня", callback_data=f"ca_cal_{today.year}_{today.month}"),
            InlineKeyboardButton("▶️", callback_data=f"ca_cal_{next_y}_{next_m}"),
        ]
    )
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="ca_back_menu")])
    return header, InlineKeyboardMarkup(keyboard)


def _month_title(year: int, month: int) -> str:
    names = [
        "",
        "Январь",
        "Февраль",
        "Март",
        "Апрель",
        "Май",
        "Июнь",
        "Июль",
        "Август",
        "Сентябрь",
        "Октябрь",
        "Ноябрь",
        "Декабрь",
    ]
    return f"{names[month]} {year}"


def slot_callback_data(slot: TodaySlotDisplay) -> str:
    if slot.training_id:
        return f"ca_slot_tid_{slot.training_id}"
    return f"ca_slot_v_{slot.virtual_token}"


def parse_slot_callback(data: str) -> Tuple[Optional[str], Optional[int]]:
    if data.startswith("ca_slot_tid_"):
        try:
            return "tid", int(data.replace("ca_slot_tid_", ""))
        except ValueError:
            return None, None
    if data.startswith("ca_slot_v_"):
        return "v", data.replace("ca_slot_v_", "")
    return None, None


def resolve_slot_from_callback(
    session: Session,
    coach: Coach,
    kind: str,
    value,
    virtual_slots: Dict[str, Dict[str, Any]],
    pick_day: date,
) -> Tuple[Optional[TodaySlotDisplay], Optional[str]]:
    """Вернуть отображение слота и текст ошибки."""
    if kind == "tid":
        t = session.query(Training).filter_by(id=value, coach_id=coach.id).first()
        if not t or t.is_cancelled:
            return None, "❌ Занятие не найдено или уже отменено."
        if (getattr(t, "training_format", None) or "").strip().lower() == TRAINING_FORMAT_INDIVIDUAL:
            return None, "❌ Это индивидуальное занятие. Выберите групповое."
        return (
            TodaySlotDisplay(
                is_virtual=False,
                training_id=t.id,
                virtual_token=None,
                sport_type=t.sport_type,
                age_group=t.age_group,
                training_datetime=t.training_date,
                is_individual_format=False,
            ),
            None,
        )
    if kind == "v":
        meta = virtual_slots.get(value)
        if not meta:
            return None, "❌ Список устарел. Выберите день заново."
        dt = datetime(
            pick_day.year,
            pick_day.month,
            pick_day.day,
            meta.get("hour", 0),
            meta.get("minute", 0),
        )
        return (
            TodaySlotDisplay(
                is_virtual=True,
                training_id=None,
                virtual_token=value,
                sport_type=meta.get("sport_type") or "",
                age_group=meta.get("age_group") or "",
                training_datetime=dt,
                is_individual_format=False,
            ),
            None,
        )
    return None, "❌ Некорректный слот."


def apply_slot_cancellation_service(
    session: Session,
    coach_id: int,
    slot: TodaySlotDisplay,
    created_by: int,
) -> dict:
    return apply_group_training_cancellation(
        session,
        coach_id,
        slot.sport_type,
        slot.age_group,
        slot.training_datetime,
        created_by=created_by,
    )


def slot_rollback_button_label(training: Training) -> str:
    line = format_training_slot_line(
        training.training_date,
        training.sport_type,
        age_group=training.age_group,
        is_individual=False,
        escape_html=False,
    )
    short = line.replace("\n", " ")[:48]
    return f"#{training.id} {short}"


def list_rollbackable_slots_service(session: Session, coach_id: int):
    return list_rollbackable_cancelled_group_trainings(session, coach_id)


def revert_slot_cancellation_service(
    session: Session, training_id: int, coach_id: int
) -> dict:
    return revert_group_training_cancellation(
        session, training_id, coach_id=coach_id
    )


def coach_user_for_slots(session: Session, coach_id: int) -> Optional[Coach]:
    return (
        session.query(Coach)
        .options(joinedload(Coach.sport_type_rel))
        .filter_by(id=coach_id)
        .first()
    )
