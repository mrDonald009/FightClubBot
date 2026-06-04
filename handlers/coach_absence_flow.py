"""Диалог «Отмена тренировки»: одно занятие (день+время) или период (праздники)."""
import html
import logging
import re
from datetime import date, datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from core.database import get_db_session
from database.models import Coach
from handlers.coach_handlers import MENU_BUTTONS, cancel_global_freeze
from keyboards.coach_kb import (
    TRAINING_CANCELLATION_BUTTON,
    TRAINING_CANCELLATION_SCOPE_HTML,
    TRAINING_CANCELLATION_TITLE,
)
from services.coach_absence_service import (
    apply_coach_absence_service,
    ca_button_label,
    deactivate_coach_absence_service,
    list_active_coach_absences,
    list_active_coaches,
    overlapping_coach_absence_ids,
    parse_ui_date,
)
from services.group_training_cancellation_service import (
    apply_slot_cancellation_service,
    build_cancellation_day_calendar,
    coach_user_for_slots,
    format_unified_cancellation_history_html,
    format_unified_cancellation_menu_status_html,
    group_slots_for_cancellation_day,
    list_rollbackable_slots_service,
    parse_slot_callback,
    resolve_slot_from_callback,
    revert_slot_cancellation_service,
    slot_callback_data,
    slot_rollback_button_label,
)
from services.user_service import UserService
from services.permissions import (
    can_manage_coach_absence_flow,
    is_coach,
    COACH_ABSENCE_DENIED_MESSAGE,
)
from utils.time_utils import now_moscow
from utils.training_slot_display import format_training_slot_line

logger = logging.getLogger(__name__)

CA_MENU, CA_COACH, CA_CAL, CA_SLOT, CA_SLOT_CONFIRM = range(5)
CA_PERIOD_START, CA_PERIOD_END, CA_PERIOD_TITLE, CA_PERIOD_CONFIRM = range(5, 9)


async def _ca_safe_edit(update, context, text, reply_markup=None, parse_mode="HTML"):
    query = update.callback_query
    if query:
        try:
            await query.edit_message_text(
                text, reply_markup=reply_markup, parse_mode=parse_mode
            )
        except Exception:
            await query.message.reply_text(
                text, reply_markup=reply_markup, parse_mode=parse_mode
            )
    else:
        await update.message.reply_text(
            text, reply_markup=reply_markup, parse_mode=parse_mode
        )


def _resolve_coach_id(context) -> int:
    return context.user_data.get("ca_coach_id")


def _resolve_pick_day(context) -> date:
    raw = context.user_data.get("ca_pick_day")
    if isinstance(raw, date):
        return raw
    if isinstance(raw, str):
        return datetime.strptime(raw, "%Y-%m-%d").date()
    return now_moscow().date()


def _ca_menu_message(status: str) -> str:
    return (
        f"🚫 <b>{TRAINING_CANCELLATION_TITLE}</b>\n"
        f"{TRAINING_CANCELLATION_SCOPE_HTML}\n\n"
        f"{status}\n\n"
        f"Выберите действие:"
    )


def _ca_action_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➕ Отменить одно занятие", callback_data="ca_action_create")],
            [InlineKeyboardButton("📅 Отмена на период", callback_data="ca_action_period")],
            [InlineKeyboardButton("↩️ Откатить период", callback_data="ca_action_period_cancel")],
            [
                InlineKeyboardButton(
                    "↩️ Откатить отменённое занятие",
                    callback_data="ca_action_slot_cancel",
                )
            ],
            [InlineKeyboardButton("📚 История", callback_data="ca_action_history")],
            [InlineKeyboardButton("🏠 В меню", callback_data="back_to_menu_main")],
        ]
    )


async def handle_ca_back_to_menu_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from handlers.coach_handlers import handle_back_to_menu_main

    query = update.callback_query
    if query:
        await query.answer()
    for key in list(context.user_data.keys()):
        if key.startswith("ca_"):
            context.user_data.pop(key, None)
    await handle_back_to_menu_main(update, context)
    return ConversationHandler.END


