"""Диалог «Отсутствие тренера» (болезнь): только абонементы этого тренера."""
import html
import logging
from datetime import datetime

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
from services.coach_absence_service import (
    apply_coach_absence_service,
    ca_button_label,
    deactivate_coach_absence_service,
    format_coach_absence_history_html,
    format_coach_absence_status_html,
    list_active_coach_absences,
    list_active_coaches,
    overlapping_coach_absence_ids,
    parse_ui_date,
)
from services.user_service import UserService
from services.permissions import (
    can_manage_coach_absence_flow,
    is_coach,
    COACH_ABSENCE_DENIED_MESSAGE,
)

logger = logging.getLogger(__name__)

CA_MENU, CA_COACH, CA_START, CA_END, CA_TITLE, CA_CONFIRM = range(6)


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


async def start_coach_absence_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    context.user_data.pop("ca_coach_id", None)
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
                status = format_coach_absence_status_html(session, coach.id)
                name = html.escape((coach.first_name or "").strip() or f"ID {coach.id}")
                header = f"🤒 <b>Отсутствие тренера</b>\n👤 {name}\n\n{status}\n\n"
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
                    [InlineKeyboardButton("❌ Отмена", callback_data="ca_cancel_flow")]
                )
                await update.message.reply_text(
                    "🤒 <b>Отсутствие тренера</b>\n\nВыберите тренера:",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="HTML",
                )
                return CA_COACH
    except Exception as e:
        logger.error("start_coach_absence_flow: %s", e, exc_info=True)
        await update.message.reply_text("❌ Ошибка")
        return ConversationHandler.END

    keyboard = [
        [InlineKeyboardButton("➕ Зарегистрировать", callback_data="ca_action_create")],
        [InlineKeyboardButton("❌ Отменить действующее", callback_data="ca_action_cancel")],
        [InlineKeyboardButton("📚 История", callback_data="ca_action_history")],
        [InlineKeyboardButton("🔙 Закрыть", callback_data="ca_cancel_flow")],
    ]
    await update.message.reply_text(
        header + "Выберите действие:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )
    return CA_MENU


async def handle_ca_pick_coach(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cid = int((query.data or "").replace("ca_pick_coach_", ""))
    context.user_data["ca_coach_id"] = cid
    with get_db_session() as session:
        coach = session.query(Coach).filter_by(id=cid).first()
        status = format_coach_absence_status_html(session, cid)
        name = html.escape((coach.first_name or "").strip() if coach else f"ID {cid}")
    keyboard = [
        [InlineKeyboardButton("➕ Зарегистрировать", callback_data="ca_action_create")],
        [InlineKeyboardButton("❌ Отменить действующее", callback_data="ca_action_cancel")],
        [InlineKeyboardButton("📚 История", callback_data="ca_action_history")],
        [InlineKeyboardButton("🔙 Закрыть", callback_data="ca_cancel_flow")],
    ]
    await query.edit_message_text(
        f"🤒 <b>Отсутствие тренера</b>\n👤 {name}\n\n{status}\n\nВыберите действие:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )
    return CA_MENU


async def handle_ca_flow_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
    for key in list(context.user_data.keys()):
        if key.startswith("ca_"):
            context.user_data.pop(key, None)
    await _ca_safe_edit(update, context, "❌ Операция отменена.")
    return ConversationHandler.END


async def handle_ca_action_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    coach_id = _resolve_coach_id(context)
    if not coach_id:
        return ConversationHandler.END
    with get_db_session() as session:
        text = format_coach_absence_history_html(session, coach_id)
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
        status = format_coach_absence_status_html(session, coach_id)
        coach = session.query(Coach).filter_by(id=coach_id).first()
        name = html.escape((coach.first_name or "").strip() if coach else "")
    keyboard = [
        [InlineKeyboardButton("➕ Зарегистрировать", callback_data="ca_action_create")],
        [InlineKeyboardButton("❌ Отменить действующее", callback_data="ca_action_cancel")],
        [InlineKeyboardButton("📚 История", callback_data="ca_action_history")],
        [InlineKeyboardButton("🔙 Закрыть", callback_data="ca_cancel_flow")],
    ]
    await query.edit_message_text(
        f"🤒 <b>Отсутствие тренера</b>\n👤 {name}\n\n{status}\n\nВыберите действие:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )
    return CA_MENU


async def handle_ca_action_create(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not _resolve_coach_id(context):
        return ConversationHandler.END
    await query.edit_message_text(
        "📅 Введите <b>дату начала</b> отсутствия (ДД.ММ.ГГГГ):",
        parse_mode="HTML",
    )
    return CA_START


async def handle_ca_start_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    dt = parse_ui_date(update.message.text or "")
    if not dt:
        await update.message.reply_text("❌ Формат: ДД.ММ.ГГГГ")
        return CA_START
    context.user_data["ca_start"] = dt
    await update.message.reply_text(
        "📅 Введите <b>дату окончания</b> (ДД.ММ.ГГГГ):", parse_mode="HTML"
    )
    return CA_END


async def handle_ca_end_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    dt = parse_ui_date(update.message.text or "")
    if not dt:
        await update.message.reply_text("❌ Формат: ДД.ММ.ГГГГ")
        return CA_END
    start = context.user_data.get("ca_start")
    if not start:
        await update.message.reply_text("❌ Начните снова: 🤒 Отсутствие тренера")
        return ConversationHandler.END
    if dt < start:
        await update.message.reply_text("❌ Окончание раньше начала")
        return CA_END
    context.user_data["ca_end"] = dt
    await update.message.reply_text(
        "📝 Введите причину (например: <i>Болезнь</i>):", parse_mode="HTML"
    )
    return CA_TITLE


async def handle_ca_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    title = (update.message.text or "").strip() or "Отсутствие тренера"
    coach_id = _resolve_coach_id(context)
    start = context.user_data.get("ca_start")
    end = context.user_data.get("ca_end")
    if not coach_id or not start or not end:
        await update.message.reply_text("❌ Сессия сброшена. Начните снова.")
        return ConversationHandler.END
    end_norm = end.replace(hour=23, minute=59, second=59)
    warn = ""
    with get_db_session() as session:
        oids = overlapping_coach_absence_ids(session, coach_id, start, end_norm)
        if oids:
            warn = (
                f"\n\n⚠️ Пересечение с отсутствием ID: {oids}. "
                "Подтверждение всё равно возможно только без пересечений при apply."
            )
    context.user_data["ca_title"] = title
    keyboard = [
        [
            InlineKeyboardButton("✅ Применить", callback_data="ca_apply_confirm"),
            InlineKeyboardButton("❌ Отмена", callback_data="ca_cancel_confirm"),
        ]
    ]
    await update.message.reply_text(
        f"🤒 <b>Подтверждение</b>\n"
        f"Период: {start.strftime('%d.%m.%Y')} — {end.strftime('%d.%m.%Y')}\n"
        f"Причина: {html.escape(title)}{warn}",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )
    return CA_CONFIRM


async def handle_ca_confirm_apply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    coach_id = _resolve_coach_id(context)
    start = context.user_data.get("ca_start")
    end = context.user_data.get("ca_end")
    title = context.user_data.get("ca_title", "Отсутствие тренера")
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
                    f"❌ Пересечение с действующим отсутствием (ID: {oids}).",
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
            f"✅ <b>Отсутствие зарегистрировано</b>\n\n"
            f"• Тренер: {html.escape(result.get('coach_name', ''))}\n"
            f"• ID: {result['coach_absence_id']}\n"
            f"• Проверено абонементов: {result.get('updated_subscriptions', 0) + result.get('skipped_subscriptions', 0)}\n"
            f"• Обновлено: {result.get('updated_subscriptions', 0)}\n"
            f"• Без изменений: {result.get('skipped_subscriptions', 0)}",
        )
    except Exception as e:
        logger.error("handle_ca_confirm_apply: %s", e, exc_info=True)
        await _ca_safe_edit(update, context, "❌ Ошибка применения")
    for key in list(context.user_data.keys()):
        if key.startswith("ca_"):
            context.user_data.pop(key, None)
    return ConversationHandler.END


async def handle_ca_confirm_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await _ca_safe_edit(update, context, "❌ Создание отменено.")
    return ConversationHandler.END


async def handle_ca_action_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    coach_id = _resolve_coach_id(context)
    if not coach_id:
        return ConversationHandler.END
    with get_db_session() as session:
        rows = list_active_coach_absences(session, coach_id)
    if not rows:
        await query.edit_message_text(
            "📭 Нет активных отсутствий для отмены.",
            parse_mode="HTML",
        )
        return CA_MENU
    keyboard = [
        [InlineKeyboardButton(ca_button_label(r.id, r.title), callback_data=f"ca_deact_pick_{r.id}")]
        for r in rows[:15]
    ]
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="ca_back_menu")])
    await query.edit_message_text(
        "Выберите отсутствие для <b>отмены</b>:",
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
            InlineKeyboardButton("✅ Да, отменить", callback_data=f"ca_deact_confirm_{ca_id}"),
            InlineKeyboardButton("🔙 Назад", callback_data="ca_action_cancel"),
        ]
    ]
    await query.edit_message_text(
        f"Отменить отсутствие <b>#{ca_id}</b>? Продления по нему будут пересчитаны.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )
    return CA_MENU


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
            f"✅ <b>Отсутствие отменено</b> (#{ca_id})\n"
            f"Проверено: {result.get('checked', 0)}\n"
            f"Обновлено: {result.get('updated_subscriptions', 0)}",
        )
    except Exception as e:
        logger.error("handle_ca_deact_confirm: %s", e, exc_info=True)
        await _ca_safe_edit(update, context, "❌ Ошибка отмены")
    return ConversationHandler.END


