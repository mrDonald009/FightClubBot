import logging
import re
from datetime import date, datetime, timedelta
import calendar as py_calendar
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes, CallbackQueryHandler, CommandHandler, ConversationHandler
from handlers.coach_handlers import (
    MENU_BUTTONS,
    normalize_full_name,
    validate_full_name_strict,
    is_phone_number,
    has_digits,
    is_valid_name_format,
)
from database.models import Athlete, Subscription, Training, Attendance, Coach, Admin, GlobalFreeze
from core.database import get_db_session
from database.db_utils.subscription_activation_payment import (
    record_payment_on_subscription_activation,
)
from database.db_utils import (
    ACTIVATION_GRACE_AFTER_START,
    get_user_by_telegram_id,
    get_user_role,
    get_athlete_card_info,
    calculate_actual_trainings_remaining as db_calculate_actual_trainings_remaining,
    sync_subscription_trainings_remaining,
    is_training_in_global_freeze,
    expire_stale_subscription_freezes,
    subscription_is_currently_frozen,
    find_next_non_frozen_calendar_date,
    find_next_non_frozen_training_date,
    training_datetime_compact,
    parse_training_datetime_compact,
    now_moscow,
    individual_training_end_time,
    training_end_time,
)
from database.db_utils.training_slots import (
    INDIVIDUAL_TRAINING_AGE_GROUP_STORED,
    TRAINING_FORMAT_INDIVIDUAL,
    dedupe_individual_trainings_by_slot,
    find_group_training_on_calendar_day,
    individual_slot_conflicts,
    individual_slot_training_ids,
    iter_allowed_individual_starts,
)
from database.db_utils.visit_history import upsert_visit_history_for_training
from typing import List, Optional, Union
from services.permissions import is_staff, can_edit_athlete, is_admin, is_athlete, is_coach

from sqlalchemy import and_, exists, func, or_, text
from sqlalchemy.orm import joinedload
import html
from utils.age_groups import format_age_group_label
from utils.attendance_display import (
    attendance_icon_for_training,
    attendance_label_ru_for_training,
    count_implicit_absent_slots,
)
from utils.training_manager import TrainingManager
from utils.coach_sport import coach_sport_type_name
from utils.discipline_keys import discipline_key_for, format_training_format_ru
from utils.subscription_checker import SubscriptionChecker
from utils.subscription_resolve import (
    active_subscriptions_all,
    active_subscription_for_training,
    subscription_for_coach_sport,
)

logger = logging.getLogger(__name__)


def calculate_actual_trainings_remaining(session, subscription):
    """Совместимый враппер над единым расчетом из database.db_utils."""
    return db_calculate_actual_trainings_remaining(session, subscription)


def _is_individual_subscription(sub: Subscription) -> bool:
    st = (getattr(sub, "subscription_type", None) or "").strip().lower()
    if st == "individual":
        return True
    dk = (getattr(sub, "discipline_key", None) or "").strip().lower()
    return "individual" in dk


def _individual_subscription_is_past_or_completed(
    sub: Subscription,
    now: datetime = None,
) -> bool:
    """Individual-бронь считается прошедшей: истёк слот или списаны все тренировки."""
    if not _is_individual_subscription(sub):
        return False
    now = now or now_moscow()
    if sub.trainings_remaining is not None and sub.trainings_remaining <= 0:
        return True
    if sub.end_date and sub.end_date < now:
        return True
    return False


def _individual_slot_relative_hint(sub: Subscription, now: datetime = None) -> str:
    """Краткая подсказка «сегодня / завтра / через N дн.» для слота individual."""
    if not sub.start_date:
        return "без даты"
    now = now or now_moscow()
    slot = sub.start_date
    if slot.date() == now.date():
        return f"сегодня в {slot.strftime('%H:%M')}"
    tomorrow = (now + timedelta(days=1)).date()
    if slot.date() == tomorrow:
        return f"завтра в {slot.strftime('%H:%M')}"
    days = (slot.date() - now.date()).days
    if days > 1:
        return f"через {days} дн."
    return "завершена"


def _individual_attendance_suffix(session, sub: Subscription) -> str:
    """Суффикс посещения для кнопки individual, если отметка уже есть."""
    if session is None:
        return ""
    att = (
        session.query(Attendance)
        .filter_by(subscription_id=sub.id)
        .order_by(Attendance.id.desc())
        .first()
    )
    if not att:
        return ""
    if att.attended:
        return " · ✅ был"
    return " · ❌ не был"


def _individual_subscription_button_label(
    sub: Subscription,
    session=None,
    *,
    now: datetime = None,
) -> str:
    """Подпись кнопки individual в picker: дата, относительное время, посещение."""
    now = now or now_moscow()
    status_icon = "🟢"
    if sub.start_date:
        slot_short = sub.start_date.strftime("%d.%m %H:%M")
    else:
        slot_short = "без даты"
    hint = _individual_slot_relative_hint(sub, now=now)
    sport = (sub.sport_type or "—").strip()
    attendance = _individual_attendance_suffix(session, sub)
    text = f"{status_icon} {slot_short} — {hint} ({sport}){attendance}"
    return _truncate_inline_button_text(text)


def _count_past_individual_subscriptions(
    subscriptions,
    *,
    sport_type: str = None,
    now: datetime = None,
) -> int:
    subs = subscriptions
    if sport_type:
        subs = [s for s in subs if (s.sport_type or "").strip() == sport_type]
    individual = [s for s in subs if _is_individual_subscription(s)]
    return sum(
        1 for s in individual if _individual_subscription_is_past_or_completed(s, now=now)
    )


def _subscription_picker_should_show(
    group_subs,
    upcoming_individual_subs,
) -> bool:
    """Picker только при 2+ активных направлениях (групповой + individual и т.п.)."""
    return len(group_subs) + len(upcoming_individual_subs) > 1


_HISTORY_FILTER_ALL = "all"
_HISTORY_FILTER_INDIVIDUAL = "individual"
_HISTORY_FILTER_GROUP = "group"
_HISTORY_FILTER_SINGLE = "single"

_VISIT_HISTORY_LOOKBACK_DAYS = 120
_VISIT_HISTORY_MONTH_DAYS = 30
_SUBSCRIPTION_HISTORY_YEAR_DAYS = 365
_VISIT_HISTORY_MAX_LINES = 28
_VISIT_HISTORY_MODE_MONTH = "month"
_VISIT_HISTORY_MODE_OLDER_MENU = "older_menu"
_VISIT_HISTORY_MODE_OLDER_MONTH = "older_month"
_HISTORY_SECTION_LIST_LIMIT = 25


def _history_subscription_buckets(all_subscriptions):
    """Разбивка истории: individual / месячные групповые / разовые."""
    individual_subs = []
    monthly_subs = []
    single_subs = []
    for sub in all_subscriptions:
        if _is_individual_subscription(sub):
            individual_subs.append(sub)
        elif (sub.subscription_type or "").strip().lower() == "single":
            single_subs.append(sub)
        else:
            monthly_subs.append(sub)
    return individual_subs, monthly_subs, single_subs


def _truncate_inline_button_text(text: str, max_len: int = 64) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def _history_subscription_button_label(sub: Subscription, session=None) -> str:
    """Краткая подпись записи в списке истории; детали — на следующем экране."""
    del session  # детали посещения только в карточке записи
    if _is_individual_subscription(sub):
        if sub.start_date:
            text = sub.start_date.strftime("%d.%m.%Y %H:%M")
        else:
            text = "без даты"
    elif (sub.subscription_type or "").strip().lower() == "single":
        if sub.start_date:
            text = sub.start_date.strftime("%d.%m.%Y %H:%M")
        else:
            text = "Разовый"
    else:
        if sub.start_date and sub.end_date:
            text = (
                f"{sub.start_date.strftime('%d.%m.%Y')}—"
                f"{sub.end_date.strftime('%d.%m.%Y')}"
            )
        elif sub.start_date:
            text = sub.start_date.strftime("%d.%m.%Y")
        else:
            text = "—"
    return _truncate_inline_button_text(text)


def _history_subscription_list_lines(sub: Subscription, idx: int) -> str:
    """Текстовый блок одной записи в списке истории (HTML)."""
    status_text = _format_subscription_status_ui(sub)
    status_icon = _status_icon_from_status_text(status_text)
    lines = f"<b>{idx}. "
    if _is_individual_subscription(sub):
        lines += f"Индивидуальная бронь #{sub.id}</b> {status_icon}\n"
        if sub.start_date:
            lines += f"   Слот: <b>{_format_dt(sub.start_date)}</b>\n"
        else:
            lines += "   Слот: не назначен\n"
        sport = (sub.sport_type or "—").strip()
        lines += f"   Вид спорта: {html.escape(sport)}\n"
    else:
        lines += f"Абонемент #{sub.id}</b> {status_icon}\n"
        sub_type = _format_subscription_type_ru(sub.subscription_type)
        start_date_str = sub.start_date.strftime("%d.%m.%Y") if sub.start_date else "—"
        end_date_str = sub.end_date.strftime("%d.%m.%Y") if sub.end_date else "—"
        lines += f"   Тип: {sub_type}\n"
        lines += f"   Период: {start_date_str} — {end_date_str}\n"
        trainings_remaining = sub.trainings_remaining or 0
        trainings_total = sub.trainings_total or 0
        if trainings_total is not None:
            lines += f"   Тренировки: {trainings_remaining}/{trainings_total}\n"
        else:
            lines += "   Тренировки: —/—\n"
    lines += f"   Статус: {status_text}\n"
    if sub.created_at:
        lines += f"   Создан: {sub.created_at.strftime('%d.%m.%Y %H:%M')}\n"
    lines += "\n"
    return lines


def _parse_subscription_history_callback(callback_data: str):
    """
    Разбор subscription_history_* с опциональным периодом (_older / _older_YYYYMM).
    Возвращает (athlete_id, history_filter, athlete_self, month_mode, older_yyyymm).
    """
    raw = callback_data.replace("subscription_history_", "")
    history_filter = _HISTORY_FILTER_ALL
    if raw.startswith("individual_"):
        history_filter = _HISTORY_FILTER_INDIVIDUAL
        raw = raw[len("individual_") :]
    elif raw.startswith("group_"):
        history_filter = _HISTORY_FILTER_GROUP
        raw = raw[len("group_") :]
    elif raw.startswith("single_"):
        history_filter = _HISTORY_FILTER_SINGLE
        raw = raw[len("single_") :]
    athlete_self = raw.startswith("athlete_")
    if athlete_self:
        raw = raw[len("athlete_") :]

    parts = raw.split("_")
    athlete_id = int(parts[0])
    month_mode = _VISIT_HISTORY_MODE_MONTH
    older_yyyymm = None
    if len(parts) >= 2 and parts[1] == "older":
        if len(parts) == 2:
            month_mode = _VISIT_HISTORY_MODE_OLDER_MENU
        elif len(parts) == 3 and len(parts[2]) == 6 and parts[2].isdigit():
            month_mode = _VISIT_HISTORY_MODE_OLDER_MONTH
            older_yyyymm = parts[2]
        else:
            raise ValueError("invalid subscription history older month")
    return athlete_id, history_filter, athlete_self, month_mode, older_yyyymm


def _subscription_history_back_callback(
    athlete_id: int, *, is_coach_viewing: bool, athlete_self: bool
) -> str:
    if is_coach_viewing:
        return f"subscription_athlete_{athlete_id}"
    return "athlete_subscription_refresh"


def _subscription_history_list_callback(
    athlete_id: int,
    *,
    history_filter: str = _HISTORY_FILTER_ALL,
    athlete_self: bool = False,
) -> str:
    base = "subscription_history"
    if history_filter == _HISTORY_FILTER_INDIVIDUAL:
        base += "_individual"
    elif history_filter == _HISTORY_FILTER_GROUP:
        base += "_group"
    elif history_filter == _HISTORY_FILTER_SINGLE:
        base += "_single"
    if athlete_self:
        return f"{base}_athlete_{athlete_id}"
    return f"{base}_{athlete_id}"


def _subscription_history_section_callback(
    athlete_id: int,
    *,
    history_filter: str,
    athlete_self: bool,
    month_mode: str = _VISIT_HISTORY_MODE_MONTH,
    older_yyyymm: str = None,
) -> str:
    base = _subscription_history_list_callback(
        athlete_id,
        history_filter=history_filter,
        athlete_self=athlete_self,
    )
    if month_mode == _VISIT_HISTORY_MODE_OLDER_MENU:
        return f"{base}_older"
    if month_mode == _VISIT_HISTORY_MODE_OLDER_MONTH and older_yyyymm:
        return f"{base}_older_{older_yyyymm}"
    return base


def _subscription_history_category_keyboard(
    athlete_id: int,
    *,
    athlete_self: bool,
):
    """Архив: выбор типа абонемента."""
    rows = [
        [
            InlineKeyboardButton(
                "Индивидуальный",
                callback_data=_subscription_history_list_callback(
                    athlete_id,
                    history_filter=_HISTORY_FILTER_INDIVIDUAL,
                    athlete_self=athlete_self,
                ),
            )
        ],
        [
            InlineKeyboardButton(
                "Месячный",
                callback_data=_subscription_history_list_callback(
                    athlete_id,
                    history_filter=_HISTORY_FILTER_GROUP,
                    athlete_self=athlete_self,
                ),
            )
        ],
        [
            InlineKeyboardButton(
                "Разовый",
                callback_data=_subscription_history_list_callback(
                    athlete_id,
                    history_filter=_HISTORY_FILTER_SINGLE,
                    athlete_self=athlete_self,
                ),
            )
        ],
    ]
    return rows


def _history_sections_back_callback(athlete_id: int, *, athlete_self: bool) -> str:
    return _subscription_history_list_callback(
        athlete_id,
        history_filter=_HISTORY_FILTER_ALL,
        athlete_self=athlete_self,
    )


def _history_monthly_subscriptions_sorted(monthly_subs):
    return sorted(
        monthly_subs,
        key=lambda s: (s.created_at or datetime.min, s.id or 0),
        reverse=True,
    )


def _history_single_subscriptions_sorted(single_subs):
    return sorted(
        single_subs,
        key=lambda s: (s.start_date or datetime.min, s.id or 0),
        reverse=True,
    )


def _history_individual_subscriptions_sorted(individual_subs):
    return sorted(
        individual_subs,
        key=lambda s: (s.start_date or datetime.min, s.id or 0),
        reverse=True,
    )


def _history_subscriptions_for_filter(history_filter, individual_subs, monthly_subs, single_subs):
    if history_filter == _HISTORY_FILTER_INDIVIDUAL:
        return _history_individual_subscriptions_sorted(individual_subs)
    if history_filter == _HISTORY_FILTER_GROUP:
        return _history_monthly_subscriptions_sorted(monthly_subs)
    if history_filter == _HISTORY_FILTER_SINGLE:
        return _history_single_subscriptions_sorted(single_subs)
    return []


def _history_subscription_sort_date(sub: Subscription) -> datetime:
    if sub.start_date:
        return sub.start_date
    if sub.created_at:
        return sub.created_at
    return datetime.min


def _filter_subscriptions_last_month(subscriptions, *, now: datetime):
    month_cutoff, _lookback = _visit_history_older_cutoffs(now)
    return [
        s for s in subscriptions if _history_subscription_sort_date(s) >= month_cutoff
    ]


def _filter_subscriptions_last_year(subscriptions, *, now: datetime):
    year_cutoff = now - timedelta(days=_SUBSCRIPTION_HISTORY_YEAR_DAYS)
    return [
        s for s in subscriptions if _history_subscription_sort_date(s) >= year_cutoff
    ]


def _filter_subscriptions_for_history_period(
    subscriptions, history_filter: str, *, now: datetime
):
    if history_filter == _HISTORY_FILTER_GROUP:
        return _filter_subscriptions_last_year(subscriptions, now=now)
    return _filter_subscriptions_last_month(subscriptions, now=now)


def _subscription_history_period_caption(history_filter: str) -> str:
    if history_filter == _HISTORY_FILTER_GROUP:
        return "За последний год"
    return "За последний месяц"


def _filter_subscriptions_older_month(
    subscriptions, *, year: int, month: int, now: datetime
):
    month_cutoff, lookback_cutoff = _visit_history_older_cutoffs(now)
    return [
        s
        for s in subscriptions
        if lookback_cutoff <= _history_subscription_sort_date(s) < month_cutoff
        and _history_subscription_sort_date(s).year == year
        and _history_subscription_sort_date(s).month == month
    ]


def _has_older_subscriptions(subscriptions, *, now: datetime) -> bool:
    month_cutoff, lookback_cutoff = _visit_history_older_cutoffs(now)
    return any(
        lookback_cutoff <= _history_subscription_sort_date(s) < month_cutoff
        for s in subscriptions
    )


def _group_subscriptions_by_month(subscriptions, *, now: datetime) -> dict:
    month_cutoff, lookback_cutoff = _visit_history_older_cutoffs(now)
    grouped = {}
    for sub in subscriptions:
        dt = _history_subscription_sort_date(sub)
        if lookback_cutoff <= dt < month_cutoff:
            key = (dt.year, dt.month)
            grouped.setdefault(key, []).append(sub)
    for key in grouped:
        grouped[key].sort(
            key=lambda s: (_history_subscription_sort_date(s), s.id or 0),
            reverse=True,
        )
    return grouped


def _render_subscription_archive_type_picker_message(athlete_name: str) -> str:
    message = "<b>Абонемент</b>\n\n"
    message += f"👤 <b>{html.escape(athlete_name)}</b>\n\n"
    message += "<i>Архив</i>\n"
    message += "<b>Выберите тип абонемента:</b>"
    return message


def _render_subscription_history_section_message(
    athlete_name: str,
    title: str,
    *,
    period_caption: str = None,
    is_month_picker: bool = False,
) -> str:
    message = "<b>Абонемент</b>\n\n"
    message += f"👤 <b>{html.escape(athlete_name)}</b>"
    if title:
        message += f"\n\n{title}"
    if period_caption:
        message += f"\n\n<i>{html.escape(period_caption)}</i>"
    if is_month_picker:
        message += "\n\n<b>Выберите месяц:</b>"
    return message


def _build_subscription_archive_list_keyboard(shown_subs, sections_cb: str):
    keyboard = [
        [
            InlineKeyboardButton(
                _history_subscription_button_label(sub),
                callback_data=f"view_sub_{sub.id}",
            )
        ]
        for sub in shown_subs
    ]
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data=sections_cb)])
    return keyboard


def _history_section_title(history_filter: str) -> str:
    if history_filter == _HISTORY_FILTER_INDIVIDUAL:
        return "<b>Индивидуальный</b>"
    if history_filter == _HISTORY_FILTER_GROUP:
        return "<b>Месячный</b>"
    if history_filter == _HISTORY_FILTER_SINGLE:
        return "<b>Разовый</b>"
    return ""


def _parse_visits_callback(callback_data: str) -> tuple:
    """
    visits_{id} — последний месяц;
    visits_{id}_older — выбор месяца (предшествующие);
    visits_{id}_older_{yyyymm} — тренировки за месяц.
    """
    if not callback_data.startswith("visits_"):
        raise ValueError("invalid visits callback")
    parts = callback_data[7:].split("_")
    athlete_id = int(parts[0])
    mode = _VISIT_HISTORY_MODE_MONTH
    older_yyyymm = None
    if len(parts) >= 2 and parts[1] == "older":
        if len(parts) == 2:
            mode = _VISIT_HISTORY_MODE_OLDER_MENU
        elif len(parts) == 3 and len(parts[2]) == 6 and parts[2].isdigit():
            mode = _VISIT_HISTORY_MODE_OLDER_MONTH
            older_yyyymm = parts[2]
        else:
            raise ValueError("invalid visits older month")
    return athlete_id, mode, older_yyyymm


def _visits_month_callback(athlete_id: int) -> str:
    return f"visits_{athlete_id}"


def _visits_older_menu_callback(athlete_id: int) -> str:
    return f"visits_{athlete_id}_older"


def _visits_older_month_callback(athlete_id: int, year: int, month: int) -> str:
    return f"visits_{athlete_id}_older_{year:04d}{month:02d}"


def _yyyymm_to_year_month(yyyymm: str) -> tuple:
    return int(yyyymm[:4]), int(yyyymm[4:6])


def _visit_history_month_label(year: int, month: int) -> str:
    """Цифровое обозначение месяца для кнопок и подписи, напр. 04.2026."""
    return f"{month:02d}.{year}"


def _visit_history_older_cutoffs(now: datetime) -> tuple:
    month_cutoff = now - timedelta(days=_VISIT_HISTORY_MONTH_DAYS)
    lookback_cutoff = now - timedelta(days=_VISIT_HISTORY_LOOKBACK_DAYS)
    return month_cutoff, lookback_cutoff


def _filter_visit_rows_last_month(rows: List[tuple], *, now: datetime) -> List[tuple]:
    month_cutoff, _lookback = _visit_history_older_cutoffs(now)
    return [r for r in rows if r[0] >= month_cutoff]


def _filter_visit_rows_older(rows: List[tuple], *, now: datetime) -> List[tuple]:
    month_cutoff, lookback_cutoff = _visit_history_older_cutoffs(now)
    return [r for r in rows if lookback_cutoff <= r[0] < month_cutoff]


def _group_older_visit_rows_by_month(
    rows: List[tuple], *, now: datetime
) -> dict:
    """{(year, month): [rows...]} для предшествующего периода."""
    grouped = {}
    for row in _filter_visit_rows_older(rows, now=now):
        key = (row[0].year, row[0].month)
        grouped.setdefault(key, []).append(row)
    for key in grouped:
        grouped[key].sort(key=lambda r: r[0])
    return grouped


def _has_older_visit_rows(rows: List[tuple], *, now: datetime) -> bool:
    return bool(_filter_visit_rows_older(rows, now=now))


def _filter_visit_rows_older_month(
    rows: List[tuple], *, year: int, month: int, now: datetime
) -> List[tuple]:
    return [
        r
        for r in _filter_visit_rows_older(rows, now=now)
        if r[0].year == year and r[0].month == month
    ]


