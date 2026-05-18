"""
Поток «Отметить посещения»: тренировки на сегодня (шаг 1), список спортсменов и отметки (шаг 2).
"""
from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple, Union

from sqlalchemy import func, or_
from sqlalchemy.orm import Session as OrmSession

from database.db_utils import get_user_role
from database.db_utils.training_slots import (
    TRAINING_FORMAT_INDIVIDUAL,
    dedupe_individual_trainings_by_slot,
    find_group_training_on_calendar_day,
    individual_slot_training_ids,
)
from database.models import Admin, Athlete, Attendance, Coach, Subscription, Training
from utils.age_groups import AGE_GROUP_CODES, format_age_group_label
from utils.coach_sport import sport_type_label_from_user
from utils.training_manager import TrainingManager
from utils.time_utils import now_moscow, training_end_time

# Размер страницы списка спортсменов на шаге 2 (inline-кнопки Telegram)
ATTENDANCE_LIST_PAGE_SIZE = 20


def format_today_trainings_count_ru(count: int) -> str:
    """Склонение «N тренировка/тренировки/тренировок» для текста тренеру."""
    n = abs(int(count))
    if n % 10 == 1 and n % 100 != 11:
        word = "тренировка"
    elif n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        word = "тренировки"
    else:
        word = "тренировок"
    return f"{n} {word}"


def is_training_in_live_attendance_window(training: Training, now: Optional[datetime] = None) -> bool:
    """Идёт ли сейчас эта пара (можно открыть шаг отметки): [начало, конец] включительно."""
    t_now = now if now is not None else now_moscow()
    start = training.training_date
    end = training_end_time(start)
    return start <= t_now <= end


@dataclass(frozen=True)
class TodaySlotDisplay:
    """Один ряд в списке тренировок на сегодня (шаг 1)."""

    is_virtual: bool
    training_id: Optional[int]
    virtual_token: Optional[str]
    sport_type: str
    age_group: str
    training_datetime: datetime
    is_individual_format: bool = False


def coach_training_access_error(user: Any, training: Training) -> Optional[str]:
    """
    Для тренера: тренировка должна быть его и по его виду спорта.
    Для админа: без ограничений.
    """
    if not user or get_user_role(user) != "coach":
        return None
    if training.coach_id != user.id:
        return "❌ Здесь только ваши тренировки. Выберите занятие, где вы указаны тренером."
    coach_sport = sport_type_label_from_user(user)
    if coach_sport and training.sport_type != coach_sport:
        return f"❌ Это занятие по другому виду спорта ({training.sport_type}). Отметки — по вашему направлению."
    return None


