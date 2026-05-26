import logging
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.error import BadRequest
from telegram.ext import ContextTypes
from database.models import Athlete, Training, Attendance
from core.database import get_db_session
from database.db_utils import (
    get_user_by_telegram_id,
    get_user_role,
    is_training_in_global_freeze,
)
from database.db_utils.training_slots import individual_slot_training_ids
from database.db_utils.visit_history import upsert_visit_history_for_training
from utils.subscription_resolve import (
    active_subscriptions_all,
    active_subscription_for_training,
    subscription_for_coach_sport,
)
from utils.coach_sport import coach_sport_type_name
from utils.time_utils import now_moscow, training_slot_end_time
from utils.attendance_display import attendance_icon_for_training
from services.attendance_training_flow import (
    build_step2_message_and_keyboard_rows,
    coach_training_access_error,
    fetch_athletes_for_training_slot,
    is_training_in_live_attendance_window,
    parse_attendance_direct_mark_callback,
    parse_attendance_page_callback,
    resolve_training_from_attendance_callback,
)
import html

logger = logging.getLogger(__name__)


def _keyboard_from_rows(rows: List[List[Tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(text, callback_data=cd) for text, cd in row] for row in rows]
    )


async def _render_attendance_step2(
    query,
    context: ContextTypes.DEFAULT_TYPE,
    session,
    user,
    training: Training,
    page: int,
    flash_html: str = None,
) -> None:
    err = coach_training_access_error(user, training)
    if err:
        await query.edit_message_text(err)
        return
    athletes, attendance_map = fetch_athletes_for_training_slot(session, training)
    message, rows = build_step2_message_and_keyboard_rows(
        training, athletes, attendance_map, page, flash_html=flash_html
    )
    context.user_data["attendance_slot_page"] = page
    context.user_data["attendance_selected_training_id"] = training.id
    try:
        await query.edit_message_text(
            message, reply_markup=_keyboard_from_rows(rows), parse_mode="HTML"
        )
    except BadRequest as e:
        if "message is not modified" in str(e).lower():
            logger.debug("Шаг 2 посещения: сообщение не изменилось, пропускаем edit")
            return
        raise


async def select_training_for_attendance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Шаг 2: показать релевантных спортсменов для выбранной тренировки."""
    query = update.callback_query
    await query.answer()

    try:
        with get_db_session() as session:
            user = get_user_by_telegram_id(session, query.from_user.id)
            if not user or get_user_role(user) != "coach":
                await query.edit_message_text("❌ У вас нет доступа к этому меню")
                return

            data = query.data or ""
            virtual_slots = context.user_data.get("attendance_virtual_slots", {})
            training, created_new, err = resolve_training_from_attendance_callback(
                session, data, virtual_slots
            )
            if err:
                await query.edit_message_text(err)
                return

            err_coach = coach_training_access_error(user, training)
            if err_coach:
                await query.edit_message_text(err_coach)
                return

            if created_new:
                session.commit()

            if not is_training_in_live_attendance_window(training):
                await query.edit_message_text(
                    "🔒 Сейчас отметить можно только <b>текущую пару</b> "
                    "(пока идёт занятие по расписанию).\n\n"
                    "Вернитесь в список и выберите слот <b>без замка 🔒</b> или обновите позже.",
                    parse_mode="HTML",
                )
                return

            context.user_data["attendance_slot_page"] = 0
            await _render_attendance_step2(query, context, session, user, training, 0)
    except Exception as e:
        logger.error("❌ ОШИБКА ВЫБОРА ТРЕНИРОВКИ ДЛЯ ОТМЕТКИ: %s", e, exc_info=True)
        await query.edit_message_text("❌ Ошибка при загрузке спортсменов")


async def handle_attendance_athletes_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Пагинация списка спортсменов на шаге 2 (attpg_{training_id}_{page})."""
    query = update.callback_query
    await query.answer()
    parsed = parse_attendance_page_callback(query.data or "")
    if not parsed:
        return
    training_id, page = parsed
    try:
        with get_db_session() as session:
            user = get_user_by_telegram_id(session, query.from_user.id)
            if not user or get_user_role(user) != "coach":
                await query.edit_message_text("❌ У вас нет доступа к этому меню")
                return
            training = session.query(Training).filter_by(id=training_id, is_cancelled=False).first()
            if not training:
                await query.edit_message_text("❌ Тренировка не найдена")
                return
            err_coach = coach_training_access_error(user, training)
            if err_coach:
                await query.edit_message_text(err_coach)
                return
            await _render_attendance_step2(query, context, session, user, training, page)
    except Exception as e:
        logger.error("❌ ОШИБКА ПАГИНАЦИИ ОТМЕТКИ: %s", e, exc_info=True)
        await query.edit_message_text("❌ Ошибка при смене страницы")


