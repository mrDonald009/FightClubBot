import logging
import re
from datetime import date, datetime, timedelta
import calendar as py_calendar
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes, CallbackQueryHandler, CommandHandler
from database.models import Session, Athlete, Subscription, Training, Attendance, Coach, Admin, GlobalFreeze, VisitHistory
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
    individual_slot_conflicts,
    individual_slot_training_ids,
    iter_allowed_individual_starts,
)
from typing import List, Optional, Union

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


def get_coach_sport_type(user: Union[Coach, Admin]) -> str:
    """Получить вид спорта тренера из связи или строки (для обратной совместимости)"""
    if isinstance(user, Coach):
        if user.sport_type_rel:
            return user.sport_type_rel.name
        elif user.sport_type:
            return user.sport_type
    return None


def _is_individual_subscription(sub: Subscription) -> bool:
    st = (getattr(sub, "subscription_type", None) or "").strip().lower()
    if st == "individual":
        return True
    dk = (getattr(sub, "discipline_key", None) or "").strip().lower()
    return "individual" in dk


def _individual_subscription_button_label(sub: Subscription) -> str:
    """Подпись кнопки: индивидуальная бронь с датой/временем слота."""
    status_icon = _status_icon_from_status_text(_format_subscription_status_ui(sub))
    if sub.start_date:
        slot = sub.start_date.strftime("%d.%m.%Y %H:%M")
    else:
        slot = "без даты"
    sport = (sub.sport_type or "—").strip()
    return f"{status_icon} Инд. {slot} ({sport})"


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
    if (subscription.frozen_training_days_total or 0) > 0 and not subscription.is_frozen:
        return f" (продлена на {subscription.frozen_training_days_total} тр. дней)"
    return ""


def _format_subscription_status_ui(subscription: Subscription) -> str:
    """
    Единое отображение статуса абонемента в UI (по МСК):
    - Истек N дней назад
    - Действует, осталось N дней
    - fallback в базовый статус checker
    """
    if subscription and subscription.end_date:
        now = now_moscow()
        if subscription.end_date < now:
            days_expired = (now - subscription.end_date).days
            return f"🔴 Истек {days_expired} дней назад"
        if subscription.is_active:
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
        end_date = _calculate_12th_training_date(start_date, sport_type, age_group)
    elif subscription.subscription_type == "single":
        end_date = training_end_time(start_date)
    elif subscription.subscription_type == "individual":
        end_date = individual_training_end_time(start_date)
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

    session = Session()
    try:
        user = get_user_by_telegram_id(session, query.from_user.id)
        if not user or get_user_role(user) not in ['coach', 'admin']:
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
    finally:
        session.close()


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

    session = Session()
    try:
        user = get_user_by_telegram_id(session, query.from_user.id)
        if not user or get_user_role(user) not in ['coach', 'admin']:
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
    finally:
        session.close()


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
    session = Session()
    try:
        user = get_user_by_telegram_id(session, query.from_user.id)
        if not user or get_user_role(user) not in ["coach", "admin"]:
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
    finally:
        session.close()


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
    session = Session()
    try:
        user = get_user_by_telegram_id(session, query.from_user.id)
        if not user or get_user_role(user) not in ["coach", "admin"]:
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
    finally:
        session.close()


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

    session = Session()
    try:
        user = get_user_by_telegram_id(session, query.from_user.id)
        if not user or get_user_role(user) not in ["coach", "admin"]:
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
    finally:
        session.close()