def _render_visit_history_month_picker(
    athlete_name: str, months: dict
) -> str:
    message = "📅 <b>Посещения</b>\n\n"
    message += f"👤 <b>{html.escape(athlete_name)}</b>\n\n"
    message += "<i>Архив</i>\n"
    message += "<b>Выберите месяц:</b>\n"
    if not months:
        message += "\n📭 Нет записей за этот период.\n"
    return message


def _build_visit_history_keyboard(
    athlete_id: int,
    mode: str,
    *,
    has_older: bool = False,
    older_months: Optional[dict] = None,
) -> InlineKeyboardMarkup:
    keyboard_rows = []
    if mode == _VISIT_HISTORY_MODE_MONTH and has_older:
        keyboard_rows.append(
            [
                InlineKeyboardButton(
                    "📜 Архив",
                    callback_data=_visits_older_menu_callback(athlete_id),
                )
            ]
        )
    elif mode == _VISIT_HISTORY_MODE_OLDER_MENU and older_months:
        for year, month in sorted(older_months.keys()):
            keyboard_rows.append(
                [
                    InlineKeyboardButton(
                        _visit_history_month_label(year, month),
                        callback_data=_visits_older_month_callback(
                            athlete_id, year, month
                        ),
                    )
                ]
            )
        keyboard_rows.append(
            [
                InlineKeyboardButton(
                    "📅 За последний месяц",
                    callback_data=_visits_month_callback(athlete_id),
                )
            ]
        )
    elif mode == _VISIT_HISTORY_MODE_OLDER_MONTH:
        keyboard_rows.append(
            [
                InlineKeyboardButton(
                    "◀️ К выбору месяца",
                    callback_data=_visits_older_menu_callback(athlete_id),
                )
            ]
        )
        keyboard_rows.append(
            [
                InlineKeyboardButton(
                    "📅 За последний месяц",
                    callback_data=_visits_month_callback(athlete_id),
                )
            ]
        )
    keyboard_rows.append(
        [
            InlineKeyboardButton(
                "🔙 Назад к карточке",
                callback_data=f"athlete_{athlete_id}",
            )
        ]
    )
    return InlineKeyboardMarkup(keyboard_rows)


def _is_individual_training_slot(training: Training) -> bool:
    return (
        (getattr(training, "training_format", None) or "").strip().lower()
        == TRAINING_FORMAT_INDIVIDUAL
    )


def _subscription_for_visit_slot(
    session, athlete_id: int, training: Training
) -> Optional[Subscription]:
    """Абонемент, покрывающий слот (для подписи «Групповая» / «Разовая» / «Индивидуальная»)."""
    if not training.training_date:
        return None
    td = training.training_date
    subs = (
        session.query(Subscription)
        .filter(
            Subscription.athlete_id == athlete_id,
            Subscription.sport_type == training.sport_type,
            func.date(Subscription.start_date) <= func.date(td),
            func.date(Subscription.end_date) >= func.date(td),
        )
        .order_by(Subscription.id.desc())
        .all()
    )
    if not subs:
        return None
    if _is_individual_training_slot(training):
        for sub in subs:
            if (getattr(sub, "subscription_type", None) or "").strip().lower() == "individual":
                return sub
        return subs[0]
    for sub in subs:
        if (getattr(sub, "subscription_type", None) or "").strip().lower() == "single":
            return sub
    for sub in subs:
        st = (getattr(sub, "subscription_type", None) or "").strip().lower()
        if st != "individual":
            return sub
    return subs[0]


def _visit_training_kind_ru(
    training: Training,
    attendance: Optional[Attendance] = None,
    subscription: Optional[Subscription] = None,
) -> str:
    if _is_individual_training_slot(training):
        return "Индивидуальная"
    sub = subscription
    if sub is None and attendance is not None:
        sub = attendance.subscription
    st = (getattr(sub, "subscription_type", None) or "").strip().lower() if sub else ""
    if st == "single":
        return "Разовая"
    return "Групповая"


def _visit_history_slot_present(attendance: Optional[Attendance]) -> bool:
    return attendance is not None and bool(attendance.attended)


def _format_visit_history_slot_line(
    training: Training,
    attendance: Optional[Attendance],
    *,
    subscription: Optional[Subscription] = None,
) -> str:
    present = _visit_history_slot_present(attendance)
    icon = "✅" if present else "❌"
    time_str = (
        training.training_date.strftime("%H:%M") if training.training_date else "—"
    )
    sport = html.escape((training.sport_type or "—").strip())
    kind = _visit_training_kind_ru(training, attendance, subscription)
    return f"{icon} {time_str} · {sport} | {kind}\n"


def _format_visit_history_compact_list(display_entries: List[tuple]) -> str:
    """Список: от старых к новым (день и время по возрастанию)."""
    if not display_entries:
        return ""
    by_day = {}
    for dt, row_line, _present in display_entries:
        by_day.setdefault(dt.date(), []).append((dt, row_line))

    lines = ["<b>Тренировки:</b>\n"]
    for day in sorted(by_day.keys()):
        for dt, row_line in sorted(by_day[day], key=lambda x: x[0]):
            lines.append(f"{dt.strftime('%d.%m')}  {row_line.strip()}\n")
    return "".join(lines)


def _render_visit_history_message(
    athlete_name: str,
    display_entries: List[tuple],
    *,
    total_matching: Optional[int] = None,
    period_caption: Optional[str] = None,
) -> str:
    """display_entries — последние N записей за выбранный период."""
    message = "📅 <b>Посещения</b>\n\n"
    message += f"👤 <b>{html.escape(athlete_name)}</b>\n"
    if period_caption:
        message += f"\n<i>{html.escape(period_caption)}</i>\n"
    message += "\n"

    total_all = total_matching if total_matching is not None else len(display_entries)
    if total_all > len(display_entries) and display_entries:
        message += (
            f"<i>Показаны последние {len(display_entries)} из {total_all}</i>\n\n"
        )

    if not display_entries:
        message += "📭 Нет записей посещений.\n"
        return message

    message += _format_visit_history_compact_list(display_entries)
    if not message.endswith("\n"):
        message += "\n"
    return message


def _supports_multi_individual_bookings(session) -> bool:
    """Схема БД допускает несколько individual-строк на одного спортсмена."""
    try:
        row = session.execute(
            text(
                "SELECT name FROM sqlite_master "
                "WHERE type='index' AND name='uq_subscriptions_individual_slot'"
            )
        ).first()
        return row is not None
    except Exception:
        return False


def prepare_individual_subscription_for_activation(
    session,
    athlete: Athlete,
    sport_type: Optional[str],
    *,
    responsible_coach_id: Optional[int] = None,
) -> Subscription:
    """Новая строка individual-абонемента под выбранный слот (без перезаписи прошлых броней)."""
    st = (sport_type or athlete.sport_type or "").strip()
    if not st:
        raise ValueError("Не указан вид спорта")

    dk = discipline_key_for(st, format="individual")
    from database.db_utils.subscriptions import create_subscription as db_create_subscription

    return db_create_subscription(
        session=session,
        athlete_id=athlete.id,
        subscription_type="individual",
        sport_type=st,
        discipline_key=dk,
        subscription_format="individual",
        responsible_coach_id=responsible_coach_id,
        commit=False,
    )


def _format_subscription_type_ru(subscription_type: Optional[str]) -> str:
    """Отобразить тип абонемента по-русски (месячный / разовый; до активации может быть NULL)."""
    if subscription_type == "monthly":
        return "Месячный"
    if subscription_type == "single":
        return "Разовый"
    if subscription_type == "individual":
        return "Индивидуальный"
    return "Не указан"


def _supports_individual_subscription_type(session) -> bool:
    """Проверка схемы SQLite: допускает ли CHECK в subscriptions значение 'individual'."""
    try:
        row = session.execute(
            text("SELECT sql FROM sqlite_master WHERE type='table' AND name='subscriptions'")
        ).first()
    except Exception:
        # В сомнительных случаях не блокируем флоу на проверке.
        return True
    ddl = ((row[0] if row else "") or "").lower()
    if not ddl:
        return True
    return "individual" in ddl


def _has_legacy_unique_athlete_constraint(session) -> bool:
    """Проверка legacy-схемы: UNIQUE только по athlete_id (1 спортсмен = 1 абонемент)."""
    if _supports_multi_individual_bookings(session):
        return False
    try:
        row = session.execute(
            text("SELECT sql FROM sqlite_master WHERE type='table' AND name='subscriptions'")
        ).first()
        ddl = ((row[0] if row else "") or "").lower()
        if ddl:
            if "unique (athlete_id)" in ddl or 'unique("athlete_id")' in ddl:
                return True
    except Exception:
        return False

    try:
        idx_rows = session.execute(
            text(
                "SELECT name, sql FROM sqlite_master "
                "WHERE type='index' AND tbl_name='subscriptions'"
            )
        ).fetchall()
    except Exception:
        return False

    for r in idx_rows:
        name = ((r[0] if r else "") or "").lower()
        sql = ((r[1] if len(r) > 1 else "") or "").lower()
        if not sql or "unique" not in sql:
            continue
        if name in (
            "uq_subscriptions_individual_slot",
            "uq_subscriptions_athlete_discipline_non_individual",
            "uq_subscriptions_athlete_discipline",
        ):
            continue
        if "discipline_key" in sql or "start_date" in sql:
            continue
        if "athlete_id" in sql:
            return True
    return False


def _format_dt(dt: datetime) -> str:
    """Единый формат даты/времени для UI."""
    return dt.strftime('%d.%m.%Y %H:%M') if dt else "—"


def _freeze_note(subscription: Subscription) -> str:
    """Устарело: продление показывается в _append_subscription_freeze_ui_lines."""
    return ""


def _append_subscription_freeze_ui_lines(message: str, subscription: Subscription, *, now=None) -> str:
    """Строки заморозки / продления срока для карточки абонемента."""
    now = now or now_moscow()
    if subscription_is_currently_frozen(subscription, now=now):
        if subscription.frozen_from:
            message += f"• ❄️ Заморожен с: {_format_dt(subscription.frozen_from)}\n"
        if subscription.frozen_until:
            message += f"• ❄️ Заморожен до: {_format_dt(subscription.frozen_until)}\n"
    days = subscription.frozen_training_days_total or 0
    if days > 0:
        st = (subscription.subscription_type or "").strip().lower()
        if st in ("single", "individual"):
            message += "• 📅 Тренировка перенесена (заморозка)\n"
        else:
            message += f"• 📅 Продлено на {days} тр. дней (заморозки)\n"
    return message


def _athlete_has_active_frozen_subscription(athlete: Athlete, *, now=None) -> bool:
    now = now or now_moscow()
    return any(
        s.is_active and subscription_is_currently_frozen(s, now=now)
        for s in athlete.subscriptions
    )


def _format_subscription_status_ui(subscription: Subscription) -> str:
    """
    Единое отображение статуса абонемента в UI (по МСК):
    - Истек N дней назад
    - Заморожен (персональная заморозка)
    - Активен
    - fallback в базовый статус checker
    """
    if subscription and subscription.end_date:
        now = now_moscow()
        if subscription.end_date < now:
            days_expired = (now - subscription.end_date).days
            return f"🔴 Истек {days_expired} дней назад"
        if subscription.is_active:
            if subscription_is_currently_frozen(subscription, now=now):
                return "❄️ Заморожен"
            return "✅ Активен"
    return SubscriptionChecker.format_subscription_status(subscription)


def _status_icon_from_status_text(status_text: str) -> str:
    """Иконка статуса для кнопок списков."""
    if not status_text:
        return "⚪"
    if status_text.startswith("✅"):
        return "🟢"
    if status_text.startswith("🔴"):
        return "🔴"
    if status_text.startswith("🟡"):
        return "🟡"
    if status_text.startswith("❄️"):
        return "❄️"
    return "⚪"


def _get_schedule(sport_type: str, age_group: str):
    schedule = TrainingManager.TRAINING_SCHEDULE.get(sport_type, {}).get(age_group)
    return schedule


async def _finalize_subscription_activation(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    query,
    session,
    subscription: Subscription,
    athlete: Athlete,
    start_date: datetime,
    *,
    after_nav: str = "subscription_card",
):
    """Сохранить активацию с выбранной первой датой тренировки."""
    sport_type = subscription.sport_type or athlete.sport_type
    age_group = athlete.age_group

    from database.db_utils import (
        _calculate_12th_training_date,
        training_end_time,
        _create_and_deduct_scheduled_trainings,
    )

    if subscription.subscription_type == "monthly":
        end_date = _calculate_12th_training_date(
            start_date, sport_type, age_group, session=session
        )
    elif subscription.subscription_type == "single":
        from database.db_utils.club_settings import get_group_training_duration_minutes

        end_date = training_end_time(
            start_date, get_group_training_duration_minutes(session)
        )
    elif subscription.subscription_type == "individual":
        from database.db_utils.club_settings import get_individual_training_duration_minutes

        end_date = individual_training_end_time(
            start_date, get_individual_training_duration_minutes(session)
        )
    else:
        await query.edit_message_text("❌ Сначала выберите тип абонемента.")
        return

    subscription.is_active = True
    subscription.start_date = start_date
    subscription.end_date = end_date

    if subscription.subscription_type == "monthly" and subscription.sport_type and athlete.age_group:
        _create_and_deduct_scheduled_trainings(session, subscription, athlete, start_date, end_date)
    elif subscription.subscription_type == "single":
        coach_id = athlete.created_by if athlete.created_by else None
        training = session.query(Training).filter_by(
            sport_type=sport_type,
            age_group=age_group,
            training_date=start_date,
            is_cancelled=False,
        ).first()
        if not training:
            training = find_group_training_on_calendar_day(
                session,
                sport_type=sport_type,
                age_group=age_group,
                day=start_date.date(),
                coach_id=coach_id,
            )
        if not training:
            training = Training(
                sport_type=sport_type,
                age_group=age_group,
                training_date=start_date,
                is_cancelled=False,
                coach_id=coach_id,
            )
            session.add(training)
            session.flush()
        elif coach_id and not getattr(training, "coach_id", None):
            training.coach_id = coach_id
            session.flush()
    elif subscription.subscription_type == "individual":
        coach_id = subscription.responsible_coach_id or athlete.created_by
        training = (
            session.query(Training)
            .filter(
                Training.sport_type == sport_type,
                Training.training_date == start_date,
                Training.is_cancelled.is_(False),
                Training.training_format == TRAINING_FORMAT_INDIVIDUAL,
                Training.coach_id == coach_id,
            )
            .order_by(Training.id.asc())
            .first()
        )
        if not training:
            training = Training(
                sport_type=sport_type,
                age_group=INDIVIDUAL_TRAINING_AGE_GROUP_STORED,
                training_date=start_date,
                is_cancelled=False,
                coach_id=coach_id,
                training_format=TRAINING_FORMAT_INDIVIDUAL,
            )
            session.add(training)
            session.flush()
        elif coach_id and not getattr(training, "coach_id", None):
            training.coach_id = coach_id
            session.flush()

    sync_subscription_trainings_remaining(session, subscription)
    record_payment_on_subscription_activation(
        session,
        subscription,
        start_date,
        recorded_by_telegram_id=query.from_user.id,
    )
    session.commit()

    if after_nav == "calendar_day":
        from handlers.coach_handlers import render_calendar_day_view
        from sqlalchemy.orm import joinedload

        coach_user = get_user_by_telegram_id(session, query.from_user.id)
        if isinstance(coach_user, Coach):
            coach_user = (
                session.query(Coach)
                .options(joinedload(Coach.sport_type_rel))
                .filter_by(id=coach_user.id)
                .first()
            )
        if not coach_user:
            await query.edit_message_text("❌ Пользователь не найден")
            return

        athlete_name = html.escape((athlete.full_name or "Спортсмен").strip())
        success = (
            f"{athlete_name} записан на "
            f"{start_date.strftime('%d.%m.%Y %H:%M')}"
        )
        await render_calendar_day_view(
            query,
            session,
            coach_user,
            start_date.date(),
            success_banner=success,
            skip_callback_answer=True,
        )
        return

    await show_subscription_card(
        update,
        context,
        override_query_data=f"subscription_{subscription.id}",
        skip_callback_answer=True,
    )


def _build_activation_calendar(
    subscription_id: int,
    sport_type: str,
    age_group: str,
    year: int,
    month: int
) -> InlineKeyboardMarkup:
    """
    Календарь выбора даты первой тренировки для активации абонемента.
    Визуально совпадает с "📅 Мой календарь" тренера:
    - строка дней недели
    - ровно 5 строк по 7 "квадратных" кнопок
    - навигация по месяцам + "Сегодня"
    Доступны для выбора только тренировочные дни по расписанию (сегодня и будущие даты).
    """
    schedule = _get_schedule(sport_type, age_group)
    training_days = set(schedule["days"]) if schedule else set()

    today = now_moscow().date()

    # Создаем календарь (monthcalendar возвращает недели с понедельника как первый день)
    cal = py_calendar.monthcalendar(year, month)

    keyboard = []

    # Строка дней недели над календарем (как в "Мой календарь")
    day_names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    keyboard.append([InlineKeyboardButton(f"{d}.", callback_data="act_ignore") for d in day_names])

    # Ровно 5 недель (как в "Мой календарь")
    weeks_to_show = cal[:5]
    while len(weeks_to_show) < 5:
        weeks_to_show.append([0, 0, 0, 0, 0, 0, 0])

    for week in weeks_to_show:
        row = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(" ", callback_data="act_ignore"))
                continue

            date_obj = datetime(year, month, day).date()
            weekday = date_obj.weekday()

            has_scheduled_training = weekday in training_days
            # Разрешаем выбирать только сегодня и будущие даты
            is_future_or_today = date_obj >= today
            enabled = has_scheduled_training and is_future_or_today

            # Тот же стиль подсветки, что и в "Мой календарь"
            if date_obj == today:
                btn_text = f"[{day:2d}]"
            elif has_scheduled_training:
                btn_text = f"({day:2d})"
            else:
                btn_text = f"{day:2d}"

            cb = f"act_date_{subscription_id}_{year}_{month}_{day}" if enabled else "act_ignore"
            row.append(InlineKeyboardButton(btn_text, callback_data=cb))

        keyboard.append(row)

    # Навигация
    prev_year, prev_month = year, month - 1
    next_year, next_month = year, month + 1
    if prev_month == 0:
        prev_month = 12
        prev_year -= 1
    if next_month == 13:
        next_month = 1
        next_year += 1

    keyboard.append([
        InlineKeyboardButton("◀️ Предыдущий", callback_data=f"act_cal_{subscription_id}_{prev_year}_{prev_month}"),
        InlineKeyboardButton("Следующий ▶️", callback_data=f"act_cal_{subscription_id}_{next_year}_{next_month}"),
    ])

    # Кнопка "Сегодня"
    now = now_moscow().date()
    if month != now.month or year != now.year:
        keyboard.append([
            InlineKeyboardButton("📅 Сегодня", callback_data=f"act_cal_{subscription_id}_{now.year}_{now.month}")
        ])

    # Навигация/выход
    keyboard.append([
        InlineKeyboardButton("🔙 Назад", callback_data=f"subscription_{subscription_id}"),
        InlineKeyboardButton("🏠 В меню", callback_data="back_to_menu_main"),
    ])

    return InlineKeyboardMarkup(keyboard)


def _build_activation_calendar_individual(
    subscription_id: int,
    year: int,
    month: int,
) -> InlineKeyboardMarkup:
    """Календарь для индивидуального абонемента: любой будущий календарный день (не только дни группы)."""
    today = now_moscow().date()
    cal = py_calendar.monthcalendar(year, month)
    keyboard = []
    day_names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    keyboard.append([InlineKeyboardButton(f"{d}.", callback_data="act_ignore") for d in day_names])
    weeks_to_show = cal[:5]
    while len(weeks_to_show) < 5:
        weeks_to_show.append([0, 0, 0, 0, 0, 0, 0])
    for week in weeks_to_show:
        row = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(" ", callback_data="act_ignore"))
                continue
            date_obj = datetime(year, month, day).date()
            enabled = date_obj >= today
            if date_obj == today:
                btn_text = f"[{day:2d}]"
            elif enabled:
                btn_text = f"({day:2d})"
            else:
                btn_text = f"{day:2d}"
            cb = f"act_date_{subscription_id}_{year}_{month}_{day}" if enabled else "act_ignore"
            row.append(InlineKeyboardButton(btn_text, callback_data=cb))
        keyboard.append(row)
    prev_year, prev_month = year, month - 1
    next_year, next_month = year, month + 1
    if prev_month == 0:
        prev_month = 12
        prev_year -= 1
    if next_month == 13:
        next_month = 1
        next_year += 1
    keyboard.append(
        [
            InlineKeyboardButton(
                "◀️ Предыдущий",
                callback_data=f"act_cal_{subscription_id}_{prev_year}_{prev_month}",
            ),
            InlineKeyboardButton(
                "Следующий ▶️",
                callback_data=f"act_cal_{subscription_id}_{next_year}_{next_month}",
            ),
        ]
    )
    nowd = now_moscow().date()
    if month != nowd.month or year != nowd.year:
        keyboard.append(
            [
                InlineKeyboardButton(
                    "📅 Сегодня",
                    callback_data=f"act_cal_{subscription_id}_{nowd.year}_{nowd.month}",
                )
            ]
        )
    keyboard.append(
        [
            InlineKeyboardButton("🔙 Назад", callback_data=f"subscription_{subscription_id}"),
            InlineKeyboardButton("🏠 В меню", callback_data="back_to_menu_main"),
        ]
    )
    return InlineKeyboardMarkup(keyboard)