def build_today_attendance_slots(
    session: OrmSession,
    user: Union[Coach, Admin],
    now: datetime,
) -> Tuple[List[TodaySlotDisplay], Dict[str, Dict[str, Any]]]:
    """
    Тренировки на сегодня для шага 1: из БД + строки по расписанию, если записи ещё нет.
    Возвращает отсортированный список для UI и словарь токенов для callback виртуальных строк.
    """
    today_start = datetime(now.year, now.month, now.day)
    today_end = today_start + timedelta(days=1)
    weekday = now.weekday()
    virtual_slots: Dict[str, Dict[str, Any]] = {}
    rows: List[TodaySlotDisplay] = []

    def add_virtual(
        sport_type_name: str,
        age_group: str,
        hour: int,
        minute: int,
        coach_id: Optional[int],
    ) -> None:
        nonlocal virtual_slots
        token = f"v{len(virtual_slots)}"
        virtual_slots[token] = {
            "sport_type": sport_type_name,
            "age_group": age_group,
            "hour": hour,
            "minute": minute,
            "coach_id": coach_id,
        }
        dt = today_start.replace(hour=hour, minute=minute, second=0, microsecond=0)
        rows.append(
            TodaySlotDisplay(
                is_virtual=True,
                training_id=None,
                virtual_token=token,
                sport_type=sport_type_name,
                age_group=age_group,
                training_datetime=dt,
            )
        )

    if isinstance(user, Admin):
        db_trainings = (
            session.query(Training)
            .filter(
                Training.training_date >= today_start,
                Training.training_date < today_end,
                Training.is_cancelled == False,
            )
            .order_by(Training.training_date.asc())
            .all()
        )
        db_trainings = dedupe_individual_trainings_by_slot(db_trainings)
        existing_keys = {
            (t.sport_type, t.age_group, t.training_date.hour, t.training_date.minute)
            for t in db_trainings
        }
        for t in db_trainings:
            rows.append(
                TodaySlotDisplay(
                    is_virtual=False,
                    training_id=t.id,
                    virtual_token=None,
                    sport_type=t.sport_type,
                    age_group=t.age_group,
                    training_datetime=t.training_date,
                    is_individual_format=(
                        (getattr(t, "training_format", None) or "").strip().lower()
                        == TRAINING_FORMAT_INDIVIDUAL
                    ),
                )
            )
        for sport_type_name, schedule_map in TrainingManager.TRAINING_SCHEDULE.items():
            for age_group in AGE_GROUP_CODES:
                schedule = schedule_map.get(age_group)
                if not schedule or weekday not in schedule.get("days", []):
                    continue
                hour, minute = TrainingManager.get_hour_minute_for_weekday(schedule, weekday)
                key = (sport_type_name, age_group, hour, minute)
                if key in existing_keys:
                    continue
                add_virtual(sport_type_name, age_group, hour, minute, coach_id=None)
    else:
        trainings_query = session.query(Training).filter(
            Training.training_date >= today_start,
            Training.training_date < today_end,
            Training.is_cancelled == False,
            Training.coach_id == user.id,
        )
        sport_type_name = None
        if isinstance(user, Coach):
            if user.sport_type_rel:
                sport_type_name = user.sport_type_rel.name
            elif user.sport_type:
                sport_type_name = user.sport_type
        if sport_type_name:
            trainings_query = trainings_query.filter(Training.sport_type == sport_type_name)
        db_trainings = trainings_query.order_by(Training.training_date.asc()).all()
        db_trainings = dedupe_individual_trainings_by_slot(db_trainings)

        if not sport_type_name and db_trainings:
            sport_type_name = db_trainings[0].sport_type

        for t in db_trainings:
            rows.append(
                TodaySlotDisplay(
                    is_virtual=False,
                    training_id=t.id,
                    virtual_token=None,
                    sport_type=t.sport_type,
                    age_group=t.age_group,
                    training_datetime=t.training_date,
                    is_individual_format=(
                        (getattr(t, "training_format", None) or "").strip().lower()
                        == TRAINING_FORMAT_INDIVIDUAL
                    ),
                )
            )

        schedule_map = TrainingManager.TRAINING_SCHEDULE.get(sport_type_name, {}) if sport_type_name else {}
        existing_keys = {
            (t.sport_type, t.age_group, t.training_date.hour, t.training_date.minute)
            for t in db_trainings
        }
        for age_group in ("children", "adults"):
            schedule = schedule_map.get(age_group)
            if not schedule or weekday not in schedule.get("days", []):
                continue
            hour, minute = TrainingManager.get_hour_minute_for_weekday(schedule, weekday)
            key = (sport_type_name, age_group, hour, minute)
            if key in existing_keys:
                continue
            add_virtual(sport_type_name, age_group, hour, minute, coach_id=user.id)

    rows.sort(key=lambda r: r.training_datetime)
    return rows, virtual_slots


def resolve_training_from_attendance_callback(
    session: OrmSession,
    callback_data: str,
    virtual_slots: Dict[str, Dict[str, Any]],
) -> Tuple[Optional[Training], bool, Optional[str]]:
    """
    По callback шага 1 вернуть Training, флаг «создали новую запись», либо текст ошибки.
    """
    data = callback_data or ""
    if data.startswith("select_mark_training_virtual_"):
        token = data.replace("select_mark_training_virtual_", "")
        slot = virtual_slots.get(token)
        if not slot:
            return None, False, (
                "❌ Список устарел.\n\n"
                "Нажмите «🔄 Обновить» на этом экране или снова «📝 Отметить посещения» в меню."
            )
        sport_type = slot.get("sport_type")
        age_group = slot.get("age_group")
        hour = slot.get("hour")
        minute = slot.get("minute")
        coach_id = slot.get("coach_id")
        if (
            not sport_type
            or age_group not in AGE_GROUP_CODES
            or not isinstance(hour, int)
            or not isinstance(minute, int)
        ):
            return None, False, "❌ Не удалось открыть тренировку. Попробуйте «🔄 Обновить» или напишите администратору."
        today = now_moscow()
        training_dt = today.replace(hour=hour, minute=minute, second=0, microsecond=0)
        q = session.query(Training).filter_by(
            sport_type=sport_type,
            age_group=age_group,
            training_date=training_dt,
            is_cancelled=False,
        )
        if coach_id is not None:
            q = q.filter_by(coach_id=coach_id)
        training = q.first()
        if training:
            return training, False, None
        training = find_group_training_on_calendar_day(
            session,
            sport_type=sport_type,
            age_group=age_group,
            day=training_dt.date(),
            coach_id=coach_id,
        )
        if training:
            return training, False, None
        payload: Dict[str, Any] = dict(
            sport_type=sport_type,
            age_group=age_group,
            training_date=training_dt,
            is_cancelled=False,
        )
        if coach_id is not None:
            payload["coach_id"] = coach_id
        training = Training(**payload)
        session.add(training)
        session.flush()
        return training, True, None

    if not data.startswith("select_mark_training_"):
        return None, False, "❌ Запрос не распознан. Откройте «📝 Отметить посещения» заново."
    try:
        training_id = int(data.replace("select_mark_training_", ""))
    except ValueError:
        return None, False, "❌ Запрос не распознан. Откройте «📝 Отметить посещения» заново."
    training = session.query(Training).filter_by(id=training_id, is_cancelled=False).first()
    if not training:
        return None, False, "❌ Такой тренировки нет или она отменена. Обновите список кнопкой «🔄 Обновить»."
    return training, False, None