def build_coach_absence_conversation() -> ConversationHandler:
    from handlers.coach_handlers import athletes_list

    _list_pat = "^(📋 Список спортсменов)$"
    return ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.Regex("^(🤒 Отсутствие тренера)$"), start_coach_absence_flow
            )
        ],
        states={
            CA_COACH: [
                CallbackQueryHandler(handle_ca_pick_coach, pattern=r"^ca_pick_coach_\d+$"),
                CallbackQueryHandler(handle_ca_flow_cancel, pattern=r"^ca_cancel_flow$"),
            ],
            CA_MENU: [
                CallbackQueryHandler(handle_ca_flow_cancel, pattern=r"^ca_cancel_flow$"),
                CallbackQueryHandler(handle_ca_back_menu, pattern=r"^ca_back_menu$"),
                CallbackQueryHandler(handle_ca_action_create, pattern=r"^ca_action_create$"),
                CallbackQueryHandler(handle_ca_action_cancel, pattern=r"^ca_action_cancel$"),
                CallbackQueryHandler(handle_ca_action_history, pattern=r"^ca_action_history$"),
                CallbackQueryHandler(handle_ca_deact_pick, pattern=r"^ca_deact_pick_\d+$"),
                CallbackQueryHandler(handle_ca_deact_confirm, pattern=r"^ca_deact_confirm_\d+$"),
            ],
            CA_START: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_ca_start_date)
            ],
            CA_END: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_ca_end_date)],
            CA_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_ca_title)],
            CA_CONFIRM: [
                CallbackQueryHandler(handle_ca_confirm_apply, pattern=r"^ca_apply_confirm$"),
                CallbackQueryHandler(handle_ca_confirm_cancel, pattern=r"^ca_cancel_confirm$"),
            ],
        },
        fallbacks=[
            CallbackQueryHandler(handle_ca_flow_cancel, pattern=r"^ca_cancel_flow$"),
            CallbackQueryHandler(handle_ca_confirm_apply, pattern=r"^ca_apply_confirm$"),
            CallbackQueryHandler(handle_ca_confirm_cancel, pattern=r"^ca_cancel_confirm$"),
            CallbackQueryHandler(handle_ca_deact_confirm, pattern=r"^ca_deact_confirm_\d+$"),
            CommandHandler("cancel", cancel_global_freeze),
            MessageHandler(filters.Regex(_list_pat), athletes_list),
        ],
        allow_reentry=True,
    )