async def handle_attendance_page_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Подсказка по кнопке номера страницы."""
    query = update.callback_query
    await query.answer(
        "Это номер страницы. Листайте список кнопками «Пред.» и «След.».",
        show_alert=False,
    )


async def handle_attendance_name_column(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    """Колонка «ФИО» в отметке посещения — не действие, только подсказка."""
    query = update.callback_query
    await query.answer("Нажмите «✅ Был» или «❌ Не был». До конца пары статус можно менять.", show_alert=False)


async def handle_attendance_slot_locked(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    """Слот не в окне текущей пары — выбор заблокирован."""
    query = update.callback_query
    await query.answer(
        "Сейчас доступна только тренировка, которая идёт по времени. Остальные слоты с 🔒 откроются в своё время.",
        show_alert=True,
    )


async def _run_attendance_mark_query(
    query,
    context: ContextTypes.DEFAULT_TYPE,
    session,
    athlete_id: int,
    training_id: int,
    attended: bool,
    *,
    clear_legacy_mark_flow_keys: bool,
) -> Optional[str]:
    """
    Запись отметки посещения.
    Возвращает: "__noop__" (повтор того же «был/не был»), "__handled__" (успех или уже отвечено через edit),
    иначе строку ошибки для edit_message_text.
    """
    athlete = session.query(Athlete).filter_by(id=athlete_id).first()
    training = session.query(Training).filter_by(id=training_id).first()

    if not athlete or not training:
        return "❌ Спортсмен или тренировка не найдены"

    if is_training_in_global_freeze(session, training.training_date):
        await query.edit_message_text(
            "⛔️ В период массовой заморозки отметка посещений недоступна."
            "\n\nСписание тренировок в этот период не производится."
        )
        return "__handled__"

    user = get_user_by_telegram_id(session, query.from_user.id)
    if user and get_user_role(user) == "coach":
        err_coach = coach_training_access_error(user, training)
        if err_coach:
            return err_coach

    subscription = active_subscription_for_training(athlete, training)
    if not subscription:
        return "❌ Нет активного абонемента для этой тренировки"

    if not subscription.is_active:
        return "❌ Абонемент не активен"

    training_start = training.training_date
    training_end_datetime = training_slot_end_time(
        training_start, getattr(training, "training_format", None)
    )
    current_time = now_moscow()
    if current_time < training_start:
        await query.edit_message_text(
            "⏳ Пара ещё не началась.\n\n"
            f"Начало: {training_start.strftime('%d.%m.%Y %H:%M')}\n"
            "Отметить можно <b>с начала</b> занятия до его <b>окончания</b>.",
            parse_mode="HTML",
        )
        return "__handled__"
    if current_time > training_end_datetime:
        await query.edit_message_text(
            "⏱ Время для ручной отметки истекло.\n\n"
            f"Окончание пары: {training_end_datetime.strftime('%d.%m.%Y %H:%M')}\n"
            "После окончания занятия отметки и списания фиксируются автоматически "
            "(в т.ч. «не был», если статус не поставлен).\n\n"
            "<i>При ошибке обратитесь к администратору.</i>",
            parse_mode="HTML",
        )
        return "__handled__"

    slot_training_ids = individual_slot_training_ids(session, training)
    existing_attendance = (
        session.query(Attendance)
        .filter(
            Attendance.athlete_id == athlete_id,
            Attendance.training_id.in_(slot_training_ids),
        )
        .first()
    )

    if existing_attendance:
        old_status = existing_attendance.attended
        if old_status == attended:
            logger.info(
                "attendance_noop trainer_tg=%s athlete_id=%s training_id=%s status=%s",
                query.from_user.id,
                athlete_id,
                training_id,
                "present" if attended else "absent",
            )
            if clear_legacy_mark_flow_keys:
                context.user_data.pop("mark_attendance_athlete_id", None)
                context.user_data.pop("selected_training_id", None)
            # Повторное нажатие того же статуса: не перерисовываем (Telegram: message is not modified)
            return "__noop__"

    if existing_attendance:
        old_status = existing_attendance.attended
        if getattr(existing_attendance, "locked_at", None) is not None:
            return (
                "❌ Пара уже завершена, статус зафиксирован. "
                "Для исправления обратитесь к администратору."
            )
        # Остаток абонемента не меняем до окончания пары; списание — при выставлении locked_at (фоновая задача).
        existing_attendance.attended = attended
        existing_attendance.marked_by = query.from_user.id
        attendance_row = existing_attendance
        logger.info(
            "attendance_updated trainer_tg=%s athlete_id=%s training_id=%s old=%s new=%s",
            query.from_user.id,
            athlete_id,
            training_id,
            "present" if old_status else "absent",
            "present" if attended else "absent",
        )
    else:
        canonical_training_id = min(slot_training_ids)
        attendance = Attendance(
            athlete_id=athlete_id,
            training_id=canonical_training_id,
            subscription_id=subscription.id,
            attended=attended,
            marked_by=query.from_user.id,
            created_at=now_moscow(),
        )

        session.add(attendance)
        session.flush()
        attendance_row = attendance
        logger.info(
            "attendance_created trainer_tg=%s athlete_id=%s training_id=%s status=%s",
            query.from_user.id,
            athlete_id,
            training_id,
            "present" if attended else "absent",
        )

    upsert_visit_history_for_training(
        session,
        athlete_id=athlete_id,
        training_id=training.id,
        attendance=attendance_row,
        status_code="present" if attended else "absent",
        status_label="✅ Был" if attended else "❌ Не был",
        source="attendance_mark",
    )

    session.commit()

    page = context.user_data.get("attendance_slot_page", 0)

    if clear_legacy_mark_flow_keys:
        context.user_data.pop("mark_attendance_athlete_id", None)
        context.user_data.pop("selected_training_id", None)

    await _render_attendance_step2(query, context, session, user, training, page)
    return "__handled__"


async def execute_mark_attendance_slot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отметка «Был»/«Не был» с шага 2 (callback atmark_…)."""
    query = update.callback_query

    parsed = parse_attendance_direct_mark_callback(query.data or "")
    if not parsed:
        await query.answer()
        return

    training_id, athlete_id, attended = parsed
    action_signature = query.data or ""
    action_guard = context.user_data.get("attendance_click_guard")
    now_ts = now_moscow().timestamp()
    if (
        isinstance(action_guard, dict)
        and action_guard.get("signature") == action_signature
        and isinstance(action_guard.get("ts"), (int, float))
        and (now_ts - action_guard.get("ts")) < 3
    ):
        await query.answer()
        return
    context.user_data["attendance_click_guard"] = {"signature": action_signature, "ts": now_ts}

    try:
        with get_db_session() as session:
            result = await _run_attendance_mark_query(
                query,
                context,
                session,
                athlete_id,
                training_id,
                attended,
                clear_legacy_mark_flow_keys=False,
            )
            if result == "__noop__":
                await query.answer("Уже так отмечено.", show_alert=False)
            elif result == "__handled__":
                await query.answer()
            elif result:
                await query.answer()
                await query.edit_message_text(result)
    except Exception as e:
        logger.error("❌ ОШИБКА ОТМЕТКИ (atmark): %s", e, exc_info=True)
        try:
            await query.answer("Ошибка отметки.", show_alert=False)
        except Exception:
            pass
        try:
            await query.edit_message_text(f"❌ Ошибка при отметке посещения: {str(e)}")
        except BadRequest:
            pass
        context.user_data.pop("attendance_click_guard", None)