def _build_individual_time_keyboard(
    session,
    subscription_id: int,
    coach_id: int,
    sport_type: str,
    year: int,
    month: int,
    day: int,
) -> Optional[InlineKeyboardMarkup]:
    """Свободные старты индивидуальной тренировки в выбранный день (шаг 30 мин)."""
    d = date(year, month, day)
    starts = iter_allowed_individual_starts(
        session,
        coach_id,
        sport_type,
        d,
        now_cutoff=now_moscow(),
    )
    if not starts:
        return None
    rows = []
    row = []
    for st in starts:
        label = st.strftime("%H:%M")
        cb = f"act_time_{subscription_id}_{training_datetime_compact(st)}"
        row.append(InlineKeyboardButton(label, callback_data=cb))
        if len(row) >= 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append(
        [
            InlineKeyboardButton(
                "🔙 К дате",
                callback_data=f"act_cal_{subscription_id}_{year}_{month}",
            )
        ]
    )
    return InlineKeyboardMarkup(rows)


async def handle_activation_calendar_nav(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Навигация по календарю выбора даты активации."""
    query = update.callback_query
    await query.answer()

    # act_cal_{subscription_id}_{YYYY}_{MM}
    parts = query.data.split("_")
    subscription_id = int(parts[2])
    year = int(parts[3])
    month = int(parts[4])

    with get_db_session() as session:
        user = get_user_by_telegram_id(session, query.from_user.id)
        if not is_staff(user):
            await query.edit_message_text("❌ У вас нет доступа")
            return

        subscription = session.query(Subscription).filter_by(id=subscription_id).first()
        if not subscription:
            await query.edit_message_text("❌ Абонемент не найден")
            return

        athlete = subscription.athlete
        if isinstance(user, Coach) and athlete.created_by != user.id:
            await query.edit_message_text("❌ Вы не можете изменять этот абонемент")
            return

        sport_type = subscription.sport_type or athlete.sport_type
        age_group = athlete.age_group

        if subscription.subscription_type == "individual":
            reply_markup = _build_activation_calendar_individual(subscription_id, year, month)
        else:
            reply_markup = _build_activation_calendar(
                subscription_id, sport_type, age_group, year, month
            )
        await query.edit_message_reply_markup(reply_markup=reply_markup)


async def handle_activation_date_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выбор даты первой тренировки для активации абонемента."""
    query = update.callback_query
    await query.answer()

    # act_date_{subscription_id}_{YYYY}_{MM}_{DD}
    parts = query.data.split("_")
    subscription_id = int(parts[2])
    year = int(parts[3])
    month = int(parts[4])
    day = int(parts[5])

    try:
        with get_db_session() as session:
            user = get_user_by_telegram_id(session, query.from_user.id)
            if not is_staff(user):
                await query.edit_message_text("❌ У вас нет доступа")
                return

            subscription = session.query(Subscription).filter_by(id=subscription_id).first()
            if not subscription:
                await query.edit_message_text("❌ Абонемент не найден")
                return

            athlete = subscription.athlete
            if isinstance(user, Coach) and athlete.created_by != user.id:
                await query.edit_message_text("❌ Вы не можете изменять этот абонемент")
                return

            sport_type = subscription.sport_type or athlete.sport_type
            age_group = athlete.age_group

            if subscription.subscription_type not in ("monthly", "single", "individual"):
                await query.edit_message_text("❌ Сначала выберите тип абонемента.")
                return

            coach_selected_date = datetime(year, month, day, 0, 0, 0)

            if subscription.subscription_type == "individual":
                coach_id = subscription.responsible_coach_id or athlete.created_by
                if not coach_id:
                    await query.edit_message_text(
                        "❌ Для индивидуального абонемента не указан тренер (ответственный / создавший спортсмена)."
                    )
                    return
                noon = datetime(year, month, day, 12, 0, 0)
                if is_training_in_global_freeze(session, noon):
                    shifted = find_next_non_frozen_calendar_date(session, noon.date())
                    sh_noon = datetime(shifted.year, shifted.month, shifted.day, 12, 0, 0)
                    freeze = (
                        session.query(GlobalFreeze)
                        .filter(
                            GlobalFreeze.is_active == True,
                            GlobalFreeze.start_date <= noon,
                            GlobalFreeze.end_date >= noon,
                        )
                        .order_by(GlobalFreeze.end_date.desc())
                        .first()
                    )
                    confirm_cb = f"act_ishift_{subscription_id}_{training_datetime_compact(sh_noon)}"
                    cancel_cb = f"act_shift_cancel_{subscription_id}"
                    keyboard = InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton(
                                    "✅ Подтвердить сдвиг дня", callback_data=confirm_cb
                                ),
                                InlineKeyboardButton(
                                    "❌ Выбрать другую дату", callback_data=cancel_cb
                                ),
                            ]
                        ]
                    )
                    freeze_label = (
                        f"{freeze.start_date.strftime('%d.%m.%Y')} — {freeze.end_date.strftime('%d.%m.%Y')}"
                        if freeze
                        else "активной массовой заморозки"
                    )
                    await query.edit_message_text(
                        "⚠️ Выбранный день попадает в период массовой заморозки.\n\n"
                        f"Период заморозки: <b>{freeze_label}</b>\n"
                        f"Предлагаемый день: <b>{shifted.strftime('%d.%m.%Y')}</b>\n\n"
                        "Подтвердить и выбрать время тренировки?",
                        parse_mode="HTML",
                        reply_markup=keyboard,
                    )
                    return
                time_kb = _build_individual_time_keyboard(
                    session, subscription_id, coach_id, sport_type, year, month, day
                )
                if not time_kb:
                    await query.edit_message_text(
                        "❌ В этот день нет свободного слота без пересечения с занятиями тренера. "
                        "Выберите другую дату.",
                        reply_markup=_build_activation_calendar_individual(
                            subscription_id, year, month
                        ),
                    )
                    return
                await query.edit_message_text(
                    "⏰ Выберите <b>время начала</b> индивидуальной тренировки (длительность 1 ч):",
                    parse_mode="HTML",
                    reply_markup=time_kb,
                )
                return

            schedule = _get_schedule(sport_type, age_group)
            if not schedule:
                await query.edit_message_text(
                    "❌ Расписание для этой группы не найдено. Обратитесь к администратору."
                )
                return

            from database.db_utils import _find_nearest_training_date

            start_date = _find_nearest_training_date(coach_selected_date, sport_type, age_group)

            if is_training_in_global_freeze(session, start_date):
                freeze = (
                    session.query(GlobalFreeze)
                    .filter(
                        GlobalFreeze.is_active == True,
                        GlobalFreeze.start_date <= start_date,
                        GlobalFreeze.end_date >= start_date,
                    )
                    .order_by(GlobalFreeze.end_date.desc())
                    .first()
                )
                shifted_start = find_next_non_frozen_training_date(
                    session,
                    (freeze.end_date + timedelta(seconds=1)) if freeze else (start_date + timedelta(days=1)),
                    sport_type,
                    age_group,
                )
                confirm_cb = f"act_shift_confirm_{subscription_id}_{training_datetime_compact(shifted_start)}"
                cancel_cb = f"act_shift_cancel_{subscription_id}"
                keyboard = InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton("✅ Подтвердить сдвиг", callback_data=confirm_cb),
                            InlineKeyboardButton("❌ Выбрать другую дату", callback_data=cancel_cb),
                        ]
                    ]
                )
                freeze_label = (
                    f"{freeze.start_date.strftime('%d.%m.%Y')} — {freeze.end_date.strftime('%d.%m.%Y')}"
                    if freeze
                    else "активной массовой заморозки"
                )
                await query.edit_message_text(
                    "⚠️ Выбранная первая тренировка попадает в период массовой заморозки.\n\n"
                    f"Период заморозки: <b>{freeze_label}</b>\n"
                    f"Предлагаемая новая дата старта: <b>{shifted_start.strftime('%d.%m.%Y %H:%M')}</b>\n\n"
                    "Подтвердить сдвиг и продолжить активацию?",
                    parse_mode="HTML",
                    reply_markup=keyboard,
                )
                return

            await _finalize_subscription_activation(
                update, context, query, session, subscription, athlete, start_date
            )
    except Exception as e:
        logger.error(f"❌ ОШИБКА ВЫБОРА ДАТЫ АКТИВАЦИИ: {e}", exc_info=True)
        await query.edit_message_text("❌ Ошибка при активации абонемента")