async def start_coach_absence_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    for key in list(context.user_data.keys()):
        if key.startswith("ca_"):
            context.user_data.pop(key, None)
    try:
        with get_db_session() as session:
            user = UserService.get_user_by_telegram_id(session, user_id)
            if not can_manage_coach_absence_flow(user):
                await update.message.reply_text(COACH_ABSENCE_DENIED_MESSAGE)
                return ConversationHandler.END
            if is_coach(user):
                coach = session.query(Coach).filter_by(telegram_id=user_id).first()
                if not coach:
                    await update.message.reply_text("❌ Тренер не найден в базе")
                    return ConversationHandler.END
                context.user_data["ca_coach_id"] = coach.id
                status = format_unified_cancellation_menu_status_html(session, coach.id)
            else:
                coaches = list_active_coaches(session)
                if not coaches:
                    await update.message.reply_text("❌ Нет активных тренеров")
                    return ConversationHandler.END
                keyboard = [
                    [
                        InlineKeyboardButton(
                            (c.first_name or f"ID {c.id}")[:40],
                            callback_data=f"ca_pick_coach_{c.id}",
                        )
                    ]
                    for c in coaches[:20]
                ]
                keyboard.append(
                    [InlineKeyboardButton("🏠 В меню", callback_data="back_to_menu_main")]
                )
                await update.message.reply_text(
                    f"🚫 <b>{TRAINING_CANCELLATION_TITLE}</b>\n\nВыберите тренера:",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="HTML",
                )
                return CA_COACH
    except Exception as e:
        logger.error("start_coach_absence_flow: %s", e, exc_info=True)
        await update.message.reply_text("❌ Ошибка")
        return ConversationHandler.END

    await update.message.reply_text(
        _ca_menu_message(status),
        reply_markup=_ca_action_keyboard(),
        parse_mode="HTML",
    )
    return CA_MENU


async def handle_ca_pick_coach(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cid = int((query.data or "").replace("ca_pick_coach_", ""))
    context.user_data["ca_coach_id"] = cid
    with get_db_session() as session:
        status = format_unified_cancellation_menu_status_html(session, cid)
    await query.edit_message_text(
        _ca_menu_message(status),
        reply_markup=_ca_action_keyboard(),
        parse_mode="HTML",
    )
    return CA_MENU


async def handle_ca_action_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    coach_id = _resolve_coach_id(context)
    if not coach_id:
        return ConversationHandler.END
    with get_db_session() as session:
        text = format_unified_cancellation_history_html(session, coach_id)
    keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="ca_back_menu")]]
    await query.edit_message_text(
        text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML"
    )
    return CA_MENU


async def handle_ca_back_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    coach_id = _resolve_coach_id(context)
    with get_db_session() as session:
        status = format_unified_cancellation_menu_status_html(session, coach_id)
    await query.edit_message_text(
        _ca_menu_message(status),
        reply_markup=_ca_action_keyboard(),
        parse_mode="HTML",
    )
    return CA_MENU


async def _show_cancellation_calendar(update, context, year: int, month: int):
    text, markup = build_cancellation_day_calendar(year, month)
    await _ca_safe_edit(update, context, text, reply_markup=markup)
    return CA_CAL


async def handle_ca_action_create(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not _resolve_coach_id(context):
        return ConversationHandler.END
    now = now_moscow()
    return await _show_cancellation_calendar(update, context, now.year, now.month)


async def handle_ca_cal_nav(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    m = re.match(r"^ca_cal_(\d{4})_(\d{1,2})$", (query.data or "").strip())
    if not m:
        return CA_CAL
    return await _show_cancellation_calendar(
        update, context, int(m.group(1)), int(m.group(2))
    )


async def handle_ca_cal_empty(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()


async def handle_ca_day_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    m = re.match(r"^ca_day_(\d{4})_(\d{1,2})_(\d{1,2})$", (query.data or "").strip())
    if not m:
        return CA_CAL
    year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    pick_day = date(year, month, day)
    context.user_data["ca_pick_day"] = pick_day.isoformat()
    coach_id = _resolve_coach_id(context)
    if not coach_id:
        return ConversationHandler.END
    with get_db_session() as session:
        coach = coach_user_for_slots(session, coach_id)
        if not coach:
            await query.edit_message_text("❌ Тренер не найден")
            return ConversationHandler.END
        slots, virtual = group_slots_for_cancellation_day(session, coach, pick_day)
        context.user_data["ca_virtual_slots"] = virtual
    if not slots:
        await query.edit_message_text(
            f"📭 <b>{pick_day.strftime('%d.%m.%Y')}</b>\n\n"
            "Нет групповых занятий по расписанию на этот день.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 К календарю", callback_data="ca_action_create")]]
            ),
            parse_mode="HTML",
        )
        return CA_SLOT
    keyboard = []
    for slot in slots:
        label = format_training_slot_line(
            slot.training_datetime,
            slot.sport_type,
            age_group=slot.age_group,
            is_individual=False,
            time_only=True,
        )
        if len(label) > 60:
            label = label[:57] + "…"
        keyboard.append(
            [InlineKeyboardButton(label, callback_data=slot_callback_data(slot))]
        )
    keyboard.append([InlineKeyboardButton("🔙 К календарю", callback_data="ca_action_create")])
    await query.edit_message_text(
        f"⏰ <b>Групповые занятия</b> {pick_day.strftime('%d.%m.%Y')}\n\n"
        "Выберите время для <b>отмены</b>:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )
    return CA_SLOT


async def handle_ca_slot_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    kind, value = parse_slot_callback(query.data or "")
    if not kind:
        await query.edit_message_text("❌ Некорректный выбор.")
        return CA_SLOT
    coach_id = _resolve_coach_id(context)
    pick_day = _resolve_pick_day(context)
    virtual = context.user_data.get("ca_virtual_slots") or {}
    with get_db_session() as session:
        coach = coach_user_for_slots(session, coach_id)
        if not coach:
            await query.edit_message_text("❌ Тренер не найден")
            return ConversationHandler.END
        slot, err = resolve_slot_from_callback(
            session, coach, kind, value, virtual, pick_day
        )
    if err or not slot:
        await query.edit_message_text(err or "❌ Ошибка")
        return CA_SLOT
    context.user_data["ca_slot"] = {
        "sport_type": slot.sport_type,
        "age_group": slot.age_group,
        "training_datetime": slot.training_datetime.isoformat(),
        "training_id": slot.training_id,
    }
    line = format_training_slot_line(
        slot.training_datetime,
        slot.sport_type,
        age_group=slot.age_group,
        is_individual=False,
        escape_html=True,
    )
    keyboard = [
        [
            InlineKeyboardButton("✅ Отменить занятие", callback_data="ca_slot_apply"),
            InlineKeyboardButton("🔙 Назад", callback_data=f"ca_day_{pick_day.year}_{pick_day.month}_{pick_day.day}"),
        ]
    ]
    await query.edit_message_text(
        f"🚫 <b>Подтверждение</b>\n\n"
        f"Отменить групповое занятие:\n<b>{line}</b>\n\n"
        "Месячные абонементы спортсменов этой группы будут "
        "<b>продлены на 1 тренировку</b> (как при заморозке). "
        "Списание за этот слот не выполняется.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )
    return CA_SLOT_CONFIRM


async def handle_ca_action_period(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not _resolve_coach_id(context):
        return ConversationHandler.END
    await query.edit_message_text(
        "📅 Введите <b>дату начала</b> периода (ДД.ММ.ГГГГ):\n"
        "<i>Например каникулы: 01.01.2026 — 10.01.2026</i>",
        parse_mode="HTML",
    )
    return CA_PERIOD_START


async def handle_ca_period_start_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    dt = parse_ui_date(update.message.text or "")
    if not dt:
        await update.message.reply_text("❌ Формат: ДД.ММ.ГГГГ")
        return CA_PERIOD_START
    context.user_data["ca_period_start"] = dt
    await update.message.reply_text(
        "📅 Введите <b>дату окончания</b> периода (ДД.ММ.ГГГГ):", parse_mode="HTML"
    )
    return CA_PERIOD_END


async def handle_ca_period_end_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    dt = parse_ui_date(update.message.text or "")
    if not dt:
        await update.message.reply_text("❌ Формат: ДД.ММ.ГГГГ")
        return CA_PERIOD_END
    start = context.user_data.get("ca_period_start")
    if not start:
        await update.message.reply_text(f"❌ Начните снова: {TRAINING_CANCELLATION_BUTTON}")
        return ConversationHandler.END
    if dt < start:
        await update.message.reply_text("❌ Окончание раньше начала")
        return CA_PERIOD_END
    context.user_data["ca_period_end"] = dt
    await update.message.reply_text(
        "📝 Введите причину (например: <i>Каникулы</i>):", parse_mode="HTML"
    )
    return CA_PERIOD_TITLE


async def handle_ca_period_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    title = (update.message.text or "").strip() or "Отмена на период"
    coach_id = _resolve_coach_id(context)
    start = context.user_data.get("ca_period_start")
    end = context.user_data.get("ca_period_end")
    if not coach_id or not start or not end:
        await update.message.reply_text("❌ Сессия сброшена. Начните снова.")
        return ConversationHandler.END
    end_norm = end.replace(hour=23, minute=59, second=59)
    warn = ""
    with get_db_session() as session:
        oids = overlapping_coach_absence_ids(session, coach_id, start, end_norm)
        if oids:
            warn = f"\n\n⚠️ Пересечение с периодом ID: {oids}."
    context.user_data["ca_period_title"] = title
    keyboard = [
        [
            InlineKeyboardButton("✅ Применить", callback_data="ca_period_apply"),
            InlineKeyboardButton("❌ Отмена", callback_data="ca_period_abort"),
        ]
    ]
    await update.message.reply_text(
        f"🚫 <b>Подтверждение периода</b>\n"
        f"С {start.strftime('%d.%m.%Y')} по {end.strftime('%d.%m.%Y')}\n"
        f"Причина: {html.escape(title)}\n\n"
        "Месячные групповые абонементы спортсменов будут продлены на все "
        f"тренировочные дни в периоде (как при заморозке).{warn}",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )
    return CA_PERIOD_CONFIRM


async def handle_ca_period_apply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    coach_id = _resolve_coach_id(context)
    start = context.user_data.get("ca_period_start")
    end = context.user_data.get("ca_period_end")
    title = context.user_data.get("ca_period_title", "Отмена на период")
    if not coach_id or not start or not end:
        await _ca_safe_edit(update, context, "❌ Сессия истекла.")
        return ConversationHandler.END
    end_norm = end.replace(hour=23, minute=59, second=59)
    try:
        with get_db_session() as session:
            oids = overlapping_coach_absence_ids(session, coach_id, start, end_norm)
            if oids:
                await _ca_safe_edit(
                    update,
                    context,
                    f"❌ Пересечение с действующим периодом (ID: {oids}).",
                )
                return ConversationHandler.END
            result = apply_coach_absence_service(
                session,
                coach_id,
                start,
                end_norm,
                title,
                update.effective_user.id,
            )
        if not result.get("success"):
            await _ca_safe_edit(update, context, f"❌ {result.get('message', 'Ошибка')}")
            return ConversationHandler.END
        await _ca_safe_edit(
            update,
            context,
            f"✅ <b>Отмена на период зарегистрирована</b>\n\n"
            f"• Тренер: {html.escape(result.get('coach_name', ''))}\n"
            f"• ID: {result['coach_absence_id']}\n"
            f"• Месячных абонементов обновлено: {result.get('updated_subscriptions', 0)}\n"
            f"• Пропущено: {result.get('skipped_subscriptions', 0)}",
        )
    except Exception as e:
        logger.error("handle_ca_period_apply: %s", e, exc_info=True)
        await _ca_safe_edit(update, context, "❌ Ошибка применения")
    for key in list(context.user_data.keys()):
        if key.startswith("ca_"):
            context.user_data.pop(key, None)
    return ConversationHandler.END


async def handle_ca_period_abort(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await _ca_safe_edit(update, context, "❌ Регистрация периода отменена.")
    return ConversationHandler.END


async def handle_ca_action_period_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    coach_id = _resolve_coach_id(context)
    if not coach_id:
        return ConversationHandler.END
    with get_db_session() as session:
        rows = list_active_coach_absences(session, coach_id)
    if not rows:
        await query.edit_message_text(
            "📭 Нет активных периодов для отката.",
            parse_mode="HTML",
        )
        return CA_MENU
    keyboard = [
        [InlineKeyboardButton(ca_button_label(r.id, r.title), callback_data=f"ca_deact_pick_{r.id}")]
        for r in rows[:15]
    ]
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="ca_back_menu")])
    await query.edit_message_text(
        "Выберите период для <b>отката</b>:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )
    return CA_MENU


async def handle_ca_deact_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ca_id = int((query.data or "").replace("ca_deact_pick_", ""))
    keyboard = [
        [
            InlineKeyboardButton("✅ Да, откатить", callback_data=f"ca_deact_confirm_{ca_id}"),
            InlineKeyboardButton("🔙 Назад", callback_data="ca_action_period_cancel"),
        ]
    ]
    await query.edit_message_text(
        f"Откатить период <b>#{ca_id}</b>? Продления по нему будут пересчитаны.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )
    return CA_MENU


async def handle_ca_action_slot_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    coach_id = _resolve_coach_id(context)
    if not coach_id:
        return ConversationHandler.END
    with get_db_session() as session:
        rows = list_rollbackable_slots_service(session, coach_id)
    if not rows:
        await query.edit_message_text(
            "📭 Нет отменённых групповых занятий для отката (за последние 30 дней).",
            parse_mode="HTML",
        )
        return CA_MENU
    keyboard = [
        [
            InlineKeyboardButton(
                slot_rollback_button_label(r),
                callback_data=f"ca_slot_deact_pick_{r.id}",
            )
        ]
        for r in rows[:15]
    ]
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="ca_back_menu")])
    await query.edit_message_text(
        "Выберите <b>отменённое занятие</b> для отката:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )
    return CA_MENU


async def handle_ca_slot_deact_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    training_id = int((query.data or "").replace("ca_slot_deact_pick_", ""))
    keyboard = [
        [
            InlineKeyboardButton(
                "✅ Да, откатить",
                callback_data=f"ca_slot_deact_confirm_{training_id}",
            ),
            InlineKeyboardButton("🔙 Назад", callback_data="ca_action_slot_cancel"),
        ]
    ]
    await query.edit_message_text(
        f"Откатить отмену занятия <b>#{training_id}</b>? "
        f"Занятие снова станет активным, продления по нему пересчитаются.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )
    return CA_MENU


async def handle_ca_slot_deact_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    training_id = int((query.data or "").replace("ca_slot_deact_confirm_", ""))
    coach_id = _resolve_coach_id(context)
    if not coach_id:
        await _ca_safe_edit(update, context, "❌ Сессия истекла. Начните снова.")
        return ConversationHandler.END
    try:
        with get_db_session() as session:
            result = revert_slot_cancellation_service(session, training_id, coach_id)
        if not result.get("success"):
            await _ca_safe_edit(update, context, f"❌ {result.get('message', 'Ошибка')}")
            return ConversationHandler.END
        extra = ""
        if not result.get("has_audit", True):
            extra = "\n<i>Продления по абонементам не восстановлены (старая отмена без аудита).</i>"
        await _ca_safe_edit(
            update,
            context,
            f"✅ <b>Отмена занятия откачена</b> (#{training_id})\n"
            f"Проверено абонементов: {result.get('checked', 0)}\n"
            f"Восстановлено сроков: {result.get('reverted', 0)}\n"
            f"Обновлено: {result.get('updated_subscriptions', 0)}"
            f"{extra}",
        )
    except Exception as e:
        logger.error("handle_ca_slot_deact_confirm: %s", e, exc_info=True)
        await _ca_safe_edit(update, context, "❌ Ошибка отката занятия")
    return ConversationHandler.END


async def handle_ca_deact_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ca_id = int((query.data or "").replace("ca_deact_confirm_", ""))
    try:
        with get_db_session() as session:
            result = deactivate_coach_absence_service(session, ca_id)
        if not result.get("success"):
            await _ca_safe_edit(update, context, f"❌ {result.get('message', 'Ошибка')}")
            return ConversationHandler.END
        await _ca_safe_edit(
            update,
            context,
            f"✅ <b>Период отмены отключён</b> (#{ca_id})\n"
            f"Проверено: {result.get('checked', 0)}\n"
            f"Обновлено: {result.get('updated_subscriptions', 0)}",
        )
    except Exception as e:
        logger.error("handle_ca_deact_confirm: %s", e, exc_info=True)
        await _ca_safe_edit(update, context, "❌ Ошибка отката")
    return ConversationHandler.END


async def handle_ca_slot_apply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    coach_id = _resolve_coach_id(context)
    slot_data = context.user_data.get("ca_slot")
    if not coach_id or not slot_data:
        await _ca_safe_edit(update, context, "❌ Сессия истекла. Начните снова.")
        return ConversationHandler.END
    from services.attendance_training_flow import TodaySlotDisplay

    slot = TodaySlotDisplay(
        is_virtual=not slot_data.get("training_id"),
        training_id=slot_data.get("training_id"),
        virtual_token=None,
        sport_type=slot_data["sport_type"],
        age_group=slot_data["age_group"],
        training_datetime=datetime.fromisoformat(slot_data["training_datetime"]),
        is_individual_format=False,
    )
    try:
        with get_db_session() as session:
            result = apply_slot_cancellation_service(
                session,
                coach_id,
                slot,
                update.effective_user.id,
            )
        if not result.get("success"):
            await _ca_safe_edit(update, context, f"❌ {result.get('message', 'Ошибка')}")
            return ConversationHandler.END
        dt = result.get("training_datetime") or slot.training_datetime
        line = format_training_slot_line(
            dt,
            slot.sport_type,
            age_group=slot.age_group,
            is_individual=False,
            escape_html=True,
        )
        await _ca_safe_edit(
            update,
            context,
            f"✅ <b>Занятие отменено</b>\n\n"
            f"{line}\n\n"
            f"• Обновлено месячных абонементов: {result.get('updated_subscriptions', 0)}\n"
            f"• Без изменений: {result.get('skipped_subscriptions', 0)}",
        )
    except Exception as e:
        logger.error("handle_ca_slot_apply: %s", e, exc_info=True)
        await _ca_safe_edit(update, context, "❌ Ошибка применения")
    for key in list(context.user_data.keys()):
        if key.startswith("ca_"):
            context.user_data.pop(key, None)
    return ConversationHandler.END


def build_coach_absence_conversation() -> ConversationHandler:
    from handlers.coach_handlers import athletes_list

    _list_pat = "^(📋 Список спортсменов)$"
    return ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.Regex(f"^({re.escape(TRAINING_CANCELLATION_BUTTON)})$"),
                start_coach_absence_flow,
            )
        ],
        states={
            CA_COACH: [
                CallbackQueryHandler(handle_ca_pick_coach, pattern=r"^ca_pick_coach_\d+$"),
                CallbackQueryHandler(handle_ca_back_to_menu_main, pattern=r"^back_to_menu_main$"),
            ],
            CA_MENU: [
                CallbackQueryHandler(handle_ca_back_to_menu_main, pattern=r"^back_to_menu_main$"),
                CallbackQueryHandler(handle_ca_back_menu, pattern=r"^ca_back_menu$"),
                CallbackQueryHandler(handle_ca_action_create, pattern=r"^ca_action_create$"),
                CallbackQueryHandler(handle_ca_action_period, pattern=r"^ca_action_period$"),
                CallbackQueryHandler(handle_ca_action_period_cancel, pattern=r"^ca_action_period_cancel$"),
                CallbackQueryHandler(handle_ca_action_slot_cancel, pattern=r"^ca_action_slot_cancel$"),
                CallbackQueryHandler(handle_ca_action_history, pattern=r"^ca_action_history$"),
                CallbackQueryHandler(handle_ca_deact_pick, pattern=r"^ca_deact_pick_\d+$"),
                CallbackQueryHandler(handle_ca_deact_confirm, pattern=r"^ca_deact_confirm_\d+$"),
                CallbackQueryHandler(handle_ca_slot_deact_pick, pattern=r"^ca_slot_deact_pick_\d+$"),
                CallbackQueryHandler(handle_ca_slot_deact_confirm, pattern=r"^ca_slot_deact_confirm_\d+$"),
            ],
            CA_CAL: [
                CallbackQueryHandler(handle_ca_cal_nav, pattern=r"^ca_cal_\d{4}_\d{1,2}$"),
                CallbackQueryHandler(handle_ca_cal_empty, pattern=r"^ca_cal_empty$"),
                CallbackQueryHandler(handle_ca_day_pick, pattern=r"^ca_day_\d{4}_\d{1,2}_\d{1,2}$"),
                CallbackQueryHandler(handle_ca_back_menu, pattern=r"^ca_back_menu$"),
                CallbackQueryHandler(handle_ca_action_create, pattern=r"^ca_action_create$"),
            ],
            CA_SLOT: [
                CallbackQueryHandler(handle_ca_slot_pick, pattern=r"^ca_slot_(tid_\d+|v_.+)$"),
                CallbackQueryHandler(handle_ca_day_pick, pattern=r"^ca_day_\d{4}_\d{1,2}_\d{1,2}$"),
                CallbackQueryHandler(handle_ca_cal_nav, pattern=r"^ca_cal_\d{4}_\d{1,2}$"),
                CallbackQueryHandler(handle_ca_action_create, pattern=r"^ca_action_create$"),
                CallbackQueryHandler(handle_ca_back_menu, pattern=r"^ca_back_menu$"),
            ],
            CA_SLOT_CONFIRM: [
                CallbackQueryHandler(handle_ca_slot_apply, pattern=r"^ca_slot_apply$"),
                CallbackQueryHandler(handle_ca_slot_pick, pattern=r"^ca_slot_(tid_\d+|v_.+)$"),
                CallbackQueryHandler(handle_ca_day_pick, pattern=r"^ca_day_\d{4}_\d{1,2}_\d{1,2}$"),
            ],
            CA_PERIOD_START: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_ca_period_start_date)
            ],
            CA_PERIOD_END: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_ca_period_end_date)
            ],
            CA_PERIOD_TITLE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_ca_period_title)
            ],
            CA_PERIOD_CONFIRM: [
                CallbackQueryHandler(handle_ca_period_apply, pattern=r"^ca_period_apply$"),
                CallbackQueryHandler(handle_ca_period_abort, pattern=r"^ca_period_abort$"),
            ],
        },
        fallbacks=[
            CallbackQueryHandler(handle_ca_back_to_menu_main, pattern=r"^back_to_menu_main$"),
            CallbackQueryHandler(handle_ca_slot_apply, pattern=r"^ca_slot_apply$"),
            CallbackQueryHandler(handle_ca_period_apply, pattern=r"^ca_period_apply$"),
            CallbackQueryHandler(handle_ca_period_abort, pattern=r"^ca_period_abort$"),
            CallbackQueryHandler(handle_ca_deact_confirm, pattern=r"^ca_deact_confirm_\d+$"),
            CallbackQueryHandler(handle_ca_slot_deact_confirm, pattern=r"^ca_slot_deact_confirm_\d+$"),
            CommandHandler("cancel", cancel_global_freeze),
            MessageHandler(filters.Regex(_list_pat), athletes_list),
        ],
        allow_reentry=True,
    )