async def handle_activation_shift_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отмена сдвига: календарь активации или карточка абонемента."""
    query = update.callback_query
    m = re.match(r"^act_shift_cancel_(\d+)$", (query.data or "").strip())
    if not m:
        await query.answer()
        return

    subscription_id = int(m.group(1))
    session = Session()
    try:
        user = get_user_by_telegram_id(session, query.from_user.id)
        if not user or get_user_role(user) not in ["coach", "admin"]:
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
    finally:
        session.close()


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

    session = Session()
    try:
        user = get_user_by_telegram_id(session, user_id)

        if not user or get_user_role(user) not in ['coach', 'admin']:
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
        
        coach_sport = get_coach_sport_type(user) if isinstance(user, Coach) else None
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
            InlineKeyboardButton("📋 К списку", callback_data="back_to_list"),
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
    finally:
        session.close()


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

    session = Session()
    try:
        user = get_user_by_telegram_id(session, query.from_user.id)

        if not user or get_user_role(user) not in ['coach', 'admin']:
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
            coach_sport_hint = get_coach_sport_type(user) if isinstance(user, Coach) else None
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
                        coach_sport_type = get_coach_sport_type(user)
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

        # Для маршрута subscription_athlete_* при одновременных активных
        # group + individual показываем раздельный экран выбора, а не одну карточку.
        if subscription_id is None:
            coach_sport_type = get_coach_sport_type(user) if isinstance(user, Coach) else None
            active_subs = [s for s in athlete.subscriptions if s.is_active]
            if coach_sport_type:
                active_subs = [s for s in active_subs if (s.sport_type or "").strip() == coach_sport_type]

            individual_subs = sorted(
                [s for s in active_subs if _is_individual_subscription(s)],
                key=lambda s: (s.start_date or datetime.min, s.id or 0),
            )
            group_subs = sorted(
                [s for s in active_subs if not _is_individual_subscription(s)],
                key=lambda s: -(s.id or 0),
            )
            need_picker = len(group_subs) + len(individual_subs) > 1
            if need_picker:
                message = f"👤 <b>{html.escape(athlete.full_name)}</b>\n\n"
                message += "🎫 <b>АБОНЕМЕНТЫ</b>\n\n"
                if len(individual_subs) > 1:
                    message += (
                        f"Индивидуальных броней: <b>{len(individual_subs)}</b>. "
                        "Выберите запись:\n\n"
                    )
                else:
                    message += "Выберите, какой абонемент открыть:\n\n"

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
                for sub in individual_subs:
                    keyboard.append([
                        InlineKeyboardButton(
                            _individual_subscription_button_label(sub),
                            callback_data=f"subscription_{sub.id}",
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
            coach_sport_type = get_coach_sport_type(user) if isinstance(user, Coach) else None
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
                    InlineKeyboardButton("📜 История абонемента", callback_data=f"subscription_history_{athlete_id}")
                ])
                keyboard.append([
                    InlineKeyboardButton("🔙 Назад к карточке", callback_data=f"athlete_{athlete_id}")
                ])
                
                reply_markup = InlineKeyboardMarkup(keyboard)
                await query.edit_message_text(message, reply_markup=reply_markup, parse_mode='HTML')
                return
            
            # Проверяем, есть ли абонемент по виду спорта текущего тренера
            coach_sport_sub = None
            coach_sport_type = get_coach_sport_type(user) if isinstance(user, Coach) else None
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
                InlineKeyboardButton("📜 История абонемента", callback_data=f"subscription_history_{athlete_id}")
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
        
        # Статус заморозки
        if subscription.is_frozen and subscription.frozen_until:
            frozen_until_str = subscription.frozen_until.strftime('%d.%m.%Y %H:%M')
            message += f"• ❄️ Заморожен до: {frozen_until_str}\n"
            if subscription.frozen_from:
                frozen_from_str = subscription.frozen_from.strftime('%d.%m.%Y %H:%M')
                message += f"• ❄️ Заморожен с: {frozen_from_str}\n"
        if (subscription.frozen_training_days_total or 0) > 0:
            message += f"• ❄️ Заморожено тренировочных дней: {subscription.frozen_training_days_total}\n"

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
                if s.is_active and _is_individual_subscription(s)
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
        any_active_frozen = any(
            s.is_active and s.is_frozen for s in athlete.subscriptions
        )
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

        # При схеме 1 спортсмен = 1 абонемент кнопка "История абонемента" не нужна
        # (историю посещений можно открыть через "📅 Посещения" в карточке спортсмена)

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
    finally:
        session.close()


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
    
    session = Session()
    try:
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

            if (subscription.frozen_training_days_total or 0) > 0:
                message_text += f"• Заморожено тренировочных дней: {subscription.frozen_training_days_total}\n"

            message_text += f"\n<b>📊 ИСПОЛЬЗОВАНИЕ</b>\n"
            message_text += f"{progress_bar} {usage_percent}%\n"
            if len(active_subs) > 1 and si < len(active_subs):
                message_text += "\n"

        # Создаем инлайн клавиатуру
        keyboard = [
            [InlineKeyboardButton("📜 История абонемента", callback_data=f"subscription_history_athlete_{athlete.id}")],
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
    finally:
        session.close()


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
    
    session = Session()
    try:
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
    finally:
        session.close()


async def show_subscription_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать историю абонементов спортсмена"""
    query = update.callback_query
    await query.answer()
    
    # Получаем athlete_id из callback_data: subscription_history_123 или subscription_history_athlete_123
    callback_data = query.data.replace("subscription_history_", "")
    if callback_data.startswith("athlete_"):
        athlete_id = int(callback_data.replace("athlete_", ""))
    else:
        athlete_id = int(callback_data)
    
    session = Session()
    try:
        user = get_user_by_telegram_id(session, query.from_user.id)
        
        if not user:
            await query.edit_message_text("❌ Пользователь не найден")
            return
        
        # Если спортсмен смотрит свою историю, получаем athlete по id (у Athlete user.id = athletes.id)
        if isinstance(user, Athlete) and callback_data.startswith("athlete_"):
            athlete = session.query(Athlete).filter_by(id=user.id).first()
            if not athlete:
                await query.edit_message_text("❌ Профиль спортсмена не найден")
                return
            athlete_id = athlete.id
        else:
            # Получаем спортсмена по переданному ID
            athlete = session.query(Athlete).filter_by(id=athlete_id).first()
            if not athlete:
                await query.edit_message_text("❌ Спортсмен не найден")
                return
        
        # Проверяем права доступа
        is_athlete_viewing_own = (isinstance(user, Athlete) and athlete.telegram_id == user.telegram_id)
        is_coach_viewing_athlete = ((isinstance(user, Coach) or isinstance(user, Admin)) and 
                                   (isinstance(user, Admin) or athlete.created_by == user.id))
        
        if not (is_athlete_viewing_own or is_coach_viewing_athlete):
            await query.edit_message_text("❌ У вас нет доступа")
            return
        
        # Все абонементы спортсмена (включая неактивные)
        all_subscriptions = session.query(Subscription).filter_by(athlete_id=athlete_id).all()
        subscriptions = sorted(all_subscriptions, key=lambda s: s.created_at or datetime.min, reverse=True)
        
        if not subscriptions:
            keyboard = [
                [InlineKeyboardButton("🔙 Назад", callback_data=f"subscription_athlete_{athlete_id}" if is_coach_viewing_athlete else "athlete_back_to_menu")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                f"👤 <b>{html.escape(athlete.full_name)}</b>\n\n"
                f"📜 <b>ИСТОРИЯ АБОНЕМЕНТА</b>\n\n"
                f"❌ История абонемента пуста.",
                reply_markup=reply_markup,
                parse_mode='HTML'
            )
            return
        
        # Формируем сообщение с историей
        message = f"👤 <b>{html.escape(athlete.full_name)}</b>\n\n"
        message += f"📜 <b>ИСТОРИЯ АБОНЕМЕНТА</b>\n\n"
        message += f"Всего записей в истории: {len(subscriptions)}\n\n"
        
        # Создаем клавиатуру с кнопками для каждого абонемента
        keyboard = []
        
        for idx, sub in enumerate(subscriptions[:10], 1):  # Показываем первые 10
            status_text = _format_subscription_status_ui(sub)
            status_icon = _status_icon_from_status_text(status_text)
            
            # Форматируем даты
            start_date_str = sub.start_date.strftime('%d.%m.%Y') if sub.start_date else "—"
            end_date_str = sub.end_date.strftime('%d.%m.%Y') if sub.end_date else "—"
            
            sub_type = _format_subscription_type_ru(sub.subscription_type)
            
            # Формируем текст кнопки
            button_text = f"{status_icon} #{sub.id} | {sub_type} | {start_date_str}"
            if len(button_text) > 64:  # Ограничение Telegram на длину текста кнопки
                button_text = f"{status_icon} #{sub.id} | {sub_type}"
            
            keyboard.append([
                InlineKeyboardButton(
                    button_text,
                    callback_data=f"view_sub_{sub.id}"
                )
            ])
            
            # Добавляем информацию в сообщение
            message += f"<b>{idx}. Абонемент #{sub.id}</b> {status_icon}\n"
            message += f"   Тип абонемента: {sub_type}\n"
            message += f"   Период: {start_date_str} — {end_date_str}\n"
            
            message += f"   Статус: {status_text}\n"
            
            trainings_remaining = sub.trainings_remaining or 0
            trainings_total = sub.trainings_total or 0
            if trainings_total is not None:
                message += f"   Тренировки: {trainings_remaining}/{trainings_total}\n"
            else:
                message += f"   Тренировки: —/—\n"
            
            if sub.created_at:
                created_str = sub.created_at.strftime('%d.%m.%Y')
                message += f"   Создан: {created_str}\n"
            
            message += "\n"
        
        if len(subscriptions) > 10:
            message += f"\n... и еще {len(subscriptions) - 10} абонементов\n"
        
        # Кнопка назад
        if is_coach_viewing_athlete:
            keyboard.append([
                InlineKeyboardButton("🔙 Назад к абонементу", callback_data=f"subscription_athlete_{athlete_id}")
            ])
        else:
            # Для спортсмена - возврат к своему абонементу
            keyboard.append([
                InlineKeyboardButton("🔙 Назад к абонементу", callback_data="athlete_subscription_refresh")
            ])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            message,
            reply_markup=reply_markup,
            parse_mode='HTML'
        )
    
    except Exception as e:
        logger.error(f"❌ ОШИБКА ПРИ ПОКАЗЕ ИСТОРИИ АБОНЕМЕНТОВ: {e}", exc_info=True)
        await query.edit_message_text("❌ Ошибка при загрузке истории абонементов")
    finally:
        session.close()