async def mark_attendance_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало процесса отметки посещения"""
    query = update.callback_query
    await query.answer()

    payload = query.data.replace("mark_attendance_", "")
    parts = payload.split("_")
    if not parts or not parts[0].isdigit():
        await query.edit_message_text("❌ Некорректный формат запроса")
        return

    athlete_id = int(parts[0])
    selected_training_id = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
    context.user_data['mark_attendance_athlete_id'] = athlete_id
    if selected_training_id:
        context.user_data['selected_training_id'] = selected_training_id

    try:
        with get_db_session() as session:
            athlete = session.query(Athlete).filter_by(id=athlete_id).first()

            if not athlete:
                await query.edit_message_text("❌ Спортсмен не найден")
                return

            if not active_subscriptions_all(athlete):
                await query.edit_message_text("❌ У спортсмена нет активного абонемента")
                return

            user = get_user_by_telegram_id(session, query.from_user.id)
            coach_sport = coach_sport_type_name(user)
            if coach_sport and subscription_for_coach_sport(athlete, coach_sport) is None:
                await query.edit_message_text("❌ Нет активного абонемента по вашему виду спорта")
                return

            if selected_training_id:
                training = session.query(Training).filter_by(id=selected_training_id, is_cancelled=False).first()
                if not training:
                    await query.edit_message_text("❌ Выбранная тренировка не найдена")
                    return
                err_coach = coach_training_access_error(user, training)
                if err_coach:
                    await query.edit_message_text(err_coach)
                    return

                if not active_subscription_for_training(athlete, training):
                    await query.edit_message_text("❌ Нет активного абонемента для этой тренировки")
                    return

                context.user_data['selected_training_id'] = training.id
                keyboard = [
                    [
                        InlineKeyboardButton("✅ Был на тренировке", callback_data="mark_present"),
                        InlineKeyboardButton("❌ Не был на тренировке", callback_data="mark_absent")
                    ],
                    [InlineKeyboardButton("🔙 Назад", callback_data=f"select_mark_training_{training.id}")]
                ]
                await query.edit_message_text(
                    "📝 <b>ОТМЕТКА ПОСЕЩЕНИЯ</b>\n\n"
                    f"👤 <b>{html.escape(athlete.full_name)}</b>\n"
                    f"📅 Тренировка: {training.training_date.strftime('%d.%m.%Y %H:%M')}\n\n"
                    "Был на этом занятии или нет?",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode='HTML'
                )
                return
        
            # Рассчитываем дату неделю назад
            week_ago = now_moscow() - timedelta(days=7)

            sport_for_slots = coach_sport or athlete.sport_type
            if not sport_for_slots:
                await query.edit_message_text("❌ Не задан вид спорта для отметки посещения")
                return

            query_filter = session.query(Training).filter(
                Training.sport_type == sport_for_slots,
                Training.age_group == athlete.age_group,
                Training.training_date >= week_ago,
                Training.is_cancelled == False
            )
        
            # Если это тренер, показываем только его тренировки
            if user and get_user_role(user) == 'coach' and getattr(user, "id", None):
                query_filter = query_filter.filter(Training.coach_id == user.id)
        
            trainings = query_filter.order_by(Training.training_date.desc()).limit(5).all()

            if not trainings:
                await query.edit_message_text("❌ Нет тренировок для отметки за последнюю неделю")
                return

            message = f"📅 <b>ОТМЕТКА ПОСЕЩЕНИЯ</b>\n\n"
            message += f"👤 <b>{html.escape(athlete.full_name)}</b>\n"
            sub_ui = subscription_for_coach_sport(athlete, coach_sport)
            from utils.age_groups import format_age_group_label

            message += f"🥊 {sport_for_slots} | {format_age_group_label(athlete.age_group)}\n"
            message += f"🎫 Абонемент #{sub_ui.id}\n"
            message += f"🏋️ Осталось тренировок: {sub_ui.trainings_remaining}\n\n"
            message += "<b>Выберите тренировку для отметки:</b>\n"

            keyboard = []

            for training in trainings:
                # Проверяем, не отмечена ли уже эта тренировка
                attendance = session.query(Attendance).filter(
                    Attendance.athlete_id == athlete_id,
                    Attendance.training_id == training.id
                ).first()

                if attendance:
                    status = "✅ Посещена" if attendance.attended else "❌ Пропущена"
                    btn_text = f"{status} - {training.training_date.strftime('%d.%m %H:%M')}"
                    callback_data = f"view_attendance_{attendance.id}"
                else:
                    slot_icon = attendance_icon_for_training(
                        None, training, now=now_moscow()
                    )
                    btn_text = f"{slot_icon} {training.training_date.strftime('%d.%m %H:%M')}"
                    callback_data = f"select_training_{training.id}"

                keyboard.append([InlineKeyboardButton(btn_text, callback_data=callback_data)])

            keyboard.append([
                InlineKeyboardButton("🔙 К тренировкам на сегодня", callback_data="attendance_training_list"),
                InlineKeyboardButton("🏠 В меню", callback_data="back_to_menu_main")
            ])

            reply_markup = InlineKeyboardMarkup(keyboard)

            await query.edit_message_text(
                message,
                reply_markup=reply_markup,
                parse_mode='HTML'
            )

    except Exception as e:
        logger.error(f"❌ ОШИБКА НАЧАЛА ОТМЕТКИ: {e}")
        await query.edit_message_text("❌ Ошибка при загрузке тренировок")


async def handle_training_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка выбора тренировки для отметки"""
    query = update.callback_query
    await query.answer()

    training_id = int(query.data.replace("select_training_", ""))
    athlete_id = context.user_data.get('mark_attendance_athlete_id')

    if not athlete_id:
        await query.edit_message_text("❌ Данные сессии утеряны")
        return

    context.user_data['selected_training_id'] = training_id

    keyboard = [
        [
            InlineKeyboardButton("✅ Был на тренировке", callback_data="mark_present"),
            InlineKeyboardButton("❌ Не был на тренировке", callback_data="mark_absent")
        ],
        [InlineKeyboardButton("🔙 Назад", callback_data=f"mark_attendance_{athlete_id}")]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        "📝 <b>ОТМЕТКА ПОСЕЩЕНИЯ</b>\n\n"
        "Был спортсмен на выбранной тренировке или нет?",
        reply_markup=reply_markup,
        parse_mode='HTML'
    )


