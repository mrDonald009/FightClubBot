"""Единый формат строк тренировочного слота: «ЧЧ:ММ - вид | Групповая — …» / «| Индивидуальная»."""
from __future__ import annotations

import html
from datetime import datetime
from typing import Optional

from database.db_utils.training_slots import TRAINING_FORMAT_INDIVIDUAL
from utils.age_groups import format_age_group_label

# «08:00 - MMA», «22.05.2026 09:00 - MMA»
SEP_TIME_SPORT = " - "
# «MMA | Детская», «MMA | Индивидуальная»
SEP_SPORT_TAIL = " | "
# «Групповая — Детская», «Петров Е.П. — Взрослая»
SEP_AGE_FORMAT = " — "


def format_training_slot_body(
    sport_type: str,
    *,
    age_group: Optional[str] = None,
    is_individual: bool = False,
    escape_html: bool = False,
) -> str:
    """Тело слота без времени: «MMA | Индивидуальная» или «MMA | Групповая — Детская»."""
    sport = html.escape(sport_type) if escape_html else sport_type
    if is_individual:
        return f"{sport}{SEP_SPORT_TAIL}Индивидуальная"
    age = format_age_group_label(age_group, short=True)
    return f"{sport}{SEP_SPORT_TAIL}Групповая{SEP_AGE_FORMAT}{age}"


def format_training_slot_line(
    training_datetime: datetime,
    sport_type: str,
    *,
    age_group: Optional[str] = None,
    is_individual: bool = False,
    time_only: bool = False,
    escape_html: bool = False,
) -> str:
    """Полная строка слота: «ДД.ММ.ГГГГ ЧЧ:ММ - …» или «ЧЧ:ММ - …»."""
    if time_only:
        left = training_datetime.strftime("%H:%M")
    else:
        left = training_datetime.strftime("%d.%m.%Y %H:%M")
    body = format_training_slot_body(
        sport_type,
        age_group=age_group,
        is_individual=is_individual,
        escape_html=escape_html,
    )
    return f"{left}{SEP_TIME_SPORT}{body}"


def format_attendance_step2_slot_title(training) -> str:
    """Заголовок шага 2 «Отметить посещения» (HTML)."""
    is_individual = (
        (getattr(training, "training_format", None) or "").strip().lower()
        == TRAINING_FORMAT_INDIVIDUAL
    )
    line = format_training_slot_line(
        training.training_date,
        training.sport_type,
        age_group=getattr(training, "age_group", None),
        is_individual=is_individual,
        escape_html=True,
    )
    return f"{line}\n\n"


def format_coach_calendar_slot_bullet(time_str: str, sport_type: str, *, age_group, is_individual: bool) -> str:
    """Строка «• 08:00 - MMA | …» для дня календаря."""
    body = format_training_slot_body(
        sport_type, age_group=age_group, is_individual=is_individual
    )
    return f"• <b>{time_str}</b>{SEP_TIME_SPORT}{body}\n"


def format_athlete_age_suffix(age_group_code: Optional[str]) -> str:
    """Подпись возраста у спортсмена в календаре: « — Взрослая»."""
    athlete_age_ru = format_age_group_label(age_group_code, short=True)
    if not athlete_age_ru:
        return ""
    return f"{SEP_AGE_FORMAT}{athlete_age_ru}"