async def view_subscription_from_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать детальную информацию об абонементе из истории"""
    query = update.callback_query
    await query.answer()
    
    # Получаем subscription_id из callback_data: view_sub_123
    subscription_id = int(query.data.replace("view_sub_", ""))
    
    session = Session()
    try:
        user = get_user_by_telegram_id(session, query.from_user.id)
        
        if not user:
            await query.edit_message_text("❌ Пользователь не найден")
            return
        
        # Получаем абонемент
        from services.subscription_service import SubscriptionService
        subscription = SubscriptionService.get_subscription_or_raise(session, subscription_id)
        athlete = subscription.athlete
        
        # Проверяем права доступа
        is_athlete_viewing_own = (isinstance(user, Athlete) and athlete.telegram_id == user.telegram_id)
        is_coach_viewing_athlete = ((isinstance(user, Coach) or isinstance(user, Admin)) and 
                                   (isinstance(user, Admin) or athlete.created_by == user.id))
        
        if not (is_athlete_viewing_own or is_coach_viewing_athlete):
            await query.edit_message_text("❌ У вас нет доступа")
            return
        
        # Получаем статистику использованных/неиспользованных тренировок
        from database.models import Attendance
        used_trainings = session.query(Attendance).filter_by(
            subscription_id=subscription.id,
            attended=True
        ).count()
        
        unused_trainings = session.query(Attendance).filter_by(
            subscription_id=subscription.id,
            attended=False
        ).count()
        
        # Расчет прогресса использования
        total_deducted = used_trainings + unused_trainings
        trainings_total = subscription.trainings_total or 0
        usage_percent = round((total_deducted / trainings_total) * 100, 1) if trainings_total > 0 else 0
        
        # Создаем визуальный прогресс-бар
        progress_length = 15
        filled = int(usage_percent * progress_length / 100)
        progress_bar = "█" * filled + "░" * (progress_length - filled)
        
        # Формируем сообщение
        message = f"🎫 <b>АБОНЕМЕНТ #{subscription.id}</b>\n\n"
        message += f"👤 <b>{html.escape(athlete.full_name)}</b>\n\n"
        
        age_group_display = format_age_group_label(athlete.age_group)
        dk = getattr(subscription, "discipline_key", None)

        message += f"<b>📋 ОСНОВНАЯ ИНФОРМАЦИЯ</b>\n"
        message += f"• Вид спорта: {subscription.sport_type or '—'}\n"
        message += f"• Возрастная группа: {age_group_display}\n"
        message += f"• Формат занятий: {format_training_format_ru(dk)}\n"
        sub_type_display = _format_subscription_type_ru(subscription.subscription_type)
        message += f"• Тип абонемента: {sub_type_display}\n"
        
        # Единый статус
        status_display = _format_subscription_status_ui(subscription)
        message += f"• Статус: {status_display}\n"
        
        # Улучшенное отображение дат действия
        start_date_str = _format_dt(subscription.start_date)
        message += f"• Дата начала: {start_date_str}\n"
        
        if subscription.end_date:
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
        
        # Дата создания абонемента
        if subscription.created_at:
            created_str = subscription.created_at.strftime('%d.%m.%Y %H:%M')
            message += f"• Создан: {created_str}\n"
        
        message += f"\n<b>🏋️ ТРЕНИРОВКИ</b>\n"
        trainings_total = subscription.trainings_total or 0
        trainings_remaining = subscription.trainings_remaining or 0
        if trainings_total is not None:
            message += f"• Всего: {trainings_total}\n"
            message += f"• Использовано: {used_trainings}\n"
            message += f"• Неиспользовано: {unused_trainings}\n"
            message += f"• Осталось: {trainings_remaining}\n"
        else:
            message += f"• Всего: —\n"
            message += f"• Использовано: {used_trainings}\n"
            message += f"• Неиспользовано: {unused_trainings}\n"
            message += f"• Осталось: —\n"
        
        if subscription.total_restored > 0:
            message += f"• Восстановлено: {subscription.total_restored}\n"
            if subscription.restored_this_month > 0:
                message += f"• Восстановлено в этом месяце: {subscription.restored_this_month}\n"
        
        if (subscription.frozen_training_days_total or 0) > 0:
            message += f"• Заморожено тренировочных дней: {subscription.frozen_training_days_total}\n"
        
        # Прогресс-бар использования
        message += f"\n<b>📊 ИСПОЛЬЗОВАНИЕ</b>\n"
        message += f"{progress_bar} {usage_percent}%\n"
        
        # Создаем инлайн клавиатуру
        keyboard = []
        
        # Кнопка истории
        keyboard.append([
            InlineKeyboardButton("📜 История абонемента", callback_data=f"subscription_history_{athlete.id}")
        ])
        
        # Кнопка назад
        if is_coach_viewing_athlete:
            keyboard.append([
                InlineKeyboardButton("🔙 Назад к истории", callback_data=f"subscription_history_{athlete.id}")
            ])
        else:
            # Для спортсмена - возврат к истории или к абонементу
            keyboard.append([
                InlineKeyboardButton("🔙 Назад к истории", callback_data=f"subscription_history_athlete_{athlete.id}")
            ])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            message,
            reply_markup=reply_markup,
            parse_mode='HTML'
        )
    
    except Exception as e:
        logger.error(f"❌ ОШИБКА ПРИ ПОКАЗЕ АБОНЕМЕНТА ИЗ ИСТОРИИ: {e}", exc_info=True)
        await query.edit_message_text("❌ Ошибка при загрузке абонемента")
    finally:
        session.close()


async def handle_activate_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Активировать абонемент - показать выбор типа или активировать существующий"""
    query = update.callback_query
    await query.answer()
    
    logger.info(f"[activate_sub] raw_query_data={getattr(query, 'data', None)} user_id={getattr(query.from_user, 'id', None)}")
    
    callback_data = query.data.replace("activate_sub_", "")
    logger.info(f"[activate_sub] parsed_callback_data={callback_data}")
    
    session = Session()
    try:
        user = get_user_by_telegram_id(session, query.from_user.id)
        if not user or get_user_role(user) not in ['coach', 'admin']:
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
            if isinstance(user, Coach):
                sport_type_for_sub = get_coach_sport_type(user)
                if not sport_type_for_sub:
                    await query.edit_message_text("❌ У вас не указан вид спорта. Обратитесь к администратору.")
                    return
            elif isinstance(user, Admin):
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
                        sport_type_for_sub = get_coach_sport_type(user)
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
    finally:
        session.close()


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

    athlete_id = int(query.data.replace("visits_", ""))

    session = Session()
    try:
        user = get_user_by_telegram_id(session, query.from_user.id)

        if not user or get_user_role(user) not in ['coach', 'admin']:
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

        message = f"📅 <b>ИСТОРИЯ ПОСЕЩЕНИЙ</b>\n\n"
        message += f"👤 <b>{html.escape(athlete.full_name)}</b>\n\n"
        message += "\n"

        now = now_moscow()
        max_lines = 28
        rows: List[tuple] = []
        visible_slots: List[Training] = []

        if athlete.sport_type and athlete.age_group:
            lookback = now - timedelta(days=120)
            _fit = TRAINING_FORMAT_INDIVIDUAL
            has_group_sub = exists().where(
                and_(
                    Subscription.athlete_id == athlete_id,
                    Subscription.sport_type == Training.sport_type,
                    func.date(Subscription.start_date) <= func.date(Training.training_date),
                    func.date(Subscription.end_date) >= func.date(Training.training_date),
                    or_(
                        Subscription.subscription_type.is_(None),
                        Subscription.subscription_type != "individual",
                    ),
                )
            )
            # Групповые слоты: совпадение вида спорта и возрастной группы, без individual.
            trainings_group = (
                session.query(Training)
                .filter(
                    Training.sport_type == athlete.sport_type,
                    Training.age_group == athlete.age_group,
                    Training.is_cancelled.is_(False),
                    Training.training_date >= lookback,
                    Training.training_date <= now,
                    has_group_sub,
                    or_(
                        Training.training_format.is_(None),
                        func.trim(Training.training_format) == "",
                        func.lower(func.trim(Training.training_format)) != _fit,
                    ),
                )
                .order_by(Training.training_date.desc())
                .limit(80)
                .all()
            )
            if getattr(athlete, "created_by", None):
                trainings_group = [t for t in trainings_group if t.coach_id == athlete.created_by]
            # Индивидуальные слоты: в trainings.age_group часто «adults» для всех;
            # отбор по факту абонемента individual с тем же start_date, что у слота.
            has_individual_sub = exists().where(
                and_(
                    Subscription.athlete_id == athlete_id,
                    Subscription.sport_type == Training.sport_type,
                    Subscription.subscription_type == "individual",
                    func.strftime("%Y-%m-%d %H:%M", Subscription.start_date)
                    == func.strftime("%Y-%m-%d %H:%M", Training.training_date),
                )
            )
            trainings_indiv = (
                session.query(Training)
                .filter(
                    Training.sport_type == athlete.sport_type,
                    Training.is_cancelled.is_(False),
                    Training.training_date >= lookback,
                    Training.training_date <= now,
                    has_individual_sub,
                    func.lower(func.coalesce(func.trim(Training.training_format), "")) == _fit,
                )
                .order_by(Training.training_date.desc())
                .limit(80)
                .all()
            )
            if getattr(athlete, "created_by", None):
                trainings_indiv = [t for t in trainings_indiv if t.coach_id == athlete.created_by]
            all_slots = dedupe_individual_trainings_by_slot(trainings_group + trainings_indiv)
            # Показываем не только завершённые, но и текущие слоты: отмеченные статусы видны сразу.
            visible_slots = sorted(all_slots, key=lambda tr: tr.training_date, reverse=True)[:45]
            if visible_slots:
                tids_flat = set()
                for tr in visible_slots:
                    tids_flat.update(individual_slot_training_ids(session, tr))
                atts = (
                    session.query(Attendance)
                    .options(joinedload(Attendance.training))
                    .filter(
                        Attendance.athlete_id == athlete_id,
                        Attendance.training_id.in_(list(tids_flat)),
                    )
                    .all()
                )
                att_by_tid = {a.training_id: a for a in atts}

                def _attendance_for_slot(tr: Training):
                    for sid in individual_slot_training_ids(session, tr):
                        if sid in att_by_tid:
                            return att_by_tid[sid]
                    return None

                history_changed = False
                ordered_slots = sorted(visible_slots, key=lambda tr: tr.training_date)
                if len(ordered_slots) > max_lines:
                    ordered_slots = ordered_slots[-max_lines:]

                for t in ordered_slots:
                    att = _attendance_for_slot(t)
                    training_date = t.training_date.strftime("%d.%m.%Y %H:%M")
                    icon = attendance_icon_for_training(att, t, now=now)
                    label = attendance_label_ru_for_training(
                        att, t, now=now, with_note=(att is None)
                    )
                    if att is not None and att.attended:
                        status_code = "present"
                    else:
                        # В истории не используем «не отмечено»: отсутствие отметки считаем «не был».
                        status_code = "absent"

                    vh = (
                        session.query(VisitHistory)
                        .filter(
                            VisitHistory.athlete_id == athlete_id,
                            VisitHistory.training_id == t.id,
                        )
                        .first()
                    )
                    if vh is None:
                        vh = VisitHistory(
                            athlete_id=athlete_id,
                            training_id=t.id,
                            subscription_id=att.subscription_id if att is not None else None,
                            attendance_id=att.id if att is not None else None,
                            status_code=status_code,
                            status_label=label,
                            source="derived",
                            recorded_at=now,
                        )
                        session.add(vh)
                        history_changed = True
                    else:
                        if (
                            vh.subscription_id != (att.subscription_id if att is not None else None)
                            or vh.attendance_id != (att.id if att is not None else None)
                            or vh.status_code != status_code
                            or vh.status_label != label
                        ):
                            vh.subscription_id = att.subscription_id if att is not None else None
                            vh.attendance_id = att.id if att is not None else None
                            vh.status_code = status_code
                            vh.status_label = label
                            vh.updated_at = now
                            history_changed = True

                    is_individual_slot = (
                        (getattr(t, "training_format", None) or "").strip().lower()
                        == TRAINING_FORMAT_INDIVIDUAL
                    )
                    if is_individual_slot:
                        slot_desc = (
                            f"{training_date} — {html.escape(t.sport_type)} — Индивидуальная"
                        )
                    else:
                        age_group_ru = format_age_group_label(t.age_group, short=True)
                        slot_desc = (
                            f"{training_date} — {html.escape(t.sport_type)} ({age_group_ru}) — Групповая"
                        )

                    if icon == "✅":
                        status_text = "✅ Был"
                    else:
                        status_text = "❌ Не был"

                    rows.append((t.training_date, f"{slot_desc} — {status_text}\n"))
                if history_changed:
                    session.commit()

        covered_tids = set()
        for tr in visible_slots:
            covered_tids.update(individual_slot_training_ids(session, tr))

        # Добавляем сохранённые отметки вне видимых слотов (другая дисциплина/группа/старше окна),
        # чтобы не терялась история прежних записей.
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
                training_date = tr.training_date.strftime("%d.%m.%Y %H:%M")
                is_individual_slot = (
                    (getattr(tr, "training_format", None) or "").strip().lower()
                    == TRAINING_FORMAT_INDIVIDUAL
                )
                if is_individual_slot:
                    slot_desc = (
                        f"{training_date} — {html.escape(tr.sport_type)} — Индивидуальная"
                    )
                else:
                    age_group_ru = format_age_group_label(tr.age_group, short=True)
                    slot_desc = (
                        f"{training_date} — {html.escape(tr.sport_type)} ({age_group_ru}) — Групповая"
                    )
            else:
                dt = now
                slot_desc = "—"
            status_text = "✅ Был" if att.attended else "❌ Не был"
            rows.append((dt, f"{slot_desc} — {status_text}\n"))

        if not rows:
            message += "📭 Нет записей посещений.\n"
        else:
            rows_sorted = sorted(rows, key=lambda x: x[0])
            if len(rows_sorted) > max_lines:
                rows_sorted = rows_sorted[-max_lines:]
            for _dt, line in rows_sorted:
                message += line

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
        logger.error(f"❌ ОШИБКА ПРИ ПОКАЗЕ ПОСЕЩЕНИЙ: {e}", exc_info=True)
        await query.edit_message_text("❌ Ошибка при загрузке посещений")
    finally:
        session.close()