async def execute_mark_attendance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выполнить отметку посещения (промежуточный экран «был/не был» по mark_present/mark_absent)."""
    query = update.callback_query
    await query.answer()

    action = query.data  # "mark_present" или "mark_absent"
    athlete_id = context.user_data.get("mark_attendance_athlete_id")
    training_id = context.user_data.get("selected_training_id")

    if not athlete_id or not training_id:
        await query.edit_message_text("❌ Данные сессии утеряны")
        return

    attended = action == "mark_present"
    action_signature = f"{athlete_id}:{training_id}:{action}"
    action_guard = context.user_data.get("attendance_click_guard")
    now_ts = now_moscow().timestamp()
    if (
        isinstance(action_guard, dict)
        and action_guard.get("signature") == action_signature
        and isinstance(action_guard.get("ts"), (int, float))
        and (now_ts - action_guard.get("ts")) < 3
    ):
        return
    context.user_data["attendance_click_guard"] = {"signature": action_signature, "ts": now_ts}

    try:
        with get_db_session() as session:
            result = await _run_attendance_mark_query(
                query,
                context,
                session,
                athlete_id,
                training_id,
                attended,
                clear_legacy_mark_flow_keys=True,
            )
            if result == "__noop__":
                pass  # callback уже подтверждён в начале обработчика
            elif result and result != "__handled__":
                await query.edit_message_text(result)
    except Exception as e:
        logger.error("❌ ОШИБКА ОТМЕТКИ ПОСЕЩЕНИЯ: %s", e, exc_info=True)
        await query.edit_message_text(f"❌ Ошибка при отметке посещения: {str(e)}")
        context.user_data.pop("attendance_click_guard", None)