def fetch_athletes_for_training_slot(
    session: OrmSession, training: Training
) -> Tuple[List[Athlete], Dict[int, Attendance]]:
    training_day = training.training_date.date()
    is_individual_slot = (
        (getattr(training, "training_format", None) or "").strip().lower()
        == TRAINING_FORMAT_INDIVIDUAL
    )
    athletes_query = (
        session.query(Athlete)
        .join(Subscription, Subscription.athlete_id == Athlete.id)
        .filter(
            Subscription.is_active == True,
            Subscription.sport_type == training.sport_type,
            func.date(Subscription.start_date) <= training_day,
            func.date(Subscription.end_date) >= training_day,
        )
    )
    # Как в «Мой календарь»: индивидуальный слот — только абонемент individual с тем же началом;
    # групповой — без individual (иначе monthly попадает на все слоты дня).
    if is_individual_slot:
        slot_key = training.training_date.strftime("%Y-%m-%d %H:%M")
        athletes_query = athletes_query.filter(
            Subscription.subscription_type == "individual",
            func.strftime("%Y-%m-%d %H:%M", Subscription.start_date) == slot_key,
        )
    else:
        athletes_query = athletes_query.filter(
            Athlete.age_group == training.age_group,
            or_(
                Subscription.subscription_type.is_(None),
                Subscription.subscription_type != "individual",
            ),
        )
    athletes_query = athletes_query.order_by(Athlete.full_name.asc())
    athletes = athletes_query.all()
    athlete_ids = [a.id for a in athletes]
    attendance_map: Dict[int, Attendance] = {}
    if athlete_ids:
        slot_tids = individual_slot_training_ids(session, training)
        existing = (
            session.query(Attendance)
            .filter(
                Attendance.training_id.in_(slot_tids),
                Attendance.athlete_id.in_(athlete_ids),
            )
            .all()
        )
        attendance_map = {a.athlete_id: a for a in existing}
    return athletes, attendance_map


def _surname_initials_button_label(full_name: str, max_len: int = 40) -> str:
    """Фамилия и инициалы (как в списках): «Иванов И.П.» — компактно для кнопки."""
    parts = [p for p in (full_name or "").strip().split() if p]
    if not parts:
        return "—"
    if len(parts) == 1:
        s = parts[0]
    elif len(parts) == 2:
        sur, first = parts[0], parts[1]
        ini = f"{first[0].upper()}." if first else ""
        s = f"{sur} {ini}".strip()
    else:
        sur, first, pat = parts[0], parts[1], parts[2]
        i1 = f"{first[0].upper()}." if first else ""
        i2 = f"{pat[0].upper()}." if pat else ""
        s = f"{sur} {i1}{i2}".strip()
    if len(s) <= max_len:
        return s
    return s[: max(max_len - 2, 4)] + ".."


def _name_column_button_with_status(full_name: str, att: Optional[Attendance]) -> str:
    """Кнопка колонки ФИО: ✅/❌ + фамилия с инициалами."""
    base = _surname_initials_button_label(full_name)
    if att is not None and att.attended:
        return f"✅ {base}"
    return f"❌ {base}"