async def show_athlete_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать детальную статистику спортсмена"""
    query = update.callback_query
    await query.answer()
    
    athlete_id = int(query.data.replace("stats_", ""))
    
    session = Session()
    try:
        user = get_user_by_telegram_id(session, query.from_user.id)
        
        if not user or get_user_role(user) not in ['coach', 'admin']:
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
    finally:
        session.close()


async def show_restore_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать меню восстановления тренировок"""
    query = update.callback_query
    await query.answer()
    
    athlete_id = int(query.data.replace("restore_", ""))
    
    session = Session()
    try:
        user = get_user_by_telegram_id(session, query.from_user.id)
        
        if not user or get_user_role(user) not in ['coach', 'admin']:
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
        
        coach_sport = get_coach_sport_type(user) if isinstance(user, Coach) else None
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
    finally:
        session.close()


async def execute_restore_training(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Восстановить конкретную тренировку"""
    query = update.callback_query
    await query.answer()
    
    attendance_id = int(query.data.replace("restore_att_", ""))
    
    session = Session()
    try:
        user = get_user_by_telegram_id(session, query.from_user.id)
        
        if not user or get_user_role(user) not in ['coach', 'admin']:
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
    finally:
        session.close()


async def select_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать список активных абонементов для выбора"""
    query = update.callback_query
    await query.answer()
    
    athlete_id = int(query.data.replace("select_sub_", ""))
    
    session = Session()
    try:
        user = get_user_by_telegram_id(session, query.from_user.id)
        
        if not user or get_user_role(user) not in ['coach', 'admin']:
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
    finally:
        session.close()


async def view_subscription_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать карточку спортсмена с выбранным абонементом"""
    query = update.callback_query
    await query.answer()
    
    subscription_id = int(query.data.replace("view_sub_card_", ""))
    
    session = Session()
    try:
        user = get_user_by_telegram_id(session, query.from_user.id)
        
        if not user or get_user_role(user) not in ['coach', 'admin']:
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
    finally:
        session.close()


async def show_edit_athlete_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать меню редактирования данных спортсмена"""
    query = update.callback_query
    await query.answer()
    
    athlete_id = int(query.data.replace("edit_", ""))
    
    session = Session()
    try:
        user = get_user_by_telegram_id(session, query.from_user.id)
        
        if not user or get_user_role(user) not in ['coach', 'admin']:
            await query.edit_message_text("❌ У вас нет доступа")
            return
        
        athlete = session.query(Athlete).filter_by(id=athlete_id).first()
        if not athlete:
            await query.edit_message_text("❌ Спортсмен не найден")
            return
        
        # Проверяем права
        if isinstance(user, Coach) and athlete.created_by != user.id:
            await query.edit_message_text("❌ Вы не можете редактировать этого спортсмена")
            return
        
        message = f"✏️ <b>РЕДАКТИРОВАНИЕ ДАННЫХ</b>\n\n"
        message += f"👤 <b>{html.escape(athlete.full_name)}</b>\n\n"
        message += "Выберите, что хотите изменить:"
        
        keyboard = [
            [InlineKeyboardButton("📝 ФИО", callback_data=f"edit_name_{athlete_id}")],
            [InlineKeyboardButton("📞 Телефон", callback_data=f"edit_phone_{athlete_id}")],
            [InlineKeyboardButton("🏥 Медицинская информация", callback_data=f"edit_medical_{athlete_id}")],
            [InlineKeyboardButton("🔙 Назад к карточке", callback_data=f"athlete_{athlete_id}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            message,
            reply_markup=reply_markup,
            parse_mode='HTML'
        )
    
    except Exception as e:
        logger.error(f"❌ ОШИБКА ПРИ ПОКАЗЕ МЕНЮ РЕДАКТИРОВАНИЯ: {e}", exc_info=True)
        await query.edit_message_text("❌ Ошибка при загрузке меню редактирования")
    finally:
        session.close()


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

    session = Session()
    try:
        user = get_user_by_telegram_id(session, query.from_user.id)
        if not user or get_user_role(user) not in ['coach', 'admin']:
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

        if any(s.is_frozen for s in active_subs):
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
    finally:
        session.close()


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

    session = Session()
    try:
        user = get_user_by_telegram_id(session, query.from_user.id)
        if not user or get_user_role(user) not in ['coach', 'admin']:
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
    finally:
        session.close()


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

    session = Session()
    try:
        user = get_user_by_telegram_id(session, query.from_user.id)
        if not user or get_user_role(user) not in ['coach', 'admin']:
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
    finally:
        session.close()


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

    session = Session()
    try:
        user = get_user_by_telegram_id(session, query.from_user.id)
        if not user or get_user_role(user) not in ['coach', 'admin']:
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
    finally:
        session.close()