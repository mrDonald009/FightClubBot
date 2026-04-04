"""
Поток «Отметить посещения»: тренировки на сегодня (шаг 1), список спортсменов и отметки (шаг 2).
"""
from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple, Union

from sqlalchemy import func
from sqlalchemy.orm import Session as OrmSession

from database.db_utils import get_user_role
from database.models import Admin, Athlete, Attendance, Coach, Subscription, Training
from utils.training_manager import TrainingManager
from utils.time_utils import now_moscow

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


@dataclass(frozen=True)
class TodaySlotDisplay:
    """Один ряд в списке тренировок на сегодня (шаг 1)."""

    is_virtual: bool
    training_id: Optional[int]
    virtual_token: Optional[str]
    sport_type: str
    age_group: str
    training_datetime: datetime


def get_coach_sport_type_name(user: Any) -> Optional[str]:
    if not user:
        return None
    if getattr(user, "sport_type_rel", None):
        return user.sport_type_rel.name
    return getattr(user, "sport_type", None)


def coach_training_access_error(user: Any, training: Training) -> Optional[str]:
    """
    Для тренера: тренировка должна быть его и по его виду спорта.
    Для админа: без ограничений.
    """
    if not user or get_user_role(user) != "coach":
        return None
    if training.coach_id != user.id:
        return "❌ Здесь только ваши тренировки. Выберите занятие, где вы указаны тренером."
    coach_sport = get_coach_sport_type_name(user)
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
                )
            )
        for sport_type_name, schedule_map in TrainingManager.TRAINING_SCHEDULE.items():
            for age_group in ("children", "adults"):
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
            or age_group not in ("children", "adults")
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
    athletes_query = (
        session.query(Athlete)
        .join(Subscription, Subscription.athlete_id == Athlete.id)
        .filter(
            Subscription.is_active == True,
            Subscription.sport_type == training.sport_type,
            Athlete.age_group == training.age_group,
            func.date(Subscription.start_date) <= training_day,
            func.date(Subscription.end_date) >= training_day,
        )
        .order_by(Athlete.full_name.asc())
    )
    athletes = athletes_query.all()
    athlete_ids = [a.id for a in athletes]
    attendance_map: Dict[int, Attendance] = {}
    if athlete_ids:
        existing = (
            session.query(Attendance)
            .filter(
                Attendance.training_id == training.id,
                Attendance.athlete_id.in_(athlete_ids),
            )
            .all()
        )
        attendance_map = {a.athlete_id: a for a in existing}
    return athletes, attendance_map


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
    marked_count = len(attendance_map)
    attended_count = sum(1 for a in attendance_map.values() if a.attended)
    absent_count = marked_count - attended_count
    pending_count = total_count - marked_count

    age_group_ru = "детская группа" if training.age_group == "children" else "взрослая группа"
    parts = [
        "📝 <b>ОТМЕТКА ПОСЕЩЕНИЯ</b>\n\n",
    ]
    if flash_html:
        parts.append(flash_html + "\n\n")
    parts.extend(
        [
            f"📅 <b>{training.training_date.strftime('%d.%m.%Y %H:%M')}</b> — "
            f"{html.escape(training.sport_type)}, {age_group_ru}\n",
            f"👥 Спортсменов в списке: <b>{total_count}</b> | Уже отмечено: <b>{marked_count}</b> | "
            f"Без отметки: <b>{pending_count}</b>\n",
            f"✅ Были: <b>{attended_count}</b> | ❌ Не были: <b>{absent_count}</b>\n\n",
            "<b>Шаг 2 из 2</b> — нажмите на фамилию, затем «был» или «не был».",
        ]
    )
    if not athletes:
        parts.append("\n\n📭 На это время нет спортсменов с подходящим абонементом.")

    message = "".join(parts)

    max_page = max(0, (total_count - 1) // page_size) if total_count else 0
    page = max(0, min(page, max_page))
    start = page * page_size
    chunk = athletes[start : start + page_size]

    keyboard_rows: List[List[Tuple[str, str]]] = []
    tid = training.id
    for athlete in chunk:
        att = attendance_map.get(athlete.id)
        if att is None:
            icon = "⏳"
        else:
            icon = "✅" if att.attended else "❌"
        full_name = (athlete.full_name or "").strip()
        if len(full_name) > 24:
            full_name = full_name[:22] + ".."
        keyboard_rows.append(
            [(f"{icon} {full_name}", f"mark_attendance_{athlete.id}_{tid}")]
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

    keyboard_rows.append(
        [
            ("🔙 К тренировкам на сегодня", "attendance_training_list"),
            ("🏠 В меню", "back_to_menu_main"),
        ]
    )

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