def build_step2_message_and_keyboard_rows(
    training: Training,
    athletes: List[Athlete],
    attendance_map: Dict[int, Attendance],
    page: int,
    page_size: int = ATTENDANCE_LIST_PAGE_SIZE,
    flash_html: Optional[str] = None,
) -> Tuple[str, List[List[Tuple[str, str]]]]:
    """
    HTML-текст и строки клавиатуры: список (text, callback_data).
    """
    total_count = len(athletes)

    max_page = max(0, (total_count - 1) // page_size) if total_count else 0
    page = max(0, min(page, max_page))
    start = page * page_size
    chunk = athletes[start : start + page_size]

    is_individual_slot = (
        (getattr(training, "training_format", None) or "").strip().lower()
        == TRAINING_FORMAT_INDIVIDUAL
    )
    if is_individual_slot:
        slot_title = (
            f"{training.training_date.strftime('%d.%m.%Y %H:%M')} — "
            f"{html.escape(training.sport_type)} — Индивидуальная\n\n"
        )
    else:
        age_group_ru = f"{format_age_group_label(training.age_group).lower()} группа"
        slot_title = (
            f"{training.training_date.strftime('%d.%m.%Y %H:%M')} — "
            f"{html.escape(training.sport_type)}, {age_group_ru}\n\n"
        )
    parts: List[str] = []
    if flash_html:
        parts.append(flash_html + "\n\n")
    parts.extend(
        [
            slot_title,
            "Пожалуйста, отметьте пришедших до окончания тренировки.",
        ]
    )
    if not athletes:
        parts.append("\n\n📭 На это время нет спортсменов с подходящим абонементом.")

    message = "".join(parts)

    keyboard_rows: List[List[Tuple[str, str]]] = []
    tid = training.id
    for athlete in chunk:
        full = (athlete.full_name or "").strip()
        att_row = attendance_map.get(athlete.id)
        keyboard_rows.append(
            [
                (_name_column_button_with_status(full, att_row), f"attnm_{tid}_{athlete.id}"),
                ("✅ Был", f"atmark_{tid}_{athlete.id}_1"),
                ("❌ Не был", f"atmark_{tid}_{athlete.id}_0"),
            ]
        )

    nav_row: List[Tuple[str, str]] = []
    if total_count > page_size:
        if page > 0:
            nav_row.append(("◀️ Пред.", f"attpg_{tid}_{page - 1}"))
        nav_row.append((f"📄 {page + 1}/{max_page + 1}", "attpg_info"))
        if page < max_page:
            nav_row.append(("След. ▶️", f"attpg_{tid}_{page + 1}"))
        if nav_row:
            keyboard_rows.append(nav_row)

    keyboard_rows.append([("🔙 К тренировкам на сегодня", "attendance_training_list")])
    keyboard_rows.append([("🏠 В меню", "back_to_menu_main")])

    return message, keyboard_rows


def parse_attendance_page_callback(data: str) -> Optional[Tuple[int, int]]:
    """attpg_{training_id}_{page}"""
    if not data.startswith("attpg_"):
        return None
    rest = data[6:]
    if "_" not in rest:
        return None
    left, right = rest.rsplit("_", 1)
    if not left.isdigit() or not right.isdigit():
        return None
    return int(left), int(right)


def parse_attendance_name_column_callback(data: str) -> Optional[Tuple[int, int]]:
    """attnm_{training_id}_{athlete_id} — колонка ФИО (без действия, только подсказка)."""
    if not data.startswith("attnm_"):
        return None
    parts = data.split("_")
    if len(parts) != 3 or parts[0] != "attnm":
        return None
    tid_s, aid_s = parts[1], parts[2]
    if not tid_s.isdigit() or not aid_s.isdigit():
        return None
    return int(tid_s), int(aid_s)


def parse_attendance_direct_mark_callback(data: str) -> Optional[Tuple[int, int, bool]]:
    """atmark_{training_id}_{athlete_id}_{0|1} — 1 был, 0 не был."""
    if not data.startswith("atmark_"):
        return None
    parts = data.split("_")
    if len(parts) != 4 or parts[0] != "atmark":
        return None
    tid_s, aid_s, bit = parts[1], parts[2], parts[3]
    if not tid_s.isdigit() or not aid_s.isdigit():
        return None
    if bit not in ("0", "1"):
        return None
    return int(tid_s), int(aid_s), bit == "1"