async def handle_activation_individual_shift_confirm(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    """Подтверждение сдвига календарного дня для индивидуальной активации → выбор времени."""
    query = update.callback_query
    await query.answer()
    m = re.match(r"^act_ishift_(\d+)_(\d{12})$", (query.data or "").strip())
    if not m:
        await query.edit_message_text("❌ Некорректная кнопка.")
        return
    subscription_id = int(m.group(1))
    shifted_noon = parse_training_datetime_compact(m.group(2))
    if not shifted_noon:
        await query.edit_message_text("❌ Некорректная дата в кнопке.")
        return
    try:
        with get_db_session() as session:
            user = get_user_by_telegram_id(session, query.from_user.id)
            if not is_staff(user):
                await query.edit_message_text("❌ У вас нет доступа")
                return
            subscription = session.query(Subscription).filter_by(id=subscription_id).first()
            if not subscription:
                await query.edit_message_text("❌ Абонемент не найден")
                return
            athlete = subscription.athlete
            if isinstance(user, Coach) and athlete.created_by != user.id:
                await query.edit_message_text("❌ Вы не можете изменять этот абонемент")
                return
            if subscription.subscription_type != "individual":
                await query.edit_message_text("❌ Некорректный тип абонемента.")
                return
            if is_training_in_global_freeze(session, shifted_noon):
                await query.edit_message_text(
                    "❌ Период заморозки изменился. Вернитесь к календарю и выберите дату снова."
                )
                return
            sport_type = subscription.sport_type or athlete.sport_type
            coach_id = subscription.responsible_coach_id or athlete.created_by
            if not coach_id:
                await query.edit_message_text("❌ Не указан тренер для индивидуального абонемента.")
                return
            time_kb = _build_individual_time_keyboard(
                session,
                subscription_id,
                coach_id,
                sport_type,
                shifted_noon.year,
                shifted_noon.month,
                shifted_noon.day,
            )
            if not time_kb:
                await query.edit_message_text(
                    "❌ В этот день нет свободного слота. Выберите другую дату.",
                    reply_markup=_build_activation_calendar_individual(
                        subscription_id, shifted_noon.year, shifted_noon.month
                    ),
                )
                return
            await query.edit_message_text(
                "⏰ Выберите <b>время начала</b> индивидуальной тренировки (длительность 1 ч):",
                parse_mode="HTML",
                reply_markup=time_kb,
            )
    except Exception as e:
        logger.error("❌ ОШИБКА act_ishift: %s", e, exc_info=True)
        await query.edit_message_text("❌ Ошибка при активации абонемента")


async def handle_activation_time_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выбор времени начала индивидуальной тренировки при активации."""
    query = update.callback_query
    await query.answer()
    m = re.match(r"^act_time_(\d+)_(\d{12})$", (query.data or "").strip())
    if not m:
        await query.edit_message_text("❌ Некорректная кнопка.")
        return
    subscription_id = int(m.group(1))
    start_date = parse_training_datetime_compact(m.group(2))
    if not start_date:
        await query.edit_message_text("❌ Некорректное время в кнопке.")
        return
    try:
        with get_db_session() as session:
            user = get_user_by_telegram_id(session, query.from_user.id)
            if not is_staff(user):
                await query.edit_message_text("❌ У вас нет доступа")
                return
            subscription = session.query(Subscription).filter_by(id=subscription_id).first()
            if not subscription or subscription.subscription_type != "individual":
                await query.edit_message_text("❌ Абонемент не найден или тип не индивидуальный.")
                return
            athlete = subscription.athlete
            if isinstance(user, Coach) and athlete.created_by != user.id:
                await query.edit_message_text("❌ Вы не можете изменять этот абонемент")
                return
            sport_type = subscription.sport_type or athlete.sport_type
            coach_id = subscription.responsible_coach_id or athlete.created_by
            if not coach_id:
                await query.edit_message_text("❌ Не указан тренер.")
                return
            now = now_moscow()
            if now > start_date + ACTIVATION_GRACE_AFTER_START:
                grace_min = int(ACTIVATION_GRACE_AFTER_START.total_seconds() // 60)
                await query.edit_message_text(
                    "❌ Время для выбора этого слота истекло "
                    f"(запас после начала {grace_min} мин). "
                    "Выберите другое время.",
                    reply_markup=_build_individual_time_keyboard(
                        session,
                        subscription_id,
                        coach_id,
                        sport_type,
                        start_date.year,
                        start_date.month,
                        start_date.day,
                    ),
                )
                return
            if is_training_in_global_freeze(session, start_date):
                await query.edit_message_text(
                    "❌ Выбранное время попадает в массовую заморозку. Выберите другое время."
                )
                return
            dup_same_athlete = (
                session.query(Subscription.id)
                .filter(
                    Subscription.athlete_id == athlete.id,
                    Subscription.subscription_type == "individual",
                    Subscription.is_active.is_(True),
                    Subscription.start_date == start_date,
                    Subscription.id != subscription.id,
                )
                .first()
            )
            if dup_same_athlete:
                await query.edit_message_text(
                    "❌ У спортсмена уже есть индивидуальная запись на это время. "
                    "Выберите другой слот.",
                    reply_markup=_build_individual_time_keyboard(
                        session,
                        subscription_id,
                        coach_id,
                        sport_type,
                        start_date.year,
                        start_date.month,
                        start_date.day,
                    ),
                )
                return
            if individual_slot_conflicts(session, coach_id, sport_type, start_date):
                await query.edit_message_text(
                    "❌ Слот занят или пересекается с групповой/индивидуальной тренировкой. "
                    "Выберите другое время.",
                    reply_markup=_build_individual_time_keyboard(
                        session,
                        subscription_id,
                        coach_id,
                        sport_type,
                        start_date.year,
                        start_date.month,
                        start_date.day,
                    ),
                )
                return
            await _finalize_subscription_activation(
                update, context, query, session, subscription, athlete, start_date
            )
    except Exception as e:
        logger.error("❌ ОШИБКА act_time: %s", e, exc_info=True)
        await query.edit_message_text("❌ Ошибка при активации абонемента")


async def handle_activation_ignore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Игнор-кнопка для календаря (пустые клетки/дни недели)."""
    query = update.callback_query
    await query.answer()


async def handle_activation_shift_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Подтверждение сдвига даты активации за пределы массовой заморозки."""
    query = update.callback_query
    await query.answer()

    m = re.match(r"^act_shift_confirm_(\d+)_(\d{12})$", (query.data or "").strip())
    if not m:
        await query.edit_message_text("❌ Некорректная кнопка.")
        return

    subscription_id = int(m.group(1))
    shifted_start = parse_training_datetime_compact(m.group(2))
    if not shifted_start:
        await query.edit_message_text("❌ Некорректная дата в кнопке.")
        return

    try:
        with get_db_session() as session:
            user = get_user_by_telegram_id(session, query.from_user.id)
            if not is_staff(user):
                await query.edit_message_text("❌ У вас нет доступа")
                return

            subscription = session.query(Subscription).filter_by(id=subscription_id).first()
            if not subscription:
                await query.edit_message_text("❌ Абонемент не найден")
                return

            if subscription.subscription_type == "individual":
                await query.edit_message_text(
                    "❌ Для индивидуального абонемента выберите дату и время в календаре активации."
                )
                return

            athlete = subscription.athlete
            if isinstance(user, Coach) and athlete.created_by != user.id:
                await query.edit_message_text("❌ Вы не можете изменять этот абонемент")
                return

            if is_training_in_global_freeze(session, shifted_start):
                await query.edit_message_text(
                    "❌ Период заморозки изменился. Вернитесь к календарю и выберите дату снова."
                )
                return

            await _finalize_subscription_activation(
                update, context, query, session, subscription, athlete, shifted_start
            )
    except Exception as e:
        logger.error("❌ ОШИБКА ПОДТВЕРЖДЕНИЯ СДВИГА АКТИВАЦИИ: %s", e, exc_info=True)
        await query.edit_message_text("❌ Ошибка при активации абонемента")


async def handle_activation_shift_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отмена сдвига: календарь активации или карточка абонемента."""
    query = update.callback_query
    m = re.match(r"^act_shift_cancel_(\d+)$", (query.data or "").strip())
    if not m:
        await query.answer()
        return

    subscription_id = int(m.group(1))
    try:
        with get_db_session() as session:
            user = get_user_by_telegram_id(session, query.from_user.id)
            if not is_staff(user):
                await query.answer()
                await query.edit_message_text("❌ У вас нет доступа")
                return

            subscription = session.query(Subscription).filter_by(id=subscription_id).first()
            if not subscription:
                await query.answer()
                await query.edit_message_text("❌ Абонемент не найден")
                return

            athlete = subscription.athlete
            if isinstance(user, Coach) and athlete.created_by != user.id:
                await query.answer()
                await query.edit_message_text("❌ Вы не можете изменять этот абонемент")
                return

            if not subscription.is_active and subscription.start_date is None:
                await query.answer()
                now = now_moscow()
                sport_type = subscription.sport_type or athlete.sport_type
                if subscription.subscription_type == "individual":
                    reply_markup = _build_activation_calendar_individual(
                        subscription.id, now.year, now.month
                    )
                else:
                    schedule = _get_schedule(sport_type, athlete.age_group)
                    if not schedule:
                        await query.edit_message_text(
                            "❌ Расписание для этой группы не найдено. Обратитесь к администратору."
                        )
                        return
                    reply_markup = _build_activation_calendar(
                        subscription.id, sport_type, athlete.age_group, now.year, now.month
                    )
                sub_type_ru = _format_subscription_type_ru(subscription.subscription_type)
                await query.edit_message_text(
                    f"👤 <b>{html.escape(athlete.full_name)}</b>\n\n"
                    f"🎫 <b>АКТИВАЦИЯ АБОНЕМЕНТА</b>\n\n"
                    f"Тип: <b>{sub_type_ru}</b>\n\n"
                    f"Выберите дату <b>первой тренировки</b> (она будет датой активации):",
                    reply_markup=reply_markup,
                    parse_mode="HTML",
                )
            else:
                await show_subscription_card(
                    update,
                    context,
                    override_query_data=f"subscription_{subscription.id}",
                    skip_callback_answer=True,
                )
    except Exception as e:
        logger.error("❌ ОШИБКА ОТМЕНЫ СДВИГА АКТИВАЦИИ: %s", e, exc_info=True)
        try:
            await query.answer()
        except Exception:
            pass
        await query.edit_message_text("❌ Ошибка")


async def show_athlete_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать карточку спортсмена"""
    query = update.callback_query
    if query:
        await query.answer()
        user_id = query.from_user.id
        # Получаем athlete_id из callback_data: athlete_123
        athlete_id = int(query.data.replace("athlete_", ""))
    else:
        user_id = update.effective_user.id
        # Получаем athlete_id из аргументов команды
        if context.args and len(context.args) > 0:
            try:
                athlete_id = int(context.args[0])
            except ValueError:
                await update.message.reply_text("❌ Неверный ID спортсмена")
                return
        else:
            await update.message.reply_text("❌ Укажите ID спортсмена: /card <ID>")
            return

    try:
        with get_db_session() as session:
            user = get_user_by_telegram_id(session, user_id)

            if not is_staff(user):
                if query:
                    await query.edit_message_text("❌ У вас нет доступа")
                else:
                    await update.message.reply_text("❌ У вас нет доступа")
                return

            # Получаем информацию для карточки
            # Если тренер смотрит карточку, выбираем абонемент по его виду спорта
            athlete = session.query(Athlete).filter_by(id=athlete_id).first()
            if not athlete:
                if query:
                    await query.edit_message_text("❌ Спортсмен не найден")
                else:
                    await update.message.reply_text("❌ Спортсмен не найден")
                return
        
            coach_sport = coach_sport_type_name(user)
            card_info = get_athlete_card_info(session, athlete_id, preferred_sport_type=coach_sport)
            if not card_info:
                if query:
                    await query.edit_message_text("❌ Ошибка при загрузке карточки")
                else:
                    await update.message.reply_text("❌ Ошибка при загрузке карточки")
                return
        
            stats = card_info['stats']

            # Проверяем права (тренер может видеть только своих спортсменов)
            if isinstance(user, Coach) and athlete.created_by != user.id:
                if query:
                    await query.edit_message_text("❌ Вы не можете просматривать этого спортсмена")
                else:
                    await update.message.reply_text("❌ Вы не можете просматривать этого спортсмена")
                return

            # Формируем сообщение (только базовая информация)
            message = f"👤 <b>КАРТОЧКА СПОРТСМЕНА</b>\n\n"
            message += f"<b>{html.escape(athlete.full_name)}</b>\n"
            message += f"📞 {athlete.phone or 'Не указан'}\n"
        
            # Дата рождения
            if athlete.birth_date:
                birth_date_str = athlete.birth_date.strftime('%d.%m.%Y')
                message += f"🎂 Дата рождения: {birth_date_str}\n"
            else:
                message += f"🎂 Дата рождения: Не указана\n"
        
            # Дата регистрации в зале
            if athlete.created_at:
                registration_date_str = athlete.created_at.strftime('%d.%m.%Y')
                message += f"📅 Дата регистрации: {registration_date_str}\n"
        
            message += f"\n"
        
            # Медицинская информация
            message += f"<b>🏥 МЕДИЦИНСКАЯ ИНФОРМАЦИЯ</b>\n"
            medical_info = athlete.medical_info or 'Не указана'
            message += f"{html.escape(medical_info)}"

            # Создаем инлайн клавиатуру
            keyboard = []

            # Первый ряд: основные действия
            keyboard.append([
                InlineKeyboardButton("🎫 Абонемент", callback_data=f"subscription_athlete_{athlete_id}"),
                InlineKeyboardButton("📅 Посещения", callback_data=f"visits_{athlete_id}")
            ])

            # Второй ряд: навигация
            keyboard.append([
                InlineKeyboardButton("📋 К списку спортсменов", callback_data="back_to_list"),
            ])

            # Третий ряд: редактирование
            keyboard.append([
                InlineKeyboardButton("✏️ Редактировать", callback_data=f"edit_{athlete_id}"),
            ])

            reply_markup = InlineKeyboardMarkup(keyboard)

            if query:
                await query.edit_message_text(
                    message,
                    reply_markup=reply_markup,
                    parse_mode='HTML'
                )
            else:
                await update.message.reply_text(
                    message,
                    reply_markup=reply_markup,
                    parse_mode='HTML'
                )

    except Exception as e:
        logger.error(f"❌ ОШИБКА ПРИ ПОКАЗЕ КАРТОЧКИ: {e}")
        if query:
            await query.edit_message_text("❌ Ошибка при загрузке карточки")
        else:
            await update.message.reply_text("❌ Ошибка при загрузке карточки")


async def show_subscription_card(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    override_query_data: str = None,
    *,
    skip_callback_answer: bool = False,
):
    """Показать детальную информацию об абонементе или список абонементов"""
    query = update.callback_query
    if query and not skip_callback_answer:
        await query.answer()

    # Парсим callback_data: subscription_athlete_123 или subscription_123
    query_data = override_query_data or query.data
    callback_data = query_data.replace("subscription_", "")
    if callback_data.startswith("athlete_"):
        athlete_id = int(callback_data.replace("athlete_", ""))
        subscription_id = None
    else:
        # Если передан subscription_id напрямую
        try:
            subscription_id = int(callback_data)
            subscription = None
        except ValueError:
            athlete_id = int(callback_data)
            subscription_id = None

    try:
        with get_db_session() as session:
            user = get_user_by_telegram_id(session, query.from_user.id)

            if not is_staff(user):
                await query.edit_message_text("❌ У вас нет доступа")
                return

            # Если передан subscription_id, получаем абонемент напрямую
            if subscription_id:
                subscription = session.query(Subscription).filter_by(id=subscription_id).first()
                if not subscription:
                    await query.edit_message_text("❌ Абонемент не найден")
                    return
                athlete_id = subscription.athlete_id
                athlete = subscription.athlete
            else:
                coach_sport_hint = coach_sport_type_name(user)
                card_info = get_athlete_card_info(
                    session, athlete_id, preferred_sport_type=coach_sport_hint
                )
                if not card_info:
                    await query.edit_message_text("❌ Спортсмен не найден")
                    return
                athlete = card_info['athlete']
                subscription = card_info['subscription']

                # Если для выбранного контекста (вид спорта тренера / первый активный) абонемента нет —
                # подбираем последний по дате, в т.ч. неактивный, чтобы не уводить сразу на список.
                if not subscription:
                    all_subs = session.query(Subscription).filter_by(athlete_id=athlete_id).all()
                    if all_subs:
                        def _sub_sort_key(s: Subscription):
                            return (s.created_at or datetime.min, s.id)

                        all_subs_sorted = sorted(all_subs, key=_sub_sort_key, reverse=True)

                        if isinstance(user, Coach):
                            coach_sport_type = coach_sport_type_name(user)
                            if coach_sport_type:
                                matching = [s for s in all_subs_sorted if s.sport_type == coach_sport_type]
                                if matching:
                                    active_matching = [s for s in matching if s.is_active]
                                    subscription = sorted(active_matching or matching, key=_sub_sort_key, reverse=True)[0]
                                else:
                                    subscription = all_subs_sorted[0]
                            else:
                                subscription = all_subs_sorted[0]
                        else:
                            subscription = all_subs_sorted[0]

            # Проверяем права
            if isinstance(user, Coach) and athlete.created_by != user.id:
                await query.edit_message_text("❌ Вы не можете просматривать этого спортсмена")
                return

            expire_stale_subscription_freezes(session, athlete_id=athlete.id, commit=True)
            session.refresh(athlete)
            if subscription:
                session.refresh(subscription)

            # Для маршрута subscription_athlete_* при одновременных активных
            # group + individual показываем раздельный экран выбора, а не одну карточку.
            if subscription_id is None:
                coach_sport_type = coach_sport_type_name(user)
                active_subs = [s for s in athlete.subscriptions if s.is_active]
                if coach_sport_type:
                    active_subs = [s for s in active_subs if (s.sport_type or "").strip() == coach_sport_type]

                active_individual_subs = [
                    s for s in active_subs if _is_individual_subscription(s)
                ]
                upcoming_individual_subs = sorted(
                    [
                        s
                        for s in active_individual_subs
                        if not _individual_subscription_is_past_or_completed(s)
                    ],
                    key=lambda s: (s.start_date or datetime.min, s.id or 0),
                )
                group_subs = sorted(
                    [s for s in active_subs if not _is_individual_subscription(s)],
                    key=lambda s: -(s.id or 0),
                )
                need_picker = _subscription_picker_should_show(
                    group_subs,
                    upcoming_individual_subs,
                )
                if need_picker:
                    message = f"👤 <b>{html.escape(athlete.full_name)}</b>\n\n"
                    message += "🎫 <b>АБОНЕМЕНТЫ</b>\n\n"
                    message += "Выберите активный абонемент:\n\n"
                    if upcoming_individual_subs:
                        message += (
                            f"Ближайшие индивидуальные: "
                            f"<b>{len(upcoming_individual_subs)}</b>\n\n"
                        )

                    keyboard = []
                    for sub in group_subs:
                        stype = _format_subscription_type_ru(sub.subscription_type)
                        status_icon = _status_icon_from_status_text(
                            _format_subscription_status_ui(sub)
                        )
                        keyboard.append([
                            InlineKeyboardButton(
                                f"{status_icon} Групповые | {stype}",
                                callback_data=f"subscription_{sub.id}",
                            )
                        ])
                    for sub in upcoming_individual_subs:
                        keyboard.append([
                            InlineKeyboardButton(
                                _individual_subscription_button_label(sub, session),
                                callback_data=f"subscription_{sub.id}",
                            )
                        ])
                    keyboard.append([
                        InlineKeyboardButton(
                            "📜 Архив",
                            callback_data=_subscription_history_list_callback(
                                athlete.id,
                                athlete_self=False,
                            ),
                        )
                    ])
                    keyboard.append([
                        InlineKeyboardButton(
                            "🔙 Назад к карточке", callback_data=f"athlete_{athlete.id}"
                        )
                    ])
                    await query.edit_message_text(
                        message,
                        reply_markup=InlineKeyboardMarkup(keyboard),
                        parse_mode="HTML",
                    )
                    return

            # Если нет конкретного абонемента, показываем список всех абонементов
            if not subscription:
                from services.subscription_service import SubscriptionService
                all_subscriptions = SubscriptionService.get_athlete_subscriptions(session, athlete_id)
            
                # Проверяем, есть ли абонемент по виду спорта текущего тренера
                coach_sport_sub = None
                coach_sport_type = coach_sport_type_name(user)
                if isinstance(user, Coach) and coach_sport_type:
                    coach_sport_sub = next((s for s in all_subscriptions if s.sport_type == coach_sport_type), None)
            
                if not all_subscriptions:
                    # Если нет абонементов, показываем кнопку создания абонемента
                    keyboard = [
                        [InlineKeyboardButton("✅ Создать абонемент", callback_data=f"activate_sub_new_{athlete_id}")],
                        [InlineKeyboardButton("🔙 Назад к карточке", callback_data=f"athlete_{athlete_id}")]
                    ]
                    reply_markup = InlineKeyboardMarkup(keyboard)

                    await query.edit_message_text(
                        f"👤 <b>{html.escape(athlete.full_name)}</b>\n\n"
                        f"❌ У спортсмена нет абонемента.\n\n"
                        f"Нажмите 'Создать абонемент' для создания и активации абонемента.",
                        reply_markup=reply_markup,
                        parse_mode='HTML'
                    )
                    return
            
                # Если есть абонементы, но нет абонемента по виду спорта тренера - предлагаем создать
                if isinstance(user, Coach) and coach_sport_type and not coach_sport_sub:
                    # Добавляем кнопку создания абонемента по виду спорта тренера
                    message = f"👤 <b>{html.escape(athlete.full_name)}</b>\n\n"
                    message += f"🎫 <b>АБОНЕМЕНТЫ</b>\n\n"
                    message += f"⚠️ У спортсмена нет абонемента по виду спорта <b>{coach_sport_type}</b>\n\n"
                    message += f"Выберите существующий абонемент или создайте новый:\n\n"
                
                    keyboard = []
                    for sub in sorted(all_subscriptions, key=lambda s: s.created_at or datetime.min, reverse=True):
                        status_icon = _status_icon_from_status_text(_format_subscription_status_ui(sub))
                        sport_type_display = sub.sport_type or "—"
                        sub_type = _format_subscription_type_ru(sub.subscription_type)
                        start_date_str = sub.start_date.strftime('%d.%m.%Y') if sub.start_date else "—"
                    
                        button_text = f"{status_icon} {sport_type_display} | {sub_type} | {start_date_str}"
                        if len(button_text) > 64:
                            button_text = f"{status_icon} {sport_type_display} | {sub_type}"
                    
                        keyboard.append([
                            InlineKeyboardButton(button_text, callback_data=f"subscription_{sub.id}")
                        ])
                
                    # Кнопка создания нового абонемента по виду спорта тренера
                    keyboard.append([
                        InlineKeyboardButton(f"✅ Создать абонемент ({coach_sport_type})", callback_data=f"activate_sub_new_{athlete_id}")
                    ])
                    keyboard.append([
                        InlineKeyboardButton("📜 Архив", callback_data=f"subscription_history_{athlete_id}")
                    ])
                    keyboard.append([
                        InlineKeyboardButton("🔙 Назад к карточке", callback_data=f"athlete_{athlete_id}")
                    ])
                
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    await query.edit_message_text(message, reply_markup=reply_markup, parse_mode='HTML')
                    return
            
                # Проверяем, есть ли абонемент по виду спорта текущего тренера
                coach_sport_sub = None
                coach_sport_type = coach_sport_type_name(user)
                if isinstance(user, Coach) and coach_sport_type:
                    coach_sport_sub = next((s for s in all_subscriptions if s.sport_type == coach_sport_type), None)
            
                # Показываем список абонементов
                message = f"👤 <b>{html.escape(athlete.full_name)}</b>\n\n"
                message += f"🎫 <b>АБОНЕМЕНТЫ</b>\n\n"
            
                keyboard = []
                for sub in sorted(all_subscriptions, key=lambda s: s.created_at or datetime.min, reverse=True):
                    status_icon = _status_icon_from_status_text(_format_subscription_status_ui(sub))
                    sport_type_display = sub.sport_type or "—"
                    sub_type = _format_subscription_type_ru(sub.subscription_type)
                    start_date_str = sub.start_date.strftime('%d.%m.%Y') if sub.start_date else "—"
                
                    button_text = f"{status_icon} {sport_type_display} | {sub_type} | {start_date_str}"
                    if len(button_text) > 64:
                        button_text = f"{status_icon} {sport_type_display} | {sub_type}"
                
                    keyboard.append([
                        InlineKeyboardButton(button_text, callback_data=f"subscription_{sub.id}")
                    ])
            
                # Если тренер смотрит и у спортсмена нет абонемента по его виду спорта - предлагаем создать
                if isinstance(user, Coach) and coach_sport_type and not coach_sport_sub:
                    keyboard.append([
                        InlineKeyboardButton(f"✅ Создать абонемент ({coach_sport_type})", callback_data=f"activate_sub_new_{athlete_id}")
                    ])
            
                keyboard.append([
                    InlineKeyboardButton("📜 Архив", callback_data=f"subscription_history_{athlete_id}")
                ])
                keyboard.append([
                    InlineKeyboardButton("🔙 Назад к карточке", callback_data=f"athlete_{athlete_id}")
                ])
            
                reply_markup = InlineKeyboardMarkup(keyboard)
                await query.edit_message_text(message, reply_markup=reply_markup, parse_mode='HTML')
                return

            # Автоматически списываем тренировки по расписанию для активного абонемента
            if subscription.is_active:
                from database.db_utils import auto_deduct_daily_trainings, migrate_existing_subscription
                migrate_existing_subscription(session, subscription.id)
                auto_deduct_daily_trainings(session)

            # Получаем возрастную группу спортсмена
            age_group_display = format_age_group_label(athlete.age_group)

            # Формируем сообщение (только необходимая информация)
            message = f"🎫 <b>АБОНЕМЕНТ</b>\n\n"
            message += f"👤 <b>{html.escape(athlete.full_name)}</b>\n\n"

            message += f"<b>📋 ИНФОРМАЦИЯ</b>\n"
            message += f"• Вид спорта: {subscription.sport_type or '—'}\n"
            message += f"• Возрастная группа: {age_group_display}\n"
            dk = getattr(subscription, "discipline_key", None)
            message += f"• Формат занятий: {format_training_format_ru(dk)}\n"

            sub_type_display = _format_subscription_type_ru(subscription.subscription_type)
            message += f"• Тип абонемента: {sub_type_display}\n"
            if (subscription.subscription_type or "").strip().lower() == "individual":
                if subscription.is_active and subscription.start_date:
                    message += (
                        f"• Слот тренировки: <b>{_format_dt(subscription.start_date)}</b>\n"
                    )

            # Статус
            status_display = _format_subscription_status_ui(subscription)
            message += f"• Статус: {status_display}\n"

            message = _append_subscription_freeze_ui_lines(message, subscription)

            # Даты (до активации не показываем "дату начала", даже если она случайно заполнена в БД)
            start_date_str = _format_dt(subscription.start_date) if (subscription.is_active and subscription.start_date) else "—"
            message += f"• Дата начала: {start_date_str}\n"
        
            if subscription.is_active and subscription.end_date:
                end_date_str = _format_dt(subscription.end_date)
                message += f"• Дата окончания: {end_date_str}{_freeze_note(subscription)}\n"
            
                # Осталось тренировок (пересчитываем на лету для актуальности)
                if subscription.trainings_total is None:
                    message += f"• Осталось тренировок: —\n"
                else:
                    actual_remaining = calculate_actual_trainings_remaining(session, subscription)
                    if actual_remaining is not None:
                        message += f"• Осталось тренировок: {actual_remaining}/{subscription.trainings_total}\n"
                        if sync_subscription_trainings_remaining(session, subscription):
                            session.commit()
                    else:
                        message += f"• Осталось тренировок: {subscription.trainings_remaining}/{subscription.trainings_total}\n"
            else:
                message += f"• Дата окончания: —\n"
                message += f"• Осталось тренировок: —\n"

            # Создаем инлайн клавиатуру
            keyboard = []

            # Кнопка активации (только если абонемент неактивен)
            if not subscription.is_active:
                keyboard.append([
                    InlineKeyboardButton("✅ Активировать", callback_data=f"activate_sub_{subscription.id}")
                ])
            elif (subscription.subscription_type or "").strip().lower() == "individual":
                n_ind = sum(
                    1
                    for s in athlete.subscriptions
                    if s.is_active
                    and _is_individual_subscription(s)
                    and not _individual_subscription_is_past_or_completed(s)
                )
                btn_label = "➕ Ещё индивидуальная тренировка"
                if n_ind > 1:
                    btn_label = f"➕ Ещё индивидуальная ({n_ind} активных)"
                keyboard.append([
                    InlineKeyboardButton(
                        btn_label,
                        callback_data=f"activate_sub_add_individual_{subscription.id}",
                    )
                ])
            else:
                # Из действующего группового/разового открываем создание individual-направления
                # без ручных деактиваций.
                keyboard.append([
                    InlineKeyboardButton(
                        "➕ Индивидуальная тренировка",
                        callback_data=f"activate_sub_add_individual_{subscription.id}",
                    )
                ])
        
            # Заморозка/разморозка — на уровне спортсмена (все активные абонементы)
            any_active_frozen = _athlete_has_active_frozen_subscription(athlete)
            if subscription.is_active:
                if any_active_frozen:
                    keyboard.append([
                        InlineKeyboardButton(
                            "❄️ Разморозить",
                            callback_data=f"unfreeze_athlete_{athlete.id}_{subscription.id}",
                        )
                    ])
                else:
                    keyboard.append([
                        InlineKeyboardButton(
                            "❄️ Заморозить",
                            callback_data=f"freeze_athlete_{athlete.id}_{subscription.id}",
                        )
                    ])

            keyboard.append([
                InlineKeyboardButton(
                    "📜 Архив",
                    callback_data=_subscription_history_list_callback(
                        athlete_id,
                        athlete_self=False,
                    ),
                )
            ])

            keyboard.append([
                InlineKeyboardButton("🔙 Назад к карточке", callback_data=f"athlete_{athlete_id}")
            ])

            reply_markup = InlineKeyboardMarkup(keyboard)

            await query.edit_message_text(
                message,
                reply_markup=reply_markup,
                parse_mode='HTML'
            )

    except Exception as e:
        logger.error(f"❌ ОШИБКА ПРИ ПОКАЗЕ АБОНЕМЕНТА: {e}")
        await query.edit_message_text("❌ Ошибка при загрузке абонемента")


async def handle_back_to_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Вернуться к списку спортсменов"""
    query = update.callback_query

    # Возвращаемся в последний выбранный фильтр (если был), иначе в экран категорий
    filter_key = context.user_data.get("athletes_list_filter")
    if filter_key in ("all", "children", "adults", "inactive", "active_children", "active_adults", "inactive_children", "inactive_adults"):
        await query.answer()
        from handlers.coach_handlers import show_athletes_list_by_filter
        page = int(context.user_data.get("athletes_list_page") or 0)
        await show_athletes_list_by_filter(update, context, filter_key, page=page)
    else:
        # athletes_list сам вызовет query.answer() — не отвечать дважды
        from handlers.coach_handlers import athletes_list
        await athletes_list(update, context)


async def handle_back_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Вернуться в главное меню"""
    query = update.callback_query
    await query.answer()

    from handlers.coach_handlers import coach_menu
    await coach_menu(update, context)


async def show_my_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать абонемент спортсмена (для самого спортсмена)"""
    query = update.callback_query
    message = update.message
    
    user_id = query.from_user.id if query else update.effective_user.id
    
    if query:
        await query.answer()
    
    try:
        with get_db_session() as session:
            user = get_user_by_telegram_id(session, user_id)
        
            if not user:
                error_msg = "❌ Пользователь не найден"
                if query:
                    await query.edit_message_text(error_msg)
                elif message:
                    await message.reply_text(error_msg)
                return
        
            # Проверяем, что это спортсмен
            if get_user_role(user) != 'athlete':
                error_msg = "❌ Эта функция доступна только для спортсменов"
                if query:
                    await query.edit_message_text(error_msg)
                elif message:
                    await message.reply_text(error_msg)
                return
        
            # Получаем спортсмена: у Athlete user.id = athletes.id (PK)
            athlete = session.query(Athlete).filter_by(id=user.id).first()
        
            if not athlete:
                error_msg = "❌ Профиль спортсмена не найден. Обратитесь к тренеру."
                if query:
                    await query.edit_message_text(error_msg)
                elif message:
                    await message.reply_text(error_msg)
                return
        
            expire_stale_subscription_freezes(session, athlete_id=athlete.id, commit=True)
            session.refresh(athlete)

            from database.db_utils import auto_deduct_daily_trainings, migrate_existing_subscription

            active_subs = sorted(active_subscriptions_all(athlete), key=lambda s: s.id)
            for sub in active_subs:
                migrate_existing_subscription(session, sub.id)
            if active_subs:
                auto_deduct_daily_trainings(session)
                for sub in active_subs:
                    session.refresh(sub)

            message_text = f"🎫 <b>МОЙ АБОНЕМЕНТ</b>\n\n"
            message_text += f"👤 <b>{html.escape(athlete.full_name)}</b>\n"
            message_text += f"🥊 {athlete.sport_type or 'Не указан'}\n\n"

            if not active_subs:
                message_text += f"❌ У вас нет активного абонемента.\n\n"
                message_text += f"Обратитесь к тренеру для оформления абонемента."
            
                keyboard = [
                    [InlineKeyboardButton("🏠 В меню", callback_data="athlete_back_to_menu")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
            
                if query:
                    await query.edit_message_text(message_text, reply_markup=reply_markup, parse_mode='HTML')
                elif message:
                    await message.reply_text(message_text, reply_markup=reply_markup, parse_mode='HTML')
                return

            if len(active_subs) > 1:
                message_text += f"<b>Активных направлений: {len(active_subs)}</b>\n\n"

            for si, subscription in enumerate(active_subs, 1):
                if len(active_subs) > 1:
                    sport_lbl = html.escape(
                        subscription.sport_type or athlete.sport_type or "—"
                    )
                    message_text += f"<b>━━ {si}. {sport_lbl}</b> (#{subscription.id})\n"

                used_trainings = session.query(Attendance).filter_by(
                    subscription_id=subscription.id,
                    attended=True,
                ).count()

                unused_trainings = session.query(Attendance).filter_by(
                    subscription_id=subscription.id,
                    attended=False,
                ).count()

                total_deducted = used_trainings + unused_trainings
                trainings_total = subscription.trainings_total or 0
                usage_percent = (
                    round((total_deducted / trainings_total) * 100, 1)
                    if trainings_total > 0
                    else 0
                )

                progress_length = 15
                filled = int(usage_percent * progress_length / 100)
                progress_bar = "█" * filled + "░" * (progress_length - filled)

                age_group_display = format_age_group_label(athlete.age_group)
                dk = getattr(subscription, "discipline_key", None)

                message_text += f"<b>📋 ОСНОВНАЯ ИНФОРМАЦИЯ</b>\n"
                message_text += f"• Возрастная группа: {age_group_display}\n"
                message_text += f"• Формат занятий: {format_training_format_ru(dk)}\n"
                sub_type_display = _format_subscription_type_ru(subscription.subscription_type)
                message_text += f"• Тип абонемента: {sub_type_display}\n"

                status_display = _format_subscription_status_ui(subscription)
                message_text += f"• Статус: {status_display}\n"
                message_text = _append_subscription_freeze_ui_lines(message_text, subscription)

                start_date_str = _format_dt(subscription.start_date)
                message_text += f"• Дата начала: {start_date_str}\n"

                if subscription.end_date:
                    end_date_str = _format_dt(subscription.end_date)
                    message_text += f"• Дата окончания: {end_date_str}{_freeze_note(subscription)}\n"

                    if subscription.trainings_total is None:
                        message_text += f"• Осталось тренировок: —\n"
                    else:
                        actual_remaining = calculate_actual_trainings_remaining(
                            session, subscription
                        )
                        if actual_remaining is not None:
                            message_text += f"• Осталось тренировок: {actual_remaining}/{subscription.trainings_total}\n"
                            if sync_subscription_trainings_remaining(session, subscription):
                                session.commit()
                        else:
                            message_text += f"• Осталось тренировок: {subscription.trainings_remaining}/{subscription.trainings_total}\n"
                else:
                    message_text += f"• Дата окончания: —\n"
                    message_text += f"• Осталось тренировок: —\n"
                if subscription.created_at:
                    created_str = subscription.created_at.strftime("%d.%m.%Y %H:%M")
                    message_text += f"• Создан: {created_str}\n"

                message_text += f"\n<b>🏋️ ТРЕНИРОВКИ</b>\n"
                trainings_remaining = subscription.trainings_remaining or 0
                if trainings_total is not None:
                    message_text += f"• Всего: {trainings_total}\n"
                    message_text += f"• Использовано: {used_trainings}\n"
                    message_text += f"• Осталось: {trainings_remaining}\n"
                else:
                    message_text += f"• Всего: —\n"
                    message_text += f"• Использовано: {used_trainings}\n"
                    message_text += f"• Осталось: —\n"

                if subscription.total_restored > 0:
                    message_text += f"• Восстановлено: {subscription.total_restored}\n"

                message_text += f"\n<b>📊 ИСПОЛЬЗОВАНИЕ</b>\n"
                message_text += f"{progress_bar} {usage_percent}%\n"
                if len(active_subs) > 1 and si < len(active_subs):
                    message_text += "\n"

            # Создаем инлайн клавиатуру
            keyboard = [
                [InlineKeyboardButton("📜 Архив", callback_data=f"subscription_history_athlete_{athlete.id}")],
                [InlineKeyboardButton("🔄 Обновить", callback_data="athlete_subscription_refresh")],
                [InlineKeyboardButton("🏠 В меню", callback_data="athlete_back_to_menu")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
        
            if query:
                await query.edit_message_text(message_text, reply_markup=reply_markup, parse_mode='HTML')
            elif message:
                await message.reply_text(message_text, reply_markup=reply_markup, parse_mode='HTML')
    
    except Exception as e:
        logger.error(f"❌ ОШИБКА ПРИ ПОКАЗЕ АБОНЕМЕНТА СПОРТСМЕНА: {e}", exc_info=True)
        error_msg = "❌ Ошибка при загрузке информации об абонементе"
        if query:
            await query.edit_message_text(error_msg)
        elif message:
            await message.reply_text(error_msg)


async def handle_athlete_back_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Вернуться в меню спортсмена"""
    query = update.callback_query
    await query.answer()
    
    from handlers.start import show_athlete_menu
    await show_athlete_menu(update, context)


async def show_my_athlete_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать карточку спортсмена (для самого спортсмена)"""
    query = update.callback_query
    message = update.message
    
    user_id = query.from_user.id if query else update.effective_user.id
    
    if query:
        await query.answer()
    
    try:
        with get_db_session() as session:
            user = get_user_by_telegram_id(session, user_id)
        
            if not user:
                error_msg = "❌ Пользователь не найден"
                if query:
                    await query.edit_message_text(error_msg)
                elif message:
                    await message.reply_text(error_msg)
                return
        
            # Проверяем, что это спортсмен
            if get_user_role(user) != 'athlete':
                error_msg = "❌ Эта функция доступна только для спортсменов"
                if query:
                    await query.edit_message_text(error_msg)
                elif message:
                    await message.reply_text(error_msg)
                return
        
            # Получаем спортсмена: у Athlete user.id = athletes.id (PK)
            athlete = session.query(Athlete).filter_by(id=user.id).first()
        
            if not athlete:
                error_msg = "❌ Профиль спортсмена не найден. Обратитесь к тренеру."
                if query:
                    await query.edit_message_text(error_msg)
                elif message:
                    await message.reply_text(error_msg)
                return
        
            # Получаем информацию для карточки
            card_info = get_athlete_card_info(session, athlete.id)
        
            if not card_info:
                error_msg = "❌ Ошибка при загрузке карточки"
                if query:
                    await query.edit_message_text(error_msg)
                elif message:
                    await message.reply_text(error_msg)
                return
        
            athlete = card_info['athlete']
            subscription = card_info['subscription']
            stats = card_info['stats']
        
            # Формируем сообщение
            message_text = f"👤 <b>МОЯ КАРТОЧКА</b>\n\n"
            message_text += f"<b>{html.escape(athlete.full_name)}</b>\n"
            message_text += f"📞 {athlete.phone or 'Не указан'}\n"
            message_text += f"🥊 {athlete.sport_type or 'Не указан'} | {card_info['age_group_display']}\n"
            message_text += f"👨‍🏫 Тренер: {athlete.coach.first_name if athlete.coach else 'Не указан'}\n"
            message_text += f"📅 В клубе с: {athlete.created_at.strftime('%d.%m.%Y')}\n\n"
        
            message_text += f"<b>📊 СТАТИСТИКА (30 дней)</b>\n"
            message_text += f"• Посещено: {stats['attended_trainings']}/{stats['total_trainings']}\n"
            message_text += f"• Пропущено: {stats['missed_trainings']}\n"
            message_text += f"• Посещаемость: {stats['attendance_rate']}%\n\n"
        
            message_text += f"<b>🏥 МЕДИЦИНСКАЯ ИНФОРМАЦИЯ</b>\n"
            message_text += f"{card_info['medical_display'] or '—'}\n\n"
        
            message_text += f"<b>🎫 АБОНЕМЕНТ</b>\n"
            if subscription:
                # Единый статус абонемента
                status_display = _format_subscription_status_ui(subscription)
            
                trainings_remaining = subscription.trainings_remaining or 0
                trainings_total = subscription.trainings_total or 0
                trainings = f"{trainings_remaining}/{trainings_total}" if trainings_total else "—/—"
                if subscription.total_restored > 0:
                    trainings += f" (🔄 +{subscription.total_restored})"
                sub_type = _format_subscription_type_ru(subscription.subscription_type)
                dk = getattr(subscription, "discipline_key", None)
                end_date = _format_dt(subscription.end_date)
                freeze_note = _freeze_note(subscription)
            
                message_text += f"• Статус: {status_display}\n"
                message_text += f"• Формат занятий: {format_training_format_ru(dk)}\n"
                message_text += f"• Тип абонемента: {sub_type}\n"
                message_text += f"• Тренировки: {trainings}\n"
                message_text += f"• Действует до: {end_date}{freeze_note}\n"
            
                # Добавляем информацию о создании
                if subscription.created_at:
                    message_text += f"• Активирован: {subscription.created_at.strftime('%d.%m.%Y')}\n"
            else:
                message_text += f"• ❌ Нет активного абонемента\n"
        
            message_text += f"\n🆔 ID: {athlete.id}"
        
            # Создаем инлайн клавиатуру
            keyboard = []
        
            # Первый ряд: основные действия
            keyboard.append([
                InlineKeyboardButton("🎫 Мой абонемент", callback_data="athlete_subscription_refresh"),
                InlineKeyboardButton("📊 Статистика", callback_data=f"stats_athlete_{athlete.id}")
            ])
        
            # Второй ряд: навигация
            keyboard.append([
                InlineKeyboardButton("🏠 В меню", callback_data="athlete_back_to_menu")
            ])
        
            reply_markup = InlineKeyboardMarkup(keyboard)
        
            if query:
                await query.edit_message_text(
                    message_text,
                    reply_markup=reply_markup,
                    parse_mode='HTML'
                )
            else:
                await message.reply_text(
                    message_text,
                    reply_markup=reply_markup,
                    parse_mode='HTML'
                )
    
    except Exception as e:
        logger.error(f"❌ ОШИБКА ПРИ ПОКАЗЕ КАРТОЧКИ СПОРТСМЕНА: {e}", exc_info=True)
        error_msg = "❌ Ошибка при загрузке карточки"
        if query:
            await query.edit_message_text(error_msg)
        elif message:
            await message.reply_text(error_msg)


async def show_subscription_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать историю абонементов спортсмена (все или только индивидуальные брони)."""
    query = update.callback_query
    await query.answer()

    try:
        athlete_id, history_filter, athlete_self_cb, _month_mode, _older_yyyymm = (
            _parse_subscription_history_callback(query.data)
        )
    except (ValueError, IndexError):
        await query.edit_message_text("❌ Неверная ссылка на абонемент")
        return

    try:
        with get_db_session() as session:
            user = get_user_by_telegram_id(session, query.from_user.id)

            if not user:
                await query.edit_message_text("❌ Пользователь не найден")
                return

            if isinstance(user, Athlete) and athlete_self_cb:
                athlete = session.query(Athlete).filter_by(id=user.id).first()
                if not athlete:
                    await query.edit_message_text("❌ Профиль спортсмена не найден")
                    return
                athlete_id = athlete.id
            else:
                athlete = session.query(Athlete).filter_by(id=athlete_id).first()
                if not athlete:
                    await query.edit_message_text("❌ Спортсмен не найден")
                    return

            is_athlete_viewing_own = (
                is_athlete(user) and athlete.telegram_id == user.telegram_id
            )
            if not (is_athlete_viewing_own or can_edit_athlete(user, athlete)):
                await query.edit_message_text("❌ У вас нет доступа")
                return

            all_subscriptions = session.query(Subscription).filter_by(athlete_id=athlete_id).all()
            individual_subs, monthly_subs, single_subs = _history_subscription_buckets(
                all_subscriptions
            )

            back_cb = _subscription_history_back_callback(
                athlete_id,
                is_coach_viewing=is_coach_viewing_athlete,
                athlete_self=athlete_self_cb,
            )
            if history_filter == _HISTORY_FILTER_ALL:
                message = _render_subscription_archive_type_picker_message(athlete.full_name)
                keyboard = _subscription_history_category_keyboard(
                    athlete_id,
                    athlete_self=athlete_self_cb,
                ) + [
                    [InlineKeyboardButton("🔙 Назад к абонементу", callback_data=back_cb)]
                ]
                await query.edit_message_text(
                    message,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="HTML",
                )
                return

            subscriptions = _history_subscriptions_for_filter(
                history_filter,
                individual_subs,
                monthly_subs,
                single_subs,
            )
            sections_cb = _history_sections_back_callback(
                athlete_id, athlete_self=athlete_self_cb
            )
            now = now_moscow()
            title = _history_section_title(history_filter)
            period_caption = _subscription_history_period_caption(history_filter)

            period_subs = _filter_subscriptions_for_history_period(
                subscriptions,
                history_filter,
                now=now,
            )
            period_subs = sorted(
                period_subs,
                key=lambda s: (_history_subscription_sort_date(s), s.id or 0),
                reverse=True,
            )
            shown = period_subs[:_HISTORY_SECTION_LIST_LIMIT]
            hidden = len(period_subs) - len(shown)

            message = _render_subscription_history_section_message(
                athlete.full_name,
                title,
                period_caption=period_caption,
            )
            if not shown:
                message += "\n\n📭 Нет записей за этот период."
            elif hidden > 0:
                message += f"\n\n<i>Показаны последние {len(shown)} из {len(period_subs)}</i>"

            keyboard = _build_subscription_archive_list_keyboard(shown, sections_cb)
            await query.edit_message_text(
                message,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML",
            )
            return

    except Exception as e:
        logger.error(f"❌ ОШИБКА ПРИ ПОКАЗЕ ИСТОРИИ АБОНЕМЕНТОВ: {e}", exc_info=True)
        await query.edit_message_text("❌ Ошибка при загрузке абонемента")


def _subscription_used_trainings_count(session, subscription: Subscription) -> int:
    remaining = calculate_actual_trainings_remaining(session, subscription)
    total = subscription.trainings_total
    if total is None or remaining is None:
        return 0
    return max(int(total) - int(remaining), 0)


def _archive_slot_booking_kind_label(subscription: Subscription) -> str:
    """Подпись вида «Индивидуальная» / «Разовая» для строки архива."""
    sub_type = (subscription.subscription_type or "").strip().lower()
    if _is_individual_subscription(subscription) or sub_type == "individual":
        return "Индивидуальная"
    if sub_type == "single":
        return "Разовая"
    return _format_subscription_type_ru(subscription.subscription_type) or "—"


def _format_archive_training_datetime(subscription: Subscription) -> str:
    """Дата и время одной тренировки: 17.05.2026 08:00–09:00."""
    start = subscription.start_date
    if not start:
        return "не назначена"
    end = subscription.end_date
    if end and start.date() == end.date() and end > start:
        return f"{start.strftime('%d.%m.%Y %H:%M')}–{end.strftime('%H:%M')}"
    return _format_dt(start)


def _format_archive_period_dates(subscription: Subscription) -> str:
    """Период абonementа в архиве — только даты."""
    start = subscription.start_date.strftime("%d.%m.%Y") if subscription.start_date else "—"
    end = subscription.end_date.strftime("%d.%m.%Y") if subscription.end_date else "—"
    return f"{start} — {end}"


def _archive_group_format_label(discipline_key: Optional[str]) -> str:
    """Групповая / индивидуальная — для строки архива."""
    fmt = format_training_format_ru(discipline_key)
    if fmt == "Групповые":
        return "Групповая"
    if fmt == "Индивидуальные":
        return "Индивидуальная"
    return fmt


def _render_subscription_archive_monthly_detail_message(
    subscription: Subscription, athlete: Athlete
) -> str:
    """Компактная карточка месячного абonementа в архиве."""
    sport = (subscription.sport_type or "—").strip()
    dk = getattr(subscription, "discipline_key", None)
    fmt = _archive_group_format_label(dk)
    age_group = format_age_group_label(athlete.age_group)

    message = f"<b>Абонемент</b>\n\n"
    message += f"👤 <b>{html.escape(athlete.full_name)}</b>\n\n"
    message += f"{html.escape(sport)} | {fmt} | {html.escape(age_group)}\n"
    message += f"• Период действия: {_format_archive_period_dates(subscription)}\n"
    if subscription.created_at:
        message += f"• Оформлен: {subscription.created_at.strftime('%d.%m.%Y %H:%M')}\n"
    return message


def _render_subscription_archive_slot_detail_message(
    subscription: Subscription, athlete: Athlete
) -> str:
    """Компактная карточка индивидуальной / разовой записи в архиве."""
    sub_type = (subscription.subscription_type or "").strip().lower()
    if _is_individual_subscription(subscription) or sub_type == "individual":
        title = f"<b>Индивидуальная бронь</b>"
    else:
        title = f"<b>Разовая тренировка</b>"

    sport = (subscription.sport_type or "—").strip()
    kind = _archive_slot_booking_kind_label(subscription)

    message = f"{title}\n\n"
    message += f"👤 <b>{html.escape(athlete.full_name)}</b>\n\n"
    message += f"{html.escape(sport)} | {kind}\n"
    message += (
        "• Дата и время начала и окончания тренировки: "
        f"<b>{_format_archive_training_datetime(subscription)}</b>\n"
    )
    if subscription.created_at:
        message += f"• Оформлена: {subscription.created_at.strftime('%d.%m.%Y %H:%M')}\n"
    return message


def _render_subscription_archive_detail_message(
    session, subscription: Subscription, athlete: Athlete
) -> str:
    sub_type = (subscription.subscription_type or "").strip().lower()

    if _is_individual_subscription(subscription) or sub_type == "single":
        return _render_subscription_archive_slot_detail_message(subscription, athlete)

    return _render_subscription_archive_monthly_detail_message(subscription, athlete)


async def view_subscription_from_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать детальную информацию об абонементе из архива."""
    query = update.callback_query
    await query.answer()

    subscription_id = int(query.data.replace("view_sub_", ""))

    try:
        with get_db_session() as session:
            user = get_user_by_telegram_id(session, query.from_user.id)

            if not user:
                await query.edit_message_text("❌ Пользователь не найден")
                return

            from services.subscription_service import SubscriptionService
            subscription = SubscriptionService.get_subscription_or_raise(session, subscription_id)
            athlete = subscription.athlete

            is_athlete_viewing_own = (
                is_athlete(user) and athlete.telegram_id == user.telegram_id
            )
            if not (is_athlete_viewing_own or can_edit_athlete(user, athlete)):
                await query.edit_message_text("❌ У вас нет доступа")
                return

            if subscription.trainings_total is not None:
                sync_subscription_trainings_remaining(session, subscription)
                session.commit()

            message = _render_subscription_archive_detail_message(
                session, subscription, athlete
            )

            if _is_individual_subscription(subscription):
                hist_filter = _HISTORY_FILTER_INDIVIDUAL
            elif (subscription.subscription_type or "").strip().lower() == "single":
                hist_filter = _HISTORY_FILTER_SINGLE
            else:
                hist_filter = _HISTORY_FILTER_GROUP

            hist_cb = _subscription_history_list_callback(
                athlete.id,
                history_filter=hist_filter,
                athlete_self=not is_coach_viewing_athlete,
            )
            keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data=hist_cb)]]

            await query.edit_message_text(
                message,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML",
            )

    except Exception as e:
        logger.error(f"❌ ОШИБКА ПРИ ПОКАЗЕ АБОНЕМЕНТА ИЗ ИСТОРИИ: {e}", exc_info=True)
        await query.edit_message_text("❌ Ошибка при загрузке абонемента")


async def handle_activate_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Активировать абонемент - показать выбор типа или активировать существующий"""
    query = update.callback_query
    await query.answer()
    
    logger.info(f"[activate_sub] raw_query_data={getattr(query, 'data', None)} user_id={getattr(query.from_user, 'id', None)}")
    
    callback_data = query.data.replace("activate_sub_", "")
    logger.info(f"[activate_sub] parsed_callback_data={callback_data}")
    
    try:
        with get_db_session() as session:
            user = get_user_by_telegram_id(session, query.from_user.id)
            if not is_staff(user):
                await query.edit_message_text("❌ У вас нет доступа")
                return
        
            # Быстрый сценарий: из активного group/single сразу перейти к индивидуальной тренировке.
            # callback: activate_sub_add_individual_{subscription_id}
            if callback_data.startswith("add_individual_"):
                sid_raw = callback_data.replace("add_individual_", "").strip()
                if not sid_raw.isdigit():
                    await query.edit_message_text(
                        "❌ Некорректная кнопка добавления индивидуальной тренировки. "
                        "Откройте абонемент заново из карточки спортсмена."
                    )
                    return
                base_subscription_id = int(sid_raw)
                base_subscription = session.query(Subscription).filter_by(id=base_subscription_id).first()
                if not base_subscription:
                    await query.edit_message_text("❌ Базовый абонемент не найден")
                    return

                athlete = base_subscription.athlete
                athlete_id = athlete.id
                if isinstance(user, Coach) and athlete.created_by != user.id:
                    await query.edit_message_text("❌ Вы не можете изменять этого спортсмена")
                    return

                sport_type_for_sub = (base_subscription.sport_type or athlete.sport_type or "").strip()
                if not sport_type_for_sub:
                    await query.edit_message_text(
                        "❌ Не удалось определить вид спорта. Укажите вид спорта у спортсмена или абонемента."
                    )
                    return
                if not _supports_individual_subscription_type(session):
                    await query.edit_message_text(
                        "❌ Схема БД не поддерживает тип индивидуального абонемента.\n"
                        "Нужно выполнить миграцию БД (subscription_type='individual').\n"
                        "После миграции повторите действие."
                    )
                    return
                if not _supports_multi_individual_bookings(session):
                    if _has_legacy_unique_athlete_constraint(session):
                        await query.edit_message_text(
                            "❌ Схема БД в legacy-режиме: действует UNIQUE по athlete_id "
                            "(1 спортсмен = 1 абонемент).\n"
                            "Выполните миграцию БД и перезапустите бота."
                        )
                        return
                    await query.edit_message_text(
                        "❌ Схема БД не поддерживает несколько индивидуальных броней "
                        "на одного спортсмена.\n"
                        "Перезапустите бота после обновления кода (миграция БД) "
                        "или выполните миграцию вручную."
                    )
                    return

                dk_individual = discipline_key_for(sport_type_for_sub, format="individual")
                coach_id_for_sub = user.id if isinstance(user, Coach) else None
                try:
                    subscription = prepare_individual_subscription_for_activation(
                        session,
                        athlete,
                        sport_type_for_sub,
                        responsible_coach_id=coach_id_for_sub,
                    )
                    session.flush()
                    session.commit()
                except Exception as e:
                    logger.error(
                        "[activate_sub] add_individual prepare failed athlete_id=%s err=%s",
                        athlete_id,
                        e,
                        exc_info=True,
                    )
                    try:
                        session.rollback()
                    except Exception:
                        pass
                    err_type = type(e).__name__
                    err_msg = str(e).strip()
                    err_tail = f" ({err_type}: {err_msg[:160]})" if err_msg else f" ({err_type})"
                    hint = ""
                    if "unique" in err_msg.lower() or "integrity" in err_type.lower():
                        hint = (
                            "\n\nВозможно, в БД осталось старое ограничение UNIQUE "
                            "(athlete_id, discipline_key). Перезапустите бота для миграции "
                            "или выполните обновление индексов subscriptions."
                        )
                    await query.edit_message_text(
                        "❌ Не удалось создать новую запись индивидуальной тренировки. "
                        f"Повторите позже.{err_tail}{hint}"
                    )
                    return
                if not getattr(subscription, "responsible_coach_id", None) and coach_id_for_sub:
                    # На шаге открытия календаря не пишем в БД: только выбор слота.
                    # ID тренера берется далее из responsible_coach_id или athlete.created_by.
                    logger.info(
                        "[activate_sub] add_individual missing responsible_coach_id subscription_id=%s athlete_id=%s",
                        subscription.id,
                        athlete_id,
                    )

                now = now_moscow()
                logger.info(
                    "[activate_sub] add_individual new booking subscription_id=%s athlete_id=%s",
                    subscription.id,
                    athlete_id,
                )

                reply_markup = _build_activation_calendar_individual(
                    subscription.id, now.year, now.month
                )
                await query.edit_message_text(
                    f"👤 <b>{html.escape(athlete.full_name)}</b>\n\n"
                    "🎫 <b>Новая индивидуальная тренировка</b>\n\n"
                    "Выберите дату и время слота.",
                    reply_markup=reply_markup,
                    parse_mode="HTML",
                )
                return

            # Если это создание нового абонемента (activate_sub_new_123)
            if callback_data.startswith("new_"):
                logger.info("[activate_sub] branch=new_subscription_choose_type")
                athlete_id = int(callback_data.replace("new_", ""))
                athlete = session.query(Athlete).filter_by(id=athlete_id).first()
                if not athlete:
                    await query.edit_message_text("❌ Спортсмен не найден")
                    return
            
                # Убираем ограничение - любой тренер может создать абонемент
                # Но проверяем, что у тренера указан вид спорта
                sport_type_for_sub = None
                if is_coach(user):
                    sport_type_for_sub = coach_sport_type_name(user)
                    if not sport_type_for_sub:
                        await query.edit_message_text("❌ У вас не указан вид спорта. Обратитесь к администратору.")
                        return
                elif is_admin(user):
                    # Для админа можно выбрать вид спорта из существующих абонементов или использовать из спортсмена
                    sport_type_for_sub = athlete.sport_type
            
                # Показываем выбор типа абонемента
                keyboard = [
                    [
                        InlineKeyboardButton("Месячный", callback_data=f"activate_sub_type_{athlete_id}_monthly"),
                        InlineKeyboardButton("Разовый", callback_data=f"activate_sub_type_{athlete_id}_single"),
                    ],
                    [
                        InlineKeyboardButton(
                            "Индивидуальный",
                            callback_data=f"activate_sub_type_{athlete_id}_individual",
                        )
                    ],
                    [InlineKeyboardButton("🔙 Назад", callback_data=f"subscription_athlete_{athlete_id}")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
            
                sport_type_display = sport_type_for_sub or "не указан"
                await query.edit_message_text(
                    f"👤 <b>{html.escape(athlete.full_name)}</b>\n\n"
                    f"🎫 <b>СОЗДАНИЕ АБОНЕМЕНТА</b>\n\n"
                    f"Вид спорта: <b>{sport_type_display}</b>\n\n"
                    f"Выберите тип абонемента:",
                    reply_markup=reply_markup,
                    parse_mode='HTML'
                )
                # Сохраняем вид спорта в контексте для использования при создании
                context.user_data['new_subscription_sport_type'] = sport_type_for_sub
                return
        
            # Если это выбор типа для нового абонемента (activate_sub_type_123_monthly)
            if callback_data.startswith("type_"):
                # Проверяем, это новый абонемент или существующий
                if callback_data.startswith("type_existing_"):
                    logger.info("[activate_sub] branch=activate_existing_with_type_choice")
                    # Активация существующего абонемента с выбором типа
                    parts = callback_data.replace("type_existing_", "").split("_")
                    subscription_id = int(parts[0])
                    subscription_type = parts[1]  # monthly / single / individual
                    logger.info(f"[activate_sub] existing_subscription_id={subscription_id} chosen_type={subscription_type}")
                
                    subscription = session.query(Subscription).filter_by(id=subscription_id).first()
                    if not subscription:
                        await query.edit_message_text("❌ Абонемент не найден")
                        return
                
                    athlete = subscription.athlete
                    if isinstance(user, Coach) and athlete.created_by != user.id:
                        await query.edit_message_text("❌ Вы не можете изменять этот абонемент")
                        return
                
                    # Устанавливаем тип абонемента и рассчитываем количество тренировок

                    subscription.subscription_type = subscription_type
                    st = (subscription.sport_type or athlete.sport_type or "").strip()
                    if subscription_type == "monthly":
                        subscription.trainings_total = 12
                        subscription.trainings_remaining = 12
                        subscription.discipline_key = discipline_key_for(st, format="group")
                    elif subscription_type == "single":
                        subscription.trainings_total = 1
                        subscription.trainings_remaining = 1
                        subscription.discipline_key = discipline_key_for(st, format="group")
                    elif subscription_type == "individual":
                        subscription.trainings_total = 1
                        subscription.trainings_remaining = 1
                        subscription.discipline_key = discipline_key_for(st, format="individual")
                    else:
                        await query.edit_message_text("❌ Неверный тип абонемента")
                        return

                    # До выбора даты НЕ активируем и НЕ ставим даты
                    subscription.is_active = False
                    subscription.start_date = None
                    subscription.end_date = None

                    session.commit()
                    logger.info(f"[activate_sub] type_selected_existing subscription_id={subscription.id} type={subscription.subscription_type}")

                    # Показываем календарь выбора даты первой тренировки (дата = дата активации)
                    now = now_moscow()
                    sport_type = subscription.sport_type or athlete.sport_type
                    if subscription_type == "individual":
                        reply_markup = _build_activation_calendar_individual(
                            subscription.id, now.year, now.month
                        )
                    else:
                        reply_markup = _build_activation_calendar(
                            subscription.id, sport_type, athlete.age_group, now.year, now.month
                        )

                    subscription_type_ru = _format_subscription_type_ru(subscription_type)
                    await query.edit_message_text(
                        f"👤 <b>{html.escape(athlete.full_name)}</b>\n\n"
                        f"🎫 <b>АКТИВАЦИЯ АБОНЕМЕНТА</b>\n\n"
                        f"Тип: <b>{subscription_type_ru}</b>\n\n"
                        f"Выберите дату <b>первой тренировки</b> (она будет датой активации):",
                        reply_markup=reply_markup,
                        parse_mode="HTML"
                    )
                    return
                else:
                    logger.info("[activate_sub] branch=create_new_with_type_choice")
                    # Создание нового абонемента с выбором типа
                    parts = callback_data.replace("type_", "").split("_")
                    athlete_id = int(parts[0])
                    subscription_type = parts[1]  # monthly / single / individual
                    logger.info(f"[activate_sub] athlete_id={athlete_id} chosen_type={subscription_type}")
                
                    athlete = session.query(Athlete).filter_by(id=athlete_id).first()
                    if not athlete:
                        await query.edit_message_text("❌ Спортсмен не найден")
                        return
                
                    # Убираем ограничение - любой тренер может создать абонемент
                    # Получаем вид спорта из контекста (сохранен при выборе типа)
                    sport_type_for_sub = context.user_data.get('new_subscription_sport_type')
                
                    # Если не сохранен в контексте, берем из профиля тренера
                    if not sport_type_for_sub:
                        if isinstance(user, Coach):
                            sport_type_for_sub = coach_sport_type_name(user)
                        if not sport_type_for_sub:
                            sport_type_for_sub = athlete.sport_type
                
                    from services.subscription_service import SubscriptionService

                    sub_fmt = "individual" if subscription_type == "individual" else "group"
                    dk = discipline_key_for(sport_type_for_sub or "", format=sub_fmt)
                    if subscription_type != "individual":
                        existing_same = (
                            session.query(Subscription)
                            .filter_by(athlete_id=athlete_id, discipline_key=dk)
                            .first()
                        )
                        if existing_same and existing_same.is_active:
                            await query.edit_message_text(
                                "❌ У спортсмена уже есть активный абонемент в этом направлении.\n"
                                "Откройте существующий абонемент или завершите его."
                            )
                            return

                    if subscription_type == "individual":
                        subscription = prepare_individual_subscription_for_activation(
                            session,
                            athlete,
                            sport_type_for_sub,
                            responsible_coach_id=user.id if isinstance(user, Coach) else None,
                        )
                    else:
                        subscription = SubscriptionService.create_subscription(
                            session=session,
                            athlete_id=athlete_id,
                            subscription_type=subscription_type,
                            sport_type=sport_type_for_sub,
                            discipline_key=dk,
                            subscription_format=sub_fmt,
                            responsible_coach_id=user.id if isinstance(user, Coach) else None,
                        )

                    subscription.subscription_type = subscription_type
                    if subscription_type == "monthly":
                        subscription.trainings_total = 12
                        subscription.trainings_remaining = 12
                    elif subscription_type == "single":
                        subscription.trainings_total = 1
                        subscription.trainings_remaining = 1
                    elif subscription_type == "individual":
                        subscription.trainings_total = 1
                        subscription.trainings_remaining = 1
                    else:
                        await query.edit_message_text("❌ Неверный тип абонемента")
                        return

                    # Явно фиксируем "неактивен до выбора даты"
                    subscription.is_active = False
                    subscription.start_date = None
                    subscription.end_date = None

                    # Обновляем вид спорта абонемента (для дальнейшей логики календаря/тренировок)
                    if sport_type_for_sub:
                        subscription.sport_type = sport_type_for_sub

                    session.commit()
                    logger.info(
                        f"[activate_sub] type_selected_new_updated subscription_id={subscription.id} type={subscription.subscription_type}"
                    )

                    # Очищаем сохраненный вид спорта из контекста
                    context.user_data.pop('new_subscription_sport_type', None)

                    now = now_moscow()
                    st = subscription.sport_type or athlete.sport_type
                    if subscription_type == "individual":
                        reply_markup = _build_activation_calendar_individual(
                            subscription.id, now.year, now.month
                        )
                    else:
                        reply_markup = _build_activation_calendar(
                            subscription.id, st, athlete.age_group, now.year, now.month
                        )

                    subscription_type_ru = _format_subscription_type_ru(subscription_type)
                    await query.edit_message_text(
                        f"👤 <b>{html.escape(athlete.full_name)}</b>\n\n"
                        f"🎫 <b>АКТИВАЦИЯ АБОНЕМЕНТА</b>\n\n"
                        f"Тип: <b>{subscription_type_ru}</b>\n\n"
                        f"Выберите дату <b>первой тренировки</b> (она будет датой активации):",
                        reply_markup=reply_markup,
                        parse_mode="HTML"
                    )
                    return
        
            # Если это активация существующего абонемента (activate_sub_<id> — только число)
            if not callback_data.isdigit():
                logger.warning(
                    "[activate_sub] unexpected_callback_data=%r raw=%r",
                    callback_data,
                    getattr(query, "data", None),
                )
                await query.edit_message_text(
                    "❌ Некорректная кнопка активации. Откройте абонемент заново из карточки спортсмена."
                )
                return
            subscription_id = int(callback_data)
            logger.info(f"[activate_sub] branch=activate_existing subscription_id={subscription_id}")
            subscription = session.query(Subscription).filter_by(id=subscription_id).first()
            if not subscription:
                await query.edit_message_text("❌ Абонемент не найден")
                return
        
            athlete = subscription.athlete
            if isinstance(user, Coach) and athlete.created_by != user.id:
                await query.edit_message_text("❌ Вы не можете изменять этот абонемент")
                return
        
            # Для неактивного абонемента всегда показываем выбор типа (чтобы можно было выбрать monthly/single)
            if not subscription.is_active:
                logger.info(f"[activate_sub] existing_not_active show_type_choice current_type={subscription.subscription_type}")
                current_type = subscription.subscription_type
                if current_type == "monthly":
                    current_type_display = "Месячный"
                elif current_type == "single":
                    current_type_display = "Разовый"
                elif current_type == "individual":
                    current_type_display = "Индивидуальный"
                else:
                    current_type_display = "Не определен"

                keyboard = [
                    [
                        InlineKeyboardButton("Месячный", callback_data=f"activate_sub_type_existing_{subscription.id}_monthly"),
                        InlineKeyboardButton("Разовый", callback_data=f"activate_sub_type_existing_{subscription.id}_single"),
                    ],
                    [
                        InlineKeyboardButton(
                            "Индивидуальный",
                            callback_data=f"activate_sub_type_existing_{subscription.id}_individual",
                        )
                    ],
                    [InlineKeyboardButton("🔙 Назад", callback_data=f"subscription_{subscription.id}")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)

                await query.edit_message_text(
                    f"👤 <b>{html.escape(athlete.full_name)}</b>\n\n"
                    f"🎫 <b>АКТИВАЦИЯ АБОНЕМЕНТА</b>\n\n"
                    f"Текущий тип: <b>{current_type_display}</b>\n\n"
                    f"Выберите тип абонемента:",
                    reply_markup=reply_markup,
                    parse_mode='HTML'
                )
                return
        
            # Если тип уже определен, активируем абонемент (первая тренировка — ближайший слот от «сейчас»)
            if subscription.subscription_type == "individual":
                await query.edit_message_text(
                    "ℹ️ Для индивидуального абонемента выберите дату и время в календаре активации "
                    "(кнопка «Активировать» у неактивной записи)."
                )
                return

            from database.db_utils import _find_nearest_training_date

            sport_type = subscription.sport_type or athlete.sport_type
            age_group = athlete.age_group
            coach_selected_date = now_moscow()
            start_date = _find_nearest_training_date(coach_selected_date, sport_type, age_group)

            if subscription.subscription_type not in ("monthly", "single"):
                await query.edit_message_text("❌ Сначала выберите тип абонемента на экране активации.")
                return

            if is_training_in_global_freeze(session, start_date):
                freeze = (
                    session.query(GlobalFreeze)
                    .filter(
                        GlobalFreeze.is_active == True,
                        GlobalFreeze.start_date <= start_date,
                        GlobalFreeze.end_date >= start_date,
                    )
                    .order_by(GlobalFreeze.end_date.desc())
                    .first()
                )
                shifted_start = find_next_non_frozen_training_date(
                    session,
                    (freeze.end_date + timedelta(seconds=1)) if freeze else (start_date + timedelta(days=1)),
                    sport_type,
                    age_group,
                )
                sid = subscription.id
                confirm_cb = f"act_shift_confirm_{sid}_{training_datetime_compact(shifted_start)}"
                cancel_cb = f"act_shift_cancel_{sid}"
                keyboard = InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton("✅ Подтвердить сдвиг", callback_data=confirm_cb),
                            InlineKeyboardButton("❌ Отмена", callback_data=cancel_cb),
                        ]
                    ]
                )
                freeze_label = (
                    f"{freeze.start_date.strftime('%d.%m.%Y')} — {freeze.end_date.strftime('%d.%m.%Y')}"
                    if freeze
                    else "активной массовой заморозки"
                )
                await query.edit_message_text(
                    "⚠️ Ближайший слот по расписанию попадает в период массовой заморозки.\n\n"
                    f"Период заморозки: <b>{freeze_label}</b>\n"
                    f"Предлагаемая дата начала: <b>{shifted_start.strftime('%d.%m.%Y %H:%M')}</b>\n\n"
                    "Подтвердить сдвиг и активировать абонемент?",
                    parse_mode="HTML",
                    reply_markup=keyboard,
                )
                return

            await _finalize_subscription_activation(
                update, context, query, session, subscription, athlete, start_date
            )
        
    except Exception as e:
        logger.error(f"❌ ОШИБКА ПРИ АКТИВАЦИИ АБОНЕМЕНТА: {e}", exc_info=True)
        err_type = type(e).__name__
        err_msg = str(e).strip()
        err_tail = f" ({err_type}: {err_msg[:120]})" if err_msg else f" ({err_type})"
        await query.edit_message_text(f"❌ Ошибка при активации абонемента{err_tail}")


def deactivate_subscription(session, subscription_id: int) -> bool:
    """
    Деактивировать абонемент (внутренняя функция для автоматического использования).
    
    Args:
        session: Сессия базы данных
        subscription_id: ID абонемента
        
    Returns:
        True если успешно, False если ошибка
    """
    try:
        subscription = session.query(Subscription).filter_by(id=subscription_id).first()
        if not subscription:
            logger.error(f"❌ Абонемент с id={subscription_id} не найден")
            return False
        
        subscription.is_active = False
        session.commit()
        logger.info(f"✅ Абонемент #{subscription_id} автоматически деактивирован")
        return True
    except Exception as e:
        logger.error(f"❌ ОШИБКА ПРИ ДЕАКТИВАЦИИ АБОНЕМЕНТА #{subscription_id}: {e}", exc_info=True)
        session.rollback()
        return False


async def show_athlete_visits(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать историю посещений спортсмена (в т.ч. слоты без записи в attendances)."""
    query = update.callback_query
    await query.answer()

    try:
        athlete_id, visit_mode, older_yyyymm = _parse_visits_callback(query.data)
    except (ValueError, IndexError):
        await query.edit_message_text("❌ Неверная ссылка на историю посещений")
        return

    try:
        render_message = None
        render_markup = None
        error_message = None

        with get_db_session() as session:
            user = get_user_by_telegram_id(session, query.from_user.id)

            if not is_staff(user):
                error_message = "❌ У вас нет доступа"
            else:
                athlete = session.query(Athlete).filter_by(id=athlete_id).first()
                if not athlete:
                    error_message = "❌ Спортсмен не найден"
                elif isinstance(user, Coach) and athlete.created_by != user.id:
                    error_message = "❌ Вы не можете просматривать этого спортсмена"
                else:
                    now = now_moscow()
                    entries: List[tuple] = []
                    covered_tids = set()

                    coach_for_slots = None
                    if getattr(athlete, "created_by", None):
                        coach_for_slots = (
                            session.query(Coach)
                            .options(joinedload(Coach.sport_type_rel))
                            .filter_by(id=athlete.created_by)
                            .first()
                        )
                    if coach_for_slots is None and isinstance(user, Coach):
                        coach_for_slots = (
                            session.query(Coach)
                            .options(joinedload(Coach.sport_type_rel))
                            .filter_by(id=user.id)
                            .first()
                        )

                    if coach_for_slots and athlete.sport_type and athlete.age_group:
                        from services.attendance_training_flow import (
                            collect_athlete_visit_history_slots,
                        )

                        lookback = now - timedelta(days=_VISIT_HISTORY_LOOKBACK_DAYS)
                        slot_rows = collect_athlete_visit_history_slots(
                            session,
                            athlete,
                            coach_for_slots,
                            range_start=lookback,
                            range_end=now,
                        )
                        for training, att, sub in slot_rows:
                            if getattr(training, "id", None):
                                covered_tids.update(
                                    individual_slot_training_ids(session, training)
                                )
                                label = attendance_label_ru_for_training(
                                    att, training, now=now, with_note=(att is None)
                                )
                                if att is not None and att.attended:
                                    status_code = "present"
                                    history_source = "attendance_mark"
                                else:
                                    status_code = "absent"
                                    history_source = (
                                        "attendance_mark"
                                        if att is not None
                                        else "calendar_derived"
                                    )
                                upsert_visit_history_for_training(
                                    session,
                                    athlete_id=athlete_id,
                                    training_id=training.id,
                                    attendance=att,
                                    status_code=status_code,
                                    status_label=label,
                                    source=history_source,
                                    recorded_at=now,
                                )

                            present = _visit_history_slot_present(att)
                            kind = _visit_training_kind_ru(training, att, subscription=sub)
                            line = _format_visit_history_slot_line(
                                training, att, subscription=sub
                            )
                            if training.training_date:
                                entries.append(
                                    (training.training_date, line, present, kind)
                                )

                    extra = (
                        session.query(Attendance)
                        .options(joinedload(Attendance.training))
                        .filter_by(athlete_id=athlete_id)
                        .order_by(Attendance.created_at.asc())
                        .limit(80)
                        .all()
                    )
                    for att in extra:
                        tid = att.training_id
                        if tid and tid in covered_tids:
                            continue
                        tr = att.training
                        if tr and tr.training_date:
                            dt = tr.training_date
                            sub = att.subscription or _subscription_for_visit_slot(
                                session, athlete_id, tr
                            )
                            present = _visit_history_slot_present(att)
                            kind = _visit_training_kind_ru(tr, att, subscription=sub)
                            line = _format_visit_history_slot_line(tr, att, subscription=sub)
                            entries.append((dt, line, present, kind))
                        elif tr:
                            dt = now
                            sub = att.subscription
                            present = _visit_history_slot_present(att)
                            kind = _visit_training_kind_ru(tr, att, subscription=sub)
                            line = _format_visit_history_slot_line(tr, att, subscription=sub)
                            entries.append((dt, line, present, kind))

                    lookback_cutoff = now - timedelta(days=_VISIT_HISTORY_LOOKBACK_DAYS)
                    entries_in_window = sorted(
                        (e for e in entries if e[0] >= lookback_cutoff),
                        key=lambda x: x[0],
                    )
                    display_rows = [
                        (dt, line, present)
                        for dt, line, present, _kind in entries_in_window
                    ]
                    has_older = _has_older_visit_rows(display_rows, now=now)
                    older_by_month = _group_older_visit_rows_by_month(
                        display_rows, now=now
                    )

                    if visit_mode == _VISIT_HISTORY_MODE_OLDER_MENU:
                        render_message = _render_visit_history_month_picker(
                            athlete.full_name, older_by_month
                        )
                        render_markup = _build_visit_history_keyboard(
                            athlete_id,
                            visit_mode,
                            older_months=older_by_month,
                        )
                    elif visit_mode == _VISIT_HISTORY_MODE_OLDER_MONTH:
                        year, month = _yyyymm_to_year_month(older_yyyymm)
                        period_rows = _filter_visit_rows_older_month(
                            display_rows, year=year, month=month, now=now
                        )
                        shown_rows = period_rows
                        if len(shown_rows) > _VISIT_HISTORY_MAX_LINES:
                            shown_rows = shown_rows[-_VISIT_HISTORY_MAX_LINES:]
                        period_caption = _visit_history_month_label(year, month)
                        render_message = _render_visit_history_message(
                            athlete.full_name,
                            shown_rows,
                            total_matching=len(period_rows),
                            period_caption=period_caption,
                        )
                        render_markup = _build_visit_history_keyboard(
                            athlete_id, visit_mode
                        )
                    else:
                        period_rows = _filter_visit_rows_last_month(
                            display_rows, now=now
                        )
                        shown_rows = period_rows
                        if len(shown_rows) > _VISIT_HISTORY_MAX_LINES:
                            shown_rows = shown_rows[-_VISIT_HISTORY_MAX_LINES:]
                        render_message = _render_visit_history_message(
                            athlete.full_name,
                            shown_rows,
                            total_matching=len(period_rows),
                            period_caption="За последний месяц",
                        )
                        render_markup = _build_visit_history_keyboard(
                            athlete_id,
                            _VISIT_HISTORY_MODE_MONTH,
                            has_older=has_older,
                        )

        if error_message:
            await query.edit_message_text(error_message)
            return
        if render_message is not None:
            await query.edit_message_text(
                render_message,
                reply_markup=render_markup,
                parse_mode='HTML',
            )

    except Exception as e:
        logger.error(f"❌ ОШИБКА ПРИ ПОКАЗЕ ПОСЕЩЕНИЙ: {e}", exc_info=True)
        await query.edit_message_text("❌ Ошибка при загрузке посещений")


async def show_athlete_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать детальную статистику спортсмена"""
    query = update.callback_query
    await query.answer()
    
    athlete_id = int(query.data.replace("stats_", ""))
    
    try:
        with get_db_session() as session:
            user = get_user_by_telegram_id(session, query.from_user.id)
        
            if not is_staff(user):
                await query.edit_message_text("❌ У вас нет доступа")
                return
        
            athlete = session.query(Athlete).filter_by(id=athlete_id).first()
            if not athlete:
                await query.edit_message_text("❌ Спортсмен не найден")
                return
        
            # Проверяем права
            if isinstance(user, Coach) and athlete.created_by != user.id:
                await query.edit_message_text("❌ Вы не можете просматривать этого спортсмена")
                return
        
            # Получаем статистику за разные периоды
            now = now_moscow()
            week_ago = now - timedelta(days=7)
            month_ago = now - timedelta(days=30)
            three_months_ago = now - timedelta(days=90)
        
            # Статистика за неделю
            week_trainings = session.query(Training).filter(
                Training.sport_type == athlete.sport_type,
                Training.age_group == athlete.age_group,
                Training.training_date >= week_ago,
                Training.is_cancelled == False
            ).count()
        
            week_attended = session.query(Attendance).filter(
                Attendance.athlete_id == athlete_id,
                Attendance.attended == True,
                Attendance.training.has(Training.training_date >= week_ago)
            ).count()
        
            # Статистика за месяц
            month_trainings = session.query(Training).filter(
                Training.sport_type == athlete.sport_type,
                Training.age_group == athlete.age_group,
                Training.training_date >= month_ago,
                Training.is_cancelled == False
            ).count()
        
            month_attended = session.query(Attendance).filter(
                Attendance.athlete_id == athlete_id,
                Attendance.attended == True,
                Attendance.training.has(Training.training_date >= month_ago)
            ).count()
        
            # Статистика за 3 месяца
            three_months_trainings = session.query(Training).filter(
                Training.sport_type == athlete.sport_type,
                Training.age_group == athlete.age_group,
                Training.training_date >= three_months_ago,
                Training.is_cancelled == False
            ).count()
        
            three_months_attended = session.query(Attendance).filter(
                Attendance.athlete_id == athlete_id,
                Attendance.attended == True,
                Attendance.training.has(Training.training_date >= three_months_ago)
            ).count()
        
            # Общая статистика
            total_attended = session.query(Attendance).filter_by(
                athlete_id=athlete_id,
                attended=True
            ).count()
        
            total_missed = session.query(Attendance).filter_by(
                athlete_id=athlete_id,
                attended=False,
                was_restored=False
            ).count()

            week_implicit = month_implicit = three_implicit = 0
            if athlete.sport_type and athlete.age_group:
                week_implicit = count_implicit_absent_slots(
                    session,
                    athlete_id,
                    athlete.sport_type,
                    athlete.age_group,
                    week_ago,
                    now,
                    now=now,
                )
                month_implicit = count_implicit_absent_slots(
                    session,
                    athlete_id,
                    athlete.sport_type,
                    athlete.age_group,
                    month_ago,
                    now,
                    now=now,
                )
                three_implicit = count_implicit_absent_slots(
                    session,
                    athlete_id,
                    athlete.sport_type,
                    athlete.age_group,
                    three_months_ago,
                    now,
                    now=now,
                )

            message = f"📊 <b>СТАТИСТИКА СПОРТСМЕНА</b>\n\n"
            message += f"👤 <b>{html.escape(athlete.full_name)}</b>\n\n"
        
            message += f"<b>📈 ПО ПЕРИОДАМ</b>\n"
            message += f"<b>Неделя:</b>\n"
            message += f"• Посещено: {week_attended}/{week_trainings}\n"
            week_rate = round((week_attended / week_trainings * 100), 1) if week_trainings > 0 else 0
            message += f"• Посещаемость: {week_rate}%\n"
            if week_implicit:
                message += f"• <i>Без отметки тренера (учтено как не был): {week_implicit}</i>\n"
            message += "\n"

            message += f"<b>Месяц:</b>\n"
            message += f"• Посещено: {month_attended}/{month_trainings}\n"
            month_rate = round((month_attended / month_trainings * 100), 1) if month_trainings > 0 else 0
            message += f"• Посещаемость: {month_rate}%\n"
            if month_implicit:
                message += f"• <i>Без отметки тренера (учтено как не был): {month_implicit}</i>\n"
            message += "\n"

            message += f"<b>3 месяца:</b>\n"
            message += f"• Посещено: {three_months_attended}/{three_months_trainings}\n"
            three_months_rate = round((three_months_attended / three_months_trainings * 100), 1) if three_months_trainings > 0 else 0
            message += f"• Посещаемость: {three_months_rate}%\n"
            if three_implicit:
                message += f"• <i>Без отметки тренера (учтено как не был): {three_implicit}</i>\n"
            message += "\n"
        
            message += f"<b>📋 ОБЩАЯ СТАТИСТИКА</b>\n"
            message += f"• Всего посещено: {total_attended}\n"
            message += f"• Всего пропущено: {total_missed}\n"
            total_rate = round((total_attended / (total_attended + total_missed) * 100), 1) if (total_attended + total_missed) > 0 else 0
            message += f"• Общая посещаемость: {total_rate}%\n"
        
            keyboard = [
                [InlineKeyboardButton("🔙 Назад к карточке", callback_data=f"athlete_{athlete_id}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
        
            await query.edit_message_text(
                message,
                reply_markup=reply_markup,
                parse_mode='HTML'
            )
    
    except Exception as e:
        logger.error(f"❌ ОШИБКА ПРИ ПОКАЗЕ СТАТИСТИКИ: {e}", exc_info=True)
        await query.edit_message_text("❌ Ошибка при загрузке статистики")


async def show_restore_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать меню восстановления тренировок"""
    query = update.callback_query
    await query.answer()
    
    athlete_id = int(query.data.replace("restore_", ""))
    
    try:
        with get_db_session() as session:
            user = get_user_by_telegram_id(session, query.from_user.id)
        
            if not is_staff(user):
                await query.edit_message_text("❌ У вас нет доступа")
                return
        
            athlete = session.query(Athlete).filter_by(id=athlete_id).first()
            if not athlete:
                await query.edit_message_text("❌ Спортсмен не найден")
                return
        
            # Проверяем права
            if isinstance(user, Coach) and athlete.created_by != user.id:
                await query.edit_message_text("❌ Вы не можете восстанавливать тренировки для этого спортсмена")
                return
        
            coach_sport = coach_sport_type_name(user)
            subscription = subscription_for_coach_sport(athlete, coach_sport)
            if not subscription:
                await query.edit_message_text("❌ Нет активного абонемента для восстановления в этом направлении")
                return

            missed_attendances = session.query(Attendance).filter(
                Attendance.athlete_id == athlete_id,
                Attendance.subscription_id == subscription.id,
                Attendance.attended == False,
                Attendance.was_restored == False
            ).order_by(Attendance.created_at.desc()).limit(10).all()
        
            message = f"🔄 <b>ВОССТАНОВЛЕНИЕ ТРЕНИРОВОК</b>\n\n"
            message += f"👤 <b>{html.escape(athlete.full_name)}</b>\n"
            message += f"🎫 Абонемент #{subscription.id}\n"
            message += f"🏋️ Осталось тренировок: {subscription.trainings_remaining}\n\n"
        
            if not missed_attendances:
                message += "❌ Нет пропущенных тренировок для восстановления"
            else:
                message += f"<b>Пропущенные тренировки (последние {len(missed_attendances)}):</b>\n\n"
            
                keyboard = []
                for att in missed_attendances:
                    training_date = att.training.training_date.strftime('%d.%m.%Y %H:%M') if att.training else "—"
                    button_text = f"📅 {training_date}"
                    if len(button_text) > 64:
                        button_text = f"📅 {training_date[:50]}"
                    keyboard.append([
                        InlineKeyboardButton(button_text, callback_data=f"restore_att_{att.id}")
                    ])
            
                reply_markup = InlineKeyboardMarkup(keyboard)
            
                await query.edit_message_text(
                    message,
                    reply_markup=reply_markup,
                    parse_mode='HTML'
                )
                return
        
            keyboard = [
                [InlineKeyboardButton("🔙 Назад к карточке", callback_data=f"athlete_{athlete_id}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
        
            await query.edit_message_text(
                message,
                reply_markup=reply_markup,
                parse_mode='HTML'
            )
    
    except Exception as e:
        logger.error(f"❌ ОШИБКА ПРИ ПОКАЗЕ МЕНЮ ВОССТАНОВЛЕНИЯ: {e}", exc_info=True)
        await query.edit_message_text("❌ Ошибка при загрузке меню восстановления")


async def execute_restore_training(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Восстановить конкретную тренировку"""
    query = update.callback_query
    await query.answer()
    
    attendance_id = int(query.data.replace("restore_att_", ""))
    
    try:
        with get_db_session() as session:
            user = get_user_by_telegram_id(session, query.from_user.id)
        
            if not is_staff(user):
                await query.edit_message_text("❌ У вас нет доступа")
                return
        
            attendance = session.query(Attendance).filter_by(id=attendance_id).first()
            if not attendance:
                await query.edit_message_text("❌ Запись о посещении не найдена")
                return
        
            athlete = attendance.athlete
            subscription = attendance.subscription
        
            # Проверяем права
            if isinstance(user, Coach) and athlete.created_by != user.id:
                await query.edit_message_text("❌ Вы не можете восстанавливать тренировки для этого спортсмена")
                return
        
            # Проверяем, что тренировка еще не восстановлена
            if attendance.was_restored:
                await query.edit_message_text("❌ Эта тренировка уже была восстановлена")
                return
        
            # Проверяем, что это пропущенная тренировка
            if attendance.attended:
                await query.edit_message_text("❌ Можно восстановить только пропущенные тренировки")
                return

            # Массовая заморозка = период без изменения остатка/восстановлений.
            training_date = attendance.training.training_date if attendance.training else None
            if training_date and is_training_in_global_freeze(session, training_date):
                await query.edit_message_text(
                    "⛔️ В период массовой заморозки восстановление тренировок недоступно."
                    "\n\nСписание/восстановление в этот период не применяется."
                )
                return
        
            # Восстанавливаем тренировку
            attendance.was_restored = True
            attendance.restoration_reason = "Восстановлено тренером"
        
            # Возвращаем тренировку в абонемент
            if subscription:
                subscription.trainings_remaining = (subscription.trainings_remaining or 0) + 1
                subscription.total_restored = (subscription.total_restored or 0) + 1
            
                # Обновляем счетчик восстановлений за месяц
                if attendance.created_at:
                    now = now_moscow()
                    if attendance.created_at.year == now.year and attendance.created_at.month == now.month:
                        subscription.restored_this_month = (subscription.restored_this_month or 0) + 1
        
            session.commit()
        
            training_date = attendance.training.training_date.strftime('%d.%m.%Y %H:%M') if attendance.training else "—"
        
            message = f"✅ <b>ТРЕНИРОВКА ВОССТАНОВЛЕНА</b>\n\n"
            message += f"👤 <b>{html.escape(athlete.full_name)}</b>\n"
            message += f"📅 Тренировка: {training_date}\n"
            message += f"🎫 Осталось тренировок: {subscription.trainings_remaining if subscription else '—'}\n"
        
            keyboard = [
                [InlineKeyboardButton("🔄 Еще восстановить", callback_data=f"restore_{athlete.id}")],
                [InlineKeyboardButton("🔙 Назад к карточке", callback_data=f"athlete_{athlete.id}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
        
            await query.edit_message_text(
                message,
                reply_markup=reply_markup,
                parse_mode='HTML'
            )
    
    except Exception as e:
        logger.error(f"❌ ОШИБКА ПРИ ВОССТАНОВЛЕНИИ ТРЕНИРОВКИ: {e}", exc_info=True)
        session.rollback()
        await query.edit_message_text("❌ Ошибка при восстановлении тренировки")


async def select_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать список активных абонементов для выбора"""
    query = update.callback_query
    await query.answer()
    
    athlete_id = int(query.data.replace("select_sub_", ""))
    
    try:
        with get_db_session() as session:
            user = get_user_by_telegram_id(session, query.from_user.id)
        
            if not is_staff(user):
                await query.edit_message_text("❌ У вас нет доступа")
                return
        
            athlete = session.query(Athlete).filter_by(id=athlete_id).first()
            if not athlete:
                await query.edit_message_text("❌ Спортсмен не найден")
                return
        
            # Проверяем права
            if isinstance(user, Coach) and athlete.created_by != user.id:
                await query.edit_message_text("❌ Вы не можете просматривать этого спортсмена")
                return
        
            # Получаем все активные абонементы
            active_subs = [s for s in athlete.subscriptions if s.is_active]
        
            if not active_subs:
                await query.edit_message_text("❌ Нет активных абонементов")
                return
        
            if len(active_subs) == 1:
                only = active_subs[0]
                await show_subscription_card(
                    update,
                    context,
                    override_query_data=f"subscription_{only.id}",
                    skip_callback_answer=True,
                )
                return
        
            message = f"🔄 <b>ВЫБОР АБОНЕМЕНТА</b>\n\n"
            message += f"👤 <b>{html.escape(athlete.full_name)}</b>\n\n"
            message += f"Выберите абонемент для просмотра:\n\n"
        
            keyboard = []
        
            for sub in active_subs:
                status_icon = _status_icon_from_status_text(_format_subscription_status_ui(sub))
                sport_type_display = sub.sport_type or "—"
                trainings = f"{sub.trainings_remaining or 0}/{sub.trainings_total or 0}"
            
                button_text = f"{status_icon} {sport_type_display} ({trainings})"
                if len(button_text) > 64:
                    button_text = f"{status_icon} {sport_type_display}"
            
                keyboard.append([
                    InlineKeyboardButton(button_text, callback_data=f"view_sub_card_{sub.id}")
                ])
        
            keyboard.append([
                InlineKeyboardButton("🔙 Назад к карточке", callback_data=f"athlete_{athlete_id}")
            ])
        
            reply_markup = InlineKeyboardMarkup(keyboard)
        
            await query.edit_message_text(
                message,
                reply_markup=reply_markup,
                parse_mode='HTML'
            )
    
    except Exception as e:
        logger.error(f"❌ ОШИБКА ПРИ ВЫБОРЕ АБОНЕМЕНТА: {e}", exc_info=True)
        await query.edit_message_text("❌ Ошибка при загрузке абонементов")


async def view_subscription_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать карточку спортсмена с выбранным абонементом"""
    query = update.callback_query
    await query.answer()
    
    subscription_id = int(query.data.replace("view_sub_card_", ""))
    
    try:
        with get_db_session() as session:
            user = get_user_by_telegram_id(session, query.from_user.id)
        
            if not is_staff(user):
                await query.edit_message_text("❌ У вас нет доступа")
                return
        
            subscription = session.query(Subscription).filter_by(id=subscription_id).first()
            if not subscription:
                await query.edit_message_text("❌ Абонемент не найден")
                return
        
            athlete = subscription.athlete
        
            # Проверяем права
            if isinstance(user, Coach) and athlete.created_by != user.id:
                await query.edit_message_text("❌ Вы не можете просматривать этого спортсмена")
                return
        
            # Сохраняем выбранный абонемент в контексте и показываем карточку
            context.user_data['selected_subscription_id'] = subscription_id
        
            # Показываем карточку спортсмена
            await show_athlete_card(update, context)
    
    except Exception as e:
        logger.error(f"❌ ОШИБКА ПРИ ПРОСМОТРЕ АБОНЕМЕНТА: {e}", exc_info=True)
        await query.edit_message_text("❌ Ошибка при загрузке")


# --- Редактирование карточки спортсмена ---
EDIT_ATHLETE_NAME = 110
EDIT_ATHLETE_PHONE = 111
EDIT_ATHLETE_MEDICAL = 112

_EDIT_ATHLETE_ID_RE = re.compile(
    r"^edit_(?:name|phone|medical|cancel)_(\d+)$"
)


def _parse_edit_athlete_callback_id(data: str) -> Optional[int]:
    m = _EDIT_ATHLETE_ID_RE.match(data or "")
    return int(m.group(1)) if m else None


def _clear_edit_athlete_state(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("edit_athlete_id", None)


# Единые заголовки и подсказки для шагов редактирования карточки
_EDIT_FIELD_UI = {
    "name": {
        "icon": "📝",
        "title": "РЕДАКТИРОВАНИЕ ФИО",
        "enter_new_start": "Введите новое ФИО",
        "enter_new_retry": "Введите новое ФИО или нажмите «Отмена».",
        "empty": "—",
    },
    "phone": {
        "icon": "📞",
        "title": "РЕДАКТИРОВАНИЕ ТЕЛЕФОНА",
        "enter_new_start": "Введите новый телефон",
        "enter_new_retry": "Введите новый телефон или нажмите «Отмена».",
        "empty": "Не указан",
        "hint": "Формат: XXX-XXX-XX-XX (пример: 925-123-45-67)",
    },
    "medical": {
        "icon": "🏥",
        "title": "РЕДАКТИРОВАНИЕ МЕДИЦИНСКОЙ ИНФОРМАЦИИ",
        "enter_new_start": "Введите новую медицинскую информацию",
        "enter_new_retry": "Введите новую медицинскую информацию или нажмите «Отмена».",
        "empty": "Не указана",
    },
}


def _edit_athlete_field_message(
    field_key: str,
    current_value: Optional[str],
    *,
    unchanged: bool = False,
) -> str:
    """
    Текст шага редактирования.
    unchanged=False — открытие: заголовок с текущим значением и «Введите новое …».
    unchanged=True — ввод совпал с БД: значение и «— уже используется.».
    """
    ui = _EDIT_FIELD_UI[field_key]
    display = (current_value or "").strip() or ui["empty"]
    if unchanged:
        parts = [
            f"{ui['icon']} <b>{ui['title']}</b>\n",
            f"{html.escape(display)} — уже используется.\n",
            ui["enter_new_retry"],
        ]
    else:
        parts = [
            f"{ui['icon']} <b>{ui['title']} - {html.escape(display)}</b>\n",
            ui["enter_new_start"],
        ]
        hint = ui.get("hint")
        if hint:
            parts.append(hint)
    return "\n".join(parts)


def _edit_athlete_after_save_keyboard(athlete_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Продолжить редактирование", callback_data=f"edit_{athlete_id}")],
        [InlineKeyboardButton("👤 К карточке", callback_data=f"athlete_{athlete_id}")],
    ])


async def _edit_athlete_unchanged_reply(
    update: Update,
    *,
    field_key: str,
    current_value: str,
    cancel_keyboard: InlineKeyboardMarkup,
) -> None:
    """Сообщение, если новое значение совпадает с тем, что уже в БД."""
    await update.message.reply_text(
        _edit_athlete_field_message(field_key, current_value, unchanged=True),
        reply_markup=cancel_keyboard,
        parse_mode="HTML",
    )


async def _edit_athlete_saved_reply(
    update: Update,
    athlete_id: int,
    *,
    field_label: str,
    new_value: str,
) -> None:
    await update.message.reply_text(
        f"✅ <b>{field_label} обновлено</b>\n\n{html.escape(new_value)}",
        reply_markup=_edit_athlete_after_save_keyboard(athlete_id),
        parse_mode="HTML",
    )


def _edit_athlete_cancel_keyboard(athlete_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Отмена", callback_data=f"edit_cancel_{athlete_id}")],
    ])


def _edit_athlete_menu_markup(athlete_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 ФИО", callback_data=f"edit_name_{athlete_id}")],
        [InlineKeyboardButton("📞 Телефон", callback_data=f"edit_phone_{athlete_id}")],
        [InlineKeyboardButton("🏥 Медицинская информация", callback_data=f"edit_medical_{athlete_id}")],
        [InlineKeyboardButton("🔙 Назад к карточке", callback_data=f"athlete_{athlete_id}")],
    ])


def _edit_athlete_menu_text(athlete: Athlete) -> str:
    message = "✏️ <b>РЕДАКТИРОВАНИЕ ДАННЫХ</b>\n\n"
    message += f"👤 <b>{html.escape(athlete.full_name)}</b>\n\n"
    message += "Выберите, что хотите изменить:"
    return message


def _load_athlete_for_edit(session, user, athlete_id: int):
    """
    Проверка доступа к редактированию.
    Возвращает (athlete, error_text) — при ошибке athlete=None.
    """
    if not is_staff(user):
        return None, "❌ У вас нет доступа"
    athlete = session.query(Athlete).filter_by(id=athlete_id).first()
    if not athlete:
        return None, "❌ Спортсмен не найден"
    if isinstance(user, Coach) and athlete.created_by != user.id:
        return None, "❌ Вы не можете редактировать этого спортсмена"
    return athlete, None


async def show_edit_athlete_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать меню редактирования данных спортсмена"""
    query = update.callback_query
    await query.answer()

    try:
        athlete_id = int(query.data.replace("edit_", ""))
    except ValueError:
        await query.edit_message_text("❌ Некорректный запрос")
        return

    _clear_edit_athlete_state(context)

    try:
        with get_db_session() as session:
            user = get_user_by_telegram_id(session, query.from_user.id)
            athlete, err = _load_athlete_for_edit(session, user, athlete_id)
            if err:
                await query.edit_message_text(err)
                return

            await query.edit_message_text(
                _edit_athlete_menu_text(athlete),
                reply_markup=_edit_athlete_menu_markup(athlete_id),
                parse_mode="HTML",
            )

    except Exception as e:
        logger.error(f"❌ ОШИБКА ПРИ ПОКАЗЕ МЕНЮ РЕДАКТИРОВАНИЯ: {e}", exc_info=True)
        await query.edit_message_text("❌ Ошибка при загрузке меню редактирования")


async def start_edit_athlete_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    athlete_id = _parse_edit_athlete_callback_id(query.data)
    if athlete_id is None:
        await query.edit_message_text("❌ Некорректный запрос")
        return ConversationHandler.END

    try:
        with get_db_session() as session:
            user = get_user_by_telegram_id(session, query.from_user.id)
            athlete, err = _load_athlete_for_edit(session, user, athlete_id)
            if err:
                await query.edit_message_text(err)
                return ConversationHandler.END

            context.user_data["edit_athlete_id"] = athlete_id
            await query.edit_message_text(
                _edit_athlete_field_message("name", athlete.full_name),
                reply_markup=_edit_athlete_cancel_keyboard(athlete_id),
                parse_mode="HTML",
            )
            return EDIT_ATHLETE_NAME
    except Exception as e:
        logger.error(f"❌ ОШИБКА СТАРТА РЕДАКТИРОВАНИЯ ФИО: {e}", exc_info=True)
        await query.edit_message_text("❌ Ошибка")
        return ConversationHandler.END


async def start_edit_athlete_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    athlete_id = _parse_edit_athlete_callback_id(query.data)
    if athlete_id is None:
        await query.edit_message_text("❌ Некорректный запрос")
        return ConversationHandler.END

    try:
        with get_db_session() as session:
            user = get_user_by_telegram_id(session, query.from_user.id)
            athlete, err = _load_athlete_for_edit(session, user, athlete_id)
            if err:
                await query.edit_message_text(err)
                return ConversationHandler.END

            context.user_data["edit_athlete_id"] = athlete_id
            await query.edit_message_text(
                _edit_athlete_field_message("phone", athlete.phone),
                reply_markup=_edit_athlete_cancel_keyboard(athlete_id),
                parse_mode="HTML",
            )
            return EDIT_ATHLETE_PHONE
    except Exception as e:
        logger.error(f"❌ ОШИБКА СТАРТА РЕДАКТИРОВАНИЯ ТЕЛЕФОНА: {e}", exc_info=True)
        await query.edit_message_text("❌ Ошибка")
        return ConversationHandler.END


async def start_edit_athlete_medical(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    athlete_id = _parse_edit_athlete_callback_id(query.data)
    if athlete_id is None:
        await query.edit_message_text("❌ Некорректный запрос")
        return ConversationHandler.END

    try:
        with get_db_session() as session:
            user = get_user_by_telegram_id(session, query.from_user.id)
            athlete, err = _load_athlete_for_edit(session, user, athlete_id)
            if err:
                await query.edit_message_text(err)
                return ConversationHandler.END

            context.user_data["edit_athlete_id"] = athlete_id
            await query.edit_message_text(
                _edit_athlete_field_message("medical", athlete.medical_info),
                reply_markup=_edit_athlete_cancel_keyboard(athlete_id),
                parse_mode="HTML",
            )
            return EDIT_ATHLETE_MEDICAL
    except Exception as e:
        logger.error(f"❌ ОШИБКА СТАРТА РЕДАКТИРОВАНИЯ МЕДИЦИНЫ: {e}", exc_info=True)
        await query.edit_message_text("❌ Ошибка")
        return ConversationHandler.END


async def save_edit_athlete_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    athlete_id = context.user_data.get("edit_athlete_id")
    if not athlete_id:
        return ConversationHandler.END

    user_text = (update.message.text or "").strip()
    if user_text in MENU_BUTTONS:
        _clear_edit_athlete_state(context)
        await update.message.reply_text("✅ Редактирование отменено.")
        return ConversationHandler.END

    full_name = normalize_full_name(user_text)
    if is_phone_number(full_name):
        await update.message.reply_text(
            "❌ <b>Это похоже на телефон, а не на ФИО.</b>\n\nВведите ФИО кириллицей:",
            parse_mode="HTML",
        )
        return EDIT_ATHLETE_NAME

    ok, normalized, error = validate_full_name_strict(full_name)
    if not ok:
        await update.message.reply_text(
            f"❌ {html.escape(error or 'Некорректное ФИО')}\n\nПопробуйте ещё раз:",
            parse_mode="HTML",
        )
        return EDIT_ATHLETE_NAME

    try:
        with get_db_session() as session:
            user = get_user_by_telegram_id(session, update.effective_user.id)
            athlete, err = _load_athlete_for_edit(session, user, athlete_id)
            if err:
                await update.message.reply_text(err)
                _clear_edit_athlete_state(context)
                return ConversationHandler.END

            if normalize_full_name(athlete.full_name or "") == normalized:
                await _edit_athlete_unchanged_reply(
                    update,
                    field_key="name",
                    current_value=athlete.full_name,
                    cancel_keyboard=_edit_athlete_cancel_keyboard(athlete_id),
                )
                return EDIT_ATHLETE_NAME

            athlete.full_name = normalized
            session.commit()

        _clear_edit_athlete_state(context)
        await _edit_athlete_saved_reply(
            update, athlete_id, field_label="ФИО", new_value=normalized
        )
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"❌ ОШИБКА СОХРАНЕНИЯ ФИО: {e}", exc_info=True)
        await update.message.reply_text("❌ Ошибка при сохранении")
        return EDIT_ATHLETE_NAME


async def save_edit_athlete_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    athlete_id = context.user_data.get("edit_athlete_id")
    if not athlete_id:
        return ConversationHandler.END

    user_text = update.message.text or ""
    if user_text in MENU_BUTTONS:
        _clear_edit_athlete_state(context)
        await update.message.reply_text("✅ Редактирование отменено.")
        return ConversationHandler.END

    if not has_digits(user_text) or is_valid_name_format(user_text):
        await update.message.reply_text(
            "❌ <b>Это похоже на ФИО, а не на телефон!</b>\n\n"
            "Введите номер в формате <b>XXX-XXX-XX-XX</b>",
            parse_mode="HTML",
        )
        return EDIT_ATHLETE_PHONE

    cleaned_input = re.sub(r"[^\d-]", "", user_text)
    phone_pattern = r"^\d{3}-\d{3}-\d{2}-\d{2}$"
    if not re.match(phone_pattern, cleaned_input):
        await update.message.reply_text(
            "❌ <b>Неверный формат телефона!</b>\n\n"
            "Формат: <b>XXX-XXX-XX-XX</b>\n<i>Пример: 925-123-45-67</i>",
            parse_mode="HTML",
        )
        return EDIT_ATHLETE_PHONE

    full_phone = f"+7-{cleaned_input}"

    try:
        with get_db_session() as session:
            user = get_user_by_telegram_id(session, update.effective_user.id)
            athlete, err = _load_athlete_for_edit(session, user, athlete_id)
            if err:
                await update.message.reply_text(err)
                _clear_edit_athlete_state(context)
                return ConversationHandler.END

            existing = (
                session.query(Athlete)
                .filter(Athlete.phone == full_phone, Athlete.id != athlete_id)
                .first()
            )
            if existing:
                await update.message.reply_text(
                    f"❌ <b>Этот телефон уже занят</b>\n\n"
                    f"ФИО: {html.escape(existing.full_name)}\n\n"
                    "Введите другой номер:",
                    parse_mode="HTML",
                )
                return EDIT_ATHLETE_PHONE

            if (athlete.phone or "") == full_phone:
                await _edit_athlete_unchanged_reply(
                    update,
                    field_key="phone",
                    current_value=athlete.phone,
                    cancel_keyboard=_edit_athlete_cancel_keyboard(athlete_id),
                )
                return EDIT_ATHLETE_PHONE

            athlete.phone = full_phone
            session.commit()

        _clear_edit_athlete_state(context)
        await _edit_athlete_saved_reply(
            update, athlete_id, field_label="Телефон", new_value=full_phone
        )
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"❌ ОШИБКА СОХРАНЕНИЯ ТЕЛЕФОНА: {e}", exc_info=True)
        await update.message.reply_text("❌ Ошибка при сохранении")
        return EDIT_ATHLETE_PHONE


async def save_edit_athlete_medical(update: Update, context: ContextTypes.DEFAULT_TYPE):
    athlete_id = context.user_data.get("edit_athlete_id")
    if not athlete_id:
        return ConversationHandler.END

    user_text = (update.message.text or "").strip()
    if user_text in MENU_BUTTONS:
        _clear_edit_athlete_state(context)
        await update.message.reply_text("✅ Редактирование отменено.")
        return ConversationHandler.END

    if user_text.lower() == "нет":
        medical_info = "Нет противопоказаний"
    else:
        medical_info = user_text

    try:
        with get_db_session() as session:
            user = get_user_by_telegram_id(session, update.effective_user.id)
            athlete, err = _load_athlete_for_edit(session, user, athlete_id)
            if err:
                await update.message.reply_text(err)
                _clear_edit_athlete_state(context)
                return ConversationHandler.END

            stored_medical = (athlete.medical_info or "").strip()
            if stored_medical == medical_info.strip():
                await _edit_athlete_unchanged_reply(
                    update,
                    field_key="medical",
                    current_value=athlete.medical_info,
                    cancel_keyboard=_edit_athlete_cancel_keyboard(athlete_id),
                )
                return EDIT_ATHLETE_MEDICAL

            athlete.medical_info = medical_info
            session.commit()

        _clear_edit_athlete_state(context)
        await _edit_athlete_saved_reply(
            update,
            athlete_id,
            field_label="Медицинская информация",
            new_value=medical_info,
        )
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"❌ ОШИБКА СОХРАНЕНИЯ МЕДИЦИНЫ: {e}", exc_info=True)
        await update.message.reply_text("❌ Ошибка при сохранении")
        return EDIT_ATHLETE_MEDICAL


async def cancel_edit_athlete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отмена редактирования по inline-кнопке — возврат в меню редактирования."""
    query = update.callback_query
    await query.answer()
    athlete_id = _parse_edit_athlete_callback_id(query.data)
    _clear_edit_athlete_state(context)
    if athlete_id is None:
        await query.edit_message_text("✅ Редактирование отменено.")
        return ConversationHandler.END

    try:
        with get_db_session() as session:
            user = get_user_by_telegram_id(session, query.from_user.id)
            athlete, err = _load_athlete_for_edit(session, user, athlete_id)
            if err:
                await query.edit_message_text(err)
                return ConversationHandler.END

            await query.edit_message_text(
                _edit_athlete_menu_text(athlete),
                reply_markup=_edit_athlete_menu_markup(athlete_id),
                parse_mode="HTML",
            )
    except Exception as e:
        logger.error(f"❌ ОШИБКА ОТМЕНЫ РЕДАКТИРОВАНИЯ: {e}", exc_info=True)
        await query.edit_message_text("✅ Редактирование отменено.")
    return ConversationHandler.END


async def cancel_edit_athlete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отмена редактирования командой /cancel."""
    athlete_id = context.user_data.get("edit_athlete_id")
    _clear_edit_athlete_state(context)
    if athlete_id:
        await update.message.reply_text(
            "✅ Редактирование отменено.\n"
            "Откройте карточку спортсмена снова из списка.",
        )
    else:
        await update.message.reply_text("Нет активного редактирования.")
    return ConversationHandler.END


_FREEZE_CAL_RE = re.compile(r"^freeze_cal_(\d+)_(\d+)_(\d{4})_(\d{1,2})$")
_FREEZE_DATE_RE = re.compile(r"^freeze_date_(\d+)_(\d+)_(\d{4})_(\d{1,2})_(\d{1,2})$")


def _build_freeze_calendar(
    athlete_id: int,
    back_subscription_id: int,
    sport_type: str,
    age_group: str,
    year: int,
    month: int,
) -> InlineKeyboardMarkup:
    """
    Календарь выбора даты окончания заморозки.
    Аналогичен календарю активации, но для выбора даты окончания заморозки.
    """
    schedule = _get_schedule(sport_type, age_group)
    training_days = set(schedule["days"]) if schedule else set()

    today = now_moscow().date()

    cal = py_calendar.monthcalendar(year, month)

    keyboard = []

    # Строка дней недели
    day_names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    keyboard.append([InlineKeyboardButton(f"{d}.", callback_data="freeze_ignore") for d in day_names])

    # Ровно 5 недель
    weeks_to_show = cal[:5]
    while len(weeks_to_show) < 5:
        weeks_to_show.append([0, 0, 0, 0, 0, 0, 0])

    for week in weeks_to_show:
        row = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(" ", callback_data="freeze_ignore"))
                continue

            date_obj = datetime(year, month, day).date()
            weekday = date_obj.weekday()

            has_scheduled_training = weekday in training_days
            # Разрешаем выбирать только будущие даты (после сегодня)
            is_future = date_obj > today
            enabled = has_scheduled_training and is_future

            if date_obj == today:
                btn_text = f"[{day:2d}]"
            elif has_scheduled_training:
                btn_text = f"({day:2d})"
            else:
                btn_text = f"{day:2d}"

            cb = (
                f"freeze_date_{athlete_id}_{back_subscription_id}_{year}_{month}_{day}"
                if enabled
                else "freeze_ignore"
            )
            row.append(InlineKeyboardButton(btn_text, callback_data=cb))

        keyboard.append(row)

    # Навигация
    prev_year, prev_month = year, month - 1
    next_year, next_month = year, month + 1
    if prev_month == 0:
        prev_month = 12
        prev_year -= 1
    if next_month == 13:
        next_month = 1
        next_year += 1

    keyboard.append([
        InlineKeyboardButton(
            "◀️ Предыдущий",
            callback_data=f"freeze_cal_{athlete_id}_{back_subscription_id}_{prev_year}_{prev_month}",
        ),
        InlineKeyboardButton(
            "Следующий ▶️",
            callback_data=f"freeze_cal_{athlete_id}_{back_subscription_id}_{next_year}_{next_month}",
        ),
    ])

    # Кнопка "Сегодня"
    now = now_moscow().date()
    if month != now.month or year != now.year:
        keyboard.append([
            InlineKeyboardButton(
                "📅 Сегодня",
                callback_data=f"freeze_cal_{athlete_id}_{back_subscription_id}_{now.year}_{now.month}",
            )
        ])

    # Навигация/выход
    keyboard.append([
        InlineKeyboardButton("🔙 Назад", callback_data=f"subscription_{back_subscription_id}"),
        InlineKeyboardButton("🏠 В меню", callback_data="back_to_menu_main"),
    ])

    return InlineKeyboardMarkup(keyboard)


async def handle_freeze_subscription_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало заморозки спортсмена (все активные абонементы) — календарь."""
    query = update.callback_query
    await query.answer()

    # freeze_athlete_{athlete_id}_{back_subscription_id}
    m = re.match(r"^freeze_athlete_(\d+)_(\d+)$", query.data or "")
    if not m:
        await query.edit_message_text("❌ Некорректные данные")
        return
    athlete_id = int(m.group(1))
    back_subscription_id = int(m.group(2))

    try:
        with get_db_session() as session:
            user = get_user_by_telegram_id(session, query.from_user.id)
            if not is_staff(user):
                await query.edit_message_text("❌ У вас нет доступа")
                return

            athlete = session.query(Athlete).filter_by(id=athlete_id).first()
            if not athlete:
                await query.edit_message_text("❌ Спортсмен не найден")
                return

            if isinstance(user, Coach) and athlete.created_by != user.id:
                await query.edit_message_text("❌ Вы не можете изменять этого спортсмена")
                return

            active_subs = [s for s in athlete.subscriptions if s.is_active]
            if not active_subs:
                await query.edit_message_text("❌ Нет активного абонемента")
                return

            expire_stale_subscription_freezes(session, athlete_id=athlete_id, commit=True)
            session.refresh(athlete)
            active_subs = [s for s in athlete.subscriptions if s.is_active]

            if any(subscription_is_currently_frozen(s) for s in active_subs):
                await query.edit_message_text("❌ У спортсмена уже есть заморозка")
                return

            back_sub = session.query(Subscription).filter_by(id=back_subscription_id).first()
            if not back_sub or back_sub.athlete_id != athlete_id:
                back_subscription_id = min(s.id for s in active_subs)

            ref_sub = session.query(Subscription).filter_by(id=back_subscription_id).first()
            sport_type = (ref_sub.sport_type if ref_sub else None) or athlete.sport_type
            age_group = athlete.age_group

            now = now_moscow()
            reply_markup = _build_freeze_calendar(
                athlete_id, back_subscription_id, sport_type, age_group, now.year, now.month
            )

            await query.edit_message_text(
                f"👤 <b>{html.escape(athlete.full_name)}</b>\n\n"
                f"❄️ <b>ЗАМОРОЗКА СПОРТСМЕНА</b>\n\n"
                f"Будут заморожены <b>все активные абонементы</b>.\n"
                f"Выберите дату <b>окончания заморозки</b> (тренировочный день):",
                reply_markup=reply_markup,
                parse_mode="HTML"
            )
    except Exception as e:
        logger.error(f"❌ ОШИБКА ПРИ НАЧАЛЕ ЗАМОРОЗКИ: {e}", exc_info=True)
        await query.edit_message_text("❌ Ошибка при загрузке календаря заморозки")


async def handle_freeze_calendar_nav(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Навигация по календарю заморозки"""
    query = update.callback_query
    await query.answer()

    m = _FREEZE_CAL_RE.match(query.data or "")
    if not m:
        return
    athlete_id = int(m.group(1))
    back_subscription_id = int(m.group(2))
    year = int(m.group(3))
    month = int(m.group(4))

    with get_db_session() as session:
        user = get_user_by_telegram_id(session, query.from_user.id)
        if not is_staff(user):
            await query.edit_message_text("❌ У вас нет доступа")
            return

        athlete = session.query(Athlete).filter_by(id=athlete_id).first()
        if not athlete:
            await query.edit_message_text("❌ Спортсмен не найден")
            return

        ref_sub = session.query(Subscription).filter_by(id=back_subscription_id).first()
        sport_type = (ref_sub.sport_type if ref_sub else None) or athlete.sport_type
        age_group = athlete.age_group

        reply_markup = _build_freeze_calendar(
            athlete_id, back_subscription_id, sport_type, age_group, year, month
        )
        await query.edit_message_reply_markup(reply_markup=reply_markup)


async def handle_freeze_date_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выбор даты окончания заморозки"""
    query = update.callback_query
    await query.answer()

    m = _FREEZE_DATE_RE.match(query.data or "")
    if not m:
        return
    athlete_id = int(m.group(1))
    back_subscription_id = int(m.group(2))
    year = int(m.group(3))
    month = int(m.group(4))
    day = int(m.group(5))

    try:
        with get_db_session() as session:
            user = get_user_by_telegram_id(session, query.from_user.id)
            if not is_staff(user):
                await query.edit_message_text("❌ У вас нет доступа")
                return

            athlete = session.query(Athlete).filter_by(id=athlete_id).first()
            if not athlete:
                await query.edit_message_text("❌ Спортсмен не найден")
                return

            if isinstance(user, Coach) and athlete.created_by != user.id:
                await query.edit_message_text("❌ Вы не можете изменять этого спортсмена")
                return

            selected_date = datetime(year, month, day, 0, 0, 0)

            from database.db_utils import freeze_athlete

            coach_db_id = user.id if isinstance(user, Coach) else None
            result = freeze_athlete(
                session,
                athlete_id,
                selected_date,
                initiated_by_coach_id=coach_db_id,
            )

            if not result["success"]:
                await query.edit_message_text(f"❌ {result['message']}")
                return

            await show_subscription_card(
                update,
                context,
                override_query_data=f"subscription_{back_subscription_id}",
                skip_callback_answer=True,
            )
    except Exception as e:
        logger.error(f"❌ ОШИБКА ПРИ ЗАМОРОЗКЕ: {e}", exc_info=True)
        await query.edit_message_text("❌ Ошибка при заморозке абонемента")


async def handle_freeze_ignore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Игнор-кнопка для календаря заморозки"""
    query = update.callback_query
    await query.answer()


async def handle_unfreeze_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Разморозить спортсмена (все активные абонементы)."""
    query = update.callback_query
    await query.answer()

    m = re.match(r"^unfreeze_athlete_(\d+)_(\d+)$", query.data or "")
    if not m:
        await query.edit_message_text("❌ Некорректные данные")
        return
    athlete_id = int(m.group(1))
    back_subscription_id = int(m.group(2))

    try:
        with get_db_session() as session:
            user = get_user_by_telegram_id(session, query.from_user.id)
            if not is_staff(user):
                await query.edit_message_text("❌ У вас нет доступа")
                return

            athlete = session.query(Athlete).filter_by(id=athlete_id).first()
            if not athlete:
                await query.edit_message_text("❌ Спортсмен не найден")
                return

            if isinstance(user, Coach) and athlete.created_by != user.id:
                await query.edit_message_text("❌ Вы не можете изменять этого спортсмена")
                return

            from database.db_utils import unfreeze_athlete

            result = unfreeze_athlete(session, athlete_id)

            if not result["success"]:
                await query.edit_message_text(f"❌ {result['message']}")
                return

            await show_subscription_card(
                update,
                context,
                override_query_data=f"subscription_{back_subscription_id}",
                skip_callback_answer=True,
            )
    except Exception as e:
        logger.error(f"❌ ОШИБКА ПРИ РАЗМОРОЗКЕ: {e}", exc_info=True)
        await query.edit_message_text("❌ Ошибка при разморозке абонемента")
