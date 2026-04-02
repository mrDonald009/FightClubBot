"""Роутер для регистрации всех обработчиков."""
import html
import logging
import re
from datetime import datetime, timedelta
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
)

from core.application import HandlerRegistrar
from core.database import get_db_session
from services.user_service import UserService
from database.db_utils import get_user_role
from services.subscription_service import SubscriptionService
from services.subscription_audit_service import run_subscription_audit, format_audit_report
from database.db_utils import (
    apply_global_freeze,
    deactivate_global_freeze_and_migrate,
    update_global_freeze_title,
)
from utils.time_utils import now_moscow
from handlers.start import start
from handlers.coach_handlers import (
    coach_menu,
    add_athlete_start,
    add_athlete_full_name,
    add_athlete_phone,
    add_athlete_birth_date,
    add_athlete_medical,
    add_athlete_age_group,
    add_athlete_subscription,
    handle_add_athlete_calendar_nav,
    handle_add_athlete_calendar_date_pick,
    handle_add_athlete_calendar_ignore,
    handle_add_athlete_shift_confirm,
    handle_add_athlete_shift_cancel,
    handle_training_date_selection,
    athletes_list,
    athletes_list_filtered,
    athletes_categories,
    cancel_athlete_creation,
    cancel_global_freeze,
    handle_back_to_menu_main,
    handle_show_more_info,
    start_training,
    handle_attendance_training_list,
    show_coach_calendar,
    handle_calendar_navigation,
    handle_calendar_date_click,
    handle_calendar_empty_click,
    ATHLETE_FULL_NAME,
    ATHLETE_PHONE,
    ATHLETE_BIRTH_DATE,
    ATHLETE_MEDICAL,
    ATHLETE_AGE_GROUP,
    ATHLETE_SUBSCRIPTION,
    ATHLETE_TRAINING_DATE,
)
from handlers.card_handlers import (
    show_athlete_card,
    show_subscription_card,
    handle_back_to_list,
    handle_back_to_menu,
    show_my_subscription,
    handle_athlete_back_to_menu,
    show_subscription_history,
    view_subscription_from_history,
    handle_activate_subscription,
    handle_activation_calendar_nav,
    handle_activation_date_pick,
    handle_activation_ignore,
    handle_activation_shift_confirm,
    handle_activation_shift_cancel,
    show_my_athlete_card,
    show_athlete_visits,
    show_athlete_stats,
    show_restore_menu,
    execute_restore_training,
    show_edit_athlete_menu,
    select_subscription,
    view_subscription_card,
    handle_freeze_subscription_start,
    handle_freeze_calendar_nav,
    handle_freeze_date_pick,
    handle_freeze_ignore,
    handle_unfreeze_subscription,
)
from handlers.attendance_handlers import (
    select_training_for_attendance,
    mark_attendance_start,
    handle_training_selection,
    execute_mark_attendance,
)

logger = logging.getLogger(__name__)

# Состояния диалога массовой заморозки
GF_ACTION_MENU, GF_START_DATE, GF_END_DATE, GF_TITLE, GF_CONFIRM, GF_EDIT_TITLE = range(6)


# Заглушки для обработчиков, которые еще не реализованы
async def handle_statistics(update, context):
    """Обработчик для статистики посещений (в разработке)."""
    await update.message.reply_text("📊 Функция в разработке")


async def handle_financial_stats(update, context):
    """Обработчик для финансовой статистики (в разработке)."""
    await update.message.reply_text("💰 Функция в разработке")


async def handle_attendance(update, context):
    """Обработчик для отметки посещения (в разработке)."""
    await update.message.reply_text("📅 Функция в разработке")


async def handle_settings(update, context):
    """Обработчик для настроек (в разработке)."""
    await update.message.reply_text("⚙️ Функция в разработке")


async def check_all_subscriptions(update, context):
    """
    Проверить и обновить статусы всех абонементов.
    
    Требует права тренера или администратора.
    """
    user_id = update.effective_user.id

    # Проверяем права (только админ или тренер)
    try:
        with get_db_session() as session:
            user = UserService.get_user_by_telegram_id(session, user_id)
            if not user or get_user_role(user) not in ['coach', 'admin']:
                await update.message.reply_text("❌ У вас нет прав для этой команды")
                return
    except Exception as e:
        logger.error(f"Ошибка при проверке прав пользователя {user_id}: {e}", exc_info=True)
        await update.message.reply_text("❌ Произошла ошибка при проверке прав")
        return

    # Выполняем проверку
    try:
        updated_count = SubscriptionService.check_and_update_subscriptions()
        await update.message.reply_text(
            f"🔄 Проверка абонементов завершена\n"
            f"✅ Обновлено статусов: {updated_count}\n\n"
            f"Теперь все абонементы имеют актуальный статус."
        )
    except Exception as e:
        logger.error(f"Ошибка при проверке абонементов: {e}", exc_info=True)
        await update.message.reply_text("❌ Произошла ошибка при проверке абонементов")


async def audit_subscriptions_now(update, context):
    """
    Ручной запуск read-only аудита абонементов.
    Требует права тренера или администратора.
    """
    user_id = update.effective_user.id

    try:
        with get_db_session() as session:
            user = UserService.get_user_by_telegram_id(session, user_id)
            if not user or get_user_role(user) not in ["coach", "admin"]:
                await update.message.reply_text("❌ У вас нет прав для этой команды")
                return
    except Exception as e:
        logger.error(f"Ошибка при проверке прав пользователя {user_id}: {e}", exc_info=True)
        await update.message.reply_text("❌ Произошла ошибка при проверке прав")
        return

    try:
        with get_db_session() as session:
            report = run_subscription_audit(session)
        await update.message.reply_text(
            format_audit_report(report),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error(f"Ошибка при ручном аудите абонементов: {e}", exc_info=True)
        await update.message.reply_text("❌ Произошла ошибка при аудите абонементов")


async def create_global_freeze(update, context):
    """
    Создать и применить массовую заморозку.
    Формат:
      /global_freeze YYYY-MM-DD YYYY-MM-DD Название
    Пример:
      /global_freeze 2026-05-01 2026-05-09 Майские праздники
    """
    user_id = update.effective_user.id
    try:
        with get_db_session() as session:
            user = UserService.get_user_by_telegram_id(session, user_id)
            if not user or get_user_role(user) not in ['coach', 'admin']:
                await update.message.reply_text("❌ У вас нет прав для этой команды")
                return
    except Exception as e:
        logger.error(f"Ошибка при проверке прав пользователя {user_id}: {e}", exc_info=True)
        await update.message.reply_text("❌ Ошибка проверки прав")
        return

    args = context.args or []
    if len(args) < 2:
        try:
            with get_db_session() as session:
                status_block = _format_current_global_freezes_html(session)
        except Exception:
            status_block = ""
        await update.message.reply_text(
            f"{status_block}\n\n"
            "Формат: /global_freeze YYYY-MM-DD YYYY-MM-DD Название\n"
            "Пример: /global_freeze 2026-05-01 2026-05-09 Майские праздники",
            parse_mode="HTML",
        )
        return

    try:
        start_date = datetime.strptime(args[0], "%Y-%m-%d")
        end_date = datetime.strptime(args[1], "%Y-%m-%d")
    except Exception:
        await update.message.reply_text("❌ Неверный формат даты. Используйте YYYY-MM-DD.")
        return

    title = " ".join(args[2:]).strip() if len(args) > 2 else "Массовая заморозка"

    try:
        with get_db_session() as session:
            result = apply_global_freeze(
                session=session,
                start_date=start_date,
                end_date=end_date,
                title=title,
                created_by=user_id,
            )
            if not result.get("success"):
                await update.message.reply_text(f"❌ {result.get('message', 'Не удалось применить заморозку')}")
                return

            await update.message.reply_text(
                f"✅ Массовая заморозка применена\n"
                f"• ID: {result['global_freeze_id']}\n"
                f"• Период: {result['start_date'].strftime('%d.%m.%Y')} — {result['end_date'].strftime('%d.%m.%Y')}\n"
                f"• Обновлено абонементов: {result['updated_subscriptions']}\n"
                f"• Пропущено: {result['skipped_subscriptions']}\n"
                f"• Суммарно добавлено тренировочных дней: {result.get('total_training_days_added', 0)}\n"
                f"• Название: {title}"
            )
    except Exception as e:
        logger.error(f"Ошибка применения массовой заморозки: {e}", exc_info=True)
        await update.message.reply_text("❌ Ошибка при применении массовой заморозки")


def _parse_ui_date(text: str):
    """Парсинг даты UI формата ДД.ММ.ГГГГ."""
    try:
        return datetime.strptime(text.strip(), "%d.%m.%Y")
    except Exception:
        return None


def _format_current_global_freezes_html(session) -> str:
    """
    Текст для UI: массовые заморозки, действующие «сейчас» (по времени и is_active).
    """
    from database.models import GlobalFreeze

    now = now_moscow()
    rows = (
        session.query(GlobalFreeze)
        .filter(GlobalFreeze.is_active == True)
        .filter(GlobalFreeze.start_date <= now)
        .filter(GlobalFreeze.end_date >= now)
        .order_by(GlobalFreeze.id.asc())
        .all()
    )
    if not rows:
        return "📭 <b>Сейчас действующих массовых заморозок нет.</b>"

    header = (
        "📌 <b>Сейчас действует массовая заморозка:</b>"
        if len(rows) == 1
        else "📌 <b>Сейчас действуют массовые заморозки:</b>"
    )
    lines = [header]
    for g in rows:
        title = html.escape((g.title or "").strip() or "без названия")
        ds = g.start_date.strftime("%d.%m.%Y")
        de = g.end_date.strftime("%d.%m.%Y")
        lines.append(f"• ID <code>{g.id}</code> — <b>{title}</b>")
        lines.append(f"  <i>{ds} — {de}</i>")
    return "\n".join(lines)


def _list_active_global_freezes(session):
    """Все массовые заморозки с is_active=True (в т.ч. будущие по календарю)."""
    from database.models import GlobalFreeze

    return (
        session.query(GlobalFreeze)
        .filter(GlobalFreeze.is_active == True)
        .order_by(GlobalFreeze.start_date.asc())
        .all()
    )


def _gf_keyboard_button_label(g) -> str:
    t = (g.title or "").strip() or "без названия"
    if len(t) > 28:
        t = t[:25] + "…"
    return f"#{g.id} {t}"


async def start_global_freeze_flow(update, context):
    """Показать меню массовой заморозки."""
    user_id = update.effective_user.id
    try:
        with get_db_session() as session:
            user = UserService.get_user_by_telegram_id(session, user_id)
            if not user or get_user_role(user) not in ['coach', 'admin']:
                await update.message.reply_text("❌ У вас нет прав для этой функции")
                return ConversationHandler.END
            status_block = _format_current_global_freezes_html(session)
    except Exception as e:
        logger.error(f"Ошибка проверки прав для массовой заморозки: {e}", exc_info=True)
        await update.message.reply_text("❌ Ошибка проверки прав")
        return ConversationHandler.END

    context.user_data.pop("gf_start_date", None)
    context.user_data.pop("gf_end_date", None)
    context.user_data.pop("gf_title", None)
    context.user_data.pop("gf_edit_id", None)

    await update.message.reply_text(
        "🌍 <b>МАССОВАЯ ЗАМОРОЗКА</b>\n\n"
        f"{status_block}\n\n"
        "Выберите действие:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Создать новую", callback_data="gf_action_create")],
            [InlineKeyboardButton("❌ Отмена массовой заморозки", callback_data="gf_action_cancel")],
            [InlineKeyboardButton("✏️ Редактирование массовой", callback_data="gf_action_edit")],
        ]),
    )
    return GF_ACTION_MENU


async def handle_global_freeze_action_create(update, context):
    """Перейти к созданию новой массовой заморозки (ввод дат)."""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    try:
        with get_db_session() as session:
            user = UserService.get_user_by_telegram_id(session, user_id)
            if not user or get_user_role(user) not in ['coach', 'admin']:
                await query.edit_message_text("❌ У вас нет прав для этой функции")
                return ConversationHandler.END
            status_block = _format_current_global_freezes_html(session)
    except Exception as e:
        logger.error(f"Ошибка проверки прав (gf_action_create): {e}", exc_info=True)
        await query.edit_message_text("❌ Ошибка проверки прав")
        return ConversationHandler.END

    context.user_data.pop("gf_start_date", None)
    context.user_data.pop("gf_end_date", None)
    context.user_data.pop("gf_title", None)
    context.user_data.pop("gf_edit_id", None)

    await query.edit_message_text(
        f"{status_block}\n\n"
        "Введите <b>дату начала</b> в формате <b>ДД.ММ.ГГГГ</b>.\n"
        "Для отмены: /cancel",
        parse_mode="HTML",
    )
    return GF_START_DATE


async def handle_global_freeze_action_cancel(update, context):
    """Деактивация массовой заморозки: список активных → подтверждение."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    try:
        with get_db_session() as session:
            user = UserService.get_user_by_telegram_id(session, user_id)
            if not user or get_user_role(user) not in ["coach", "admin"]:
                await query.edit_message_text("❌ У вас нет прав для этой функции")
                return ConversationHandler.END
            rows = _list_active_global_freezes(session)
    except Exception as e:
        logger.error(f"Ошибка (gf_action_cancel): {e}", exc_info=True)
        await query.edit_message_text("❌ Ошибка при загрузке списка")
        return ConversationHandler.END

    if not rows:
        await query.edit_message_text(
            "📭 Нет <b>активных</b> массовых заморозок для деактивации.\n\n"
            "Отключённые ранее записи остаются в базе как история.\n"
            "При необходимости используйте команду:\n"
            "<code>/global_freeze_deactivate &lt;id&gt;</code>",
            parse_mode="HTML",
        )
        return ConversationHandler.END

    kb = [
        [InlineKeyboardButton(_gf_keyboard_button_label(g), callback_data=f"gf_deact_pick_{g.id}")]
        for g in rows
    ]
    await query.edit_message_text(
        "❌ <b>Снять действие</b> массовой заморозки\n\n"
        "Выберите запись. Для затронутых <b>месячных</b> абонементов будет выполнен пересчёт "
        "(даты, списания, остаток).",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(kb),
    )
    return GF_ACTION_MENU


async def handle_gf_deact_pick(update, context):
    query = update.callback_query
    await query.answer()
    m = re.fullmatch(r"gf_deact_pick_(\d+)", query.data or "")
    if not m:
        return GF_ACTION_MENU
    gf_id = int(m.group(1))
    user_id = query.from_user.id
    try:
        with get_db_session() as session:
            user = UserService.get_user_by_telegram_id(session, user_id)
            if not user or get_user_role(user) not in ["coach", "admin"]:
                await query.edit_message_text("❌ У вас нет прав для этой функции")
                return ConversationHandler.END
            from database.models import GlobalFreeze

            gf = session.query(GlobalFreeze).filter_by(id=gf_id).first()
    except Exception as e:
        logger.error(f"Ошибка (gf_deact_pick): {e}", exc_info=True)
        await query.edit_message_text("❌ Ошибка")
        return ConversationHandler.END

    if not gf or not gf.is_active:
        await query.edit_message_text("❌ Запись не найдена или уже не активна.")
        return ConversationHandler.END

    title = html.escape((gf.title or "").strip() or "без названия")
    ds = gf.start_date.strftime("%d.%m.%Y")
    de = gf.end_date.strftime("%d.%m.%Y")
    await query.edit_message_text(
        "⚠️ <b>Подтверждение деактивации</b>\n\n"
        f"ID: <code>{gf_id}</code>\n"
        f"Название: <b>{title}</b>\n"
        f"Период: <i>{ds} — {de}</i>\n\n"
        "Деактивировать? Ограничения по массовой заморозке перестанут действовать.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "✅ Да, деактивировать",
                        callback_data=f"gf_deact_confirm_{gf_id}",
                    )
                ],
                [InlineKeyboardButton("↩️ Нет", callback_data="gf_deact_abort")],
            ]
        ),
    )
    return GF_ACTION_MENU


async def handle_gf_deact_confirm(update, context):
    query = update.callback_query
    await query.answer()
    m = re.fullmatch(r"gf_deact_confirm_(\d+)", query.data or "")
    if not m:
        return GF_ACTION_MENU
    gf_id = int(m.group(1))
    user_id = query.from_user.id

    try:
        with get_db_session() as session:
            user = UserService.get_user_by_telegram_id(session, user_id)
            if not user or get_user_role(user) not in ["coach", "admin"]:
                await query.edit_message_text("❌ У вас нет прав для этой функции")
                return ConversationHandler.END
            result = deactivate_global_freeze_and_migrate(session, gf_id)
    except Exception as e:
        logger.error(f"Ошибка (gf_deact_confirm): {e}", exc_info=True)
        await query.edit_message_text("❌ Ошибка при деактивации")
        return ConversationHandler.END

    if not result.get("success"):
        await query.edit_message_text(f"❌ {result.get('message', 'Ошибка')}")
        return ConversationHandler.END

    if result.get("already_inactive"):
        esc = html.escape((result.get("title") or "").strip())
        await query.edit_message_text(
            f"ℹ️ {html.escape(result.get('message', ''))}\nНазвание: <b>{esc}</b>",
            parse_mode="HTML",
        )
        return ConversationHandler.END

    esc = html.escape((result.get("title") or "").strip())
    await query.edit_message_text(
        f"✅ Массовая заморозка #{result['global_freeze_id']} деактивирована.\n\n"
        f"• Мигрировано абонементов (monthly): {result.get('migrated', 0)}\n"
        f"• Название: <b>{esc}</b>",
        parse_mode="HTML",
    )
    return ConversationHandler.END


async def handle_gf_deact_abort(update, context):
    query = update.callback_query
    await query.answer("Отменено")
    await query.edit_message_text("↩️ Деактивация отменена.")
    return ConversationHandler.END


async def handle_global_freeze_action_edit(update, context):
    """Редактирование названия активной массовой заморозки."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    try:
        with get_db_session() as session:
            user = UserService.get_user_by_telegram_id(session, user_id)
            if not user or get_user_role(user) not in ["coach", "admin"]:
                await query.edit_message_text("❌ У вас нет прав для этой функции")
                return ConversationHandler.END
            rows = _list_active_global_freezes(session)
    except Exception as e:
        logger.error(f"Ошибка (gf_action_edit): {e}", exc_info=True)
        await query.edit_message_text("❌ Ошибка при загрузке списка")
        return ConversationHandler.END

    if not rows:
        await query.edit_message_text(
            "📭 Нет <b>активных</b> массовых заморозок для редактирования названия.",
            parse_mode="HTML",
        )
        return ConversationHandler.END

    kb = [
        [InlineKeyboardButton(_gf_keyboard_button_label(g), callback_data=f"gf_edit_pick_{g.id}")]
        for g in rows
    ]
    await query.edit_message_text(
        "✏️ <b>Изменить название</b> массовой заморозки\n\n"
        "Выберите запись (даты и абонементы не меняются):",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(kb),
    )
    return GF_ACTION_MENU


async def handle_gf_edit_pick(update, context):
    query = update.callback_query
    await query.answer()
    m = re.fullmatch(r"gf_edit_pick_(\d+)", query.data or "")
    if not m:
        return GF_ACTION_MENU
    gf_id = int(m.group(1))
    user_id = query.from_user.id
    try:
        with get_db_session() as session:
            user = UserService.get_user_by_telegram_id(session, user_id)
            if not user or get_user_role(user) not in ["coach", "admin"]:
                await query.edit_message_text("❌ У вас нет прав для этой функции")
                return ConversationHandler.END
            from database.models import GlobalFreeze

            gf = session.query(GlobalFreeze).filter_by(id=gf_id).first()
    except Exception as e:
        logger.error(f"Ошибка (gf_edit_pick): {e}", exc_info=True)
        await query.edit_message_text("❌ Ошибка")
        return ConversationHandler.END

    if not gf or not gf.is_active:
        await query.edit_message_text("❌ Запись не найдена или уже не активна.")
        return ConversationHandler.END

    context.user_data["gf_edit_id"] = gf_id
    cur = html.escape((gf.title or "").strip() or "без названия")
    await query.edit_message_text(
        f"✏️ Новое название для массовой заморозки ID <code>{gf_id}</code>.\n"
        f"Сейчас: <b>{cur}</b>\n\n"
        "Введите новый текст или /cancel",
        parse_mode="HTML",
    )
    return GF_EDIT_TITLE


async def handle_global_freeze_edit_title_input(update, context):
    gf_id = context.user_data.get("gf_edit_id")
    if not gf_id:
        await update.message.reply_text("❌ Сессия сброшена. Начните снова: 🌍 Массовая заморозка")
        return ConversationHandler.END

    title = (update.message.text or "").strip()
    if not title:
        await update.message.reply_text(
            "Название не может быть пустым. Введите текст или /cancel."
        )
        return GF_EDIT_TITLE

    user_id = update.effective_user.id
    try:
        with get_db_session() as session:
            user = UserService.get_user_by_telegram_id(session, user_id)
            if not user or get_user_role(user) not in ["coach", "admin"]:
                await update.message.reply_text("❌ У вас нет прав для этой функции")
                context.user_data.pop("gf_edit_id", None)
                return ConversationHandler.END
            result = update_global_freeze_title(session, gf_id, title)
    except Exception as e:
        logger.error(f"Ошибка (gf_edit_title): {e}", exc_info=True)
        context.user_data.pop("gf_edit_id", None)
        await update.message.reply_text("❌ Ошибка при сохранении названия")
        return ConversationHandler.END

    context.user_data.pop("gf_edit_id", None)
    if not result.get("success"):
        await update.message.reply_text(f"❌ {result.get('message', 'Ошибка')}")
        return ConversationHandler.END

    await update.message.reply_text(
        f"✅ Название обновлено (ID {result['global_freeze_id']}): {result['title']}"
    )
    return ConversationHandler.END


async def handle_global_freeze_start_date(update, context):
    dt = _parse_ui_date(update.message.text or "")
    if not dt:
        await update.message.reply_text("❌ Неверный формат. Введите дату начала как ДД.ММ.ГГГГ")
        return GF_START_DATE
    context.user_data["gf_start_date"] = dt
    await update.message.reply_text("Введите <b>дату окончания</b> (ДД.ММ.ГГГГ).", parse_mode="HTML")
    return GF_END_DATE


async def handle_global_freeze_end_date(update, context):
    dt = _parse_ui_date(update.message.text or "")
    if not dt:
        await update.message.reply_text("❌ Неверный формат. Введите дату окончания как ДД.ММ.ГГГГ")
        return GF_END_DATE
    start_date = context.user_data.get("gf_start_date")
    if not start_date:
        await update.message.reply_text("❌ Сессия сброшена. Начните снова: 🌍 Массовая заморозка")
        return ConversationHandler.END
    if dt < start_date:
        await update.message.reply_text("❌ Дата окончания не может быть раньше даты начала.")
        return GF_END_DATE
    context.user_data["gf_end_date"] = dt
    await update.message.reply_text(
        "Введите название заморозки (например: Майские праздники).",
        parse_mode="HTML",
    )
    return GF_TITLE


async def handle_global_freeze_title(update, context):
    title = (update.message.text or "").strip()
    if not title:
        await update.message.reply_text(
            "Название заморозки обязательно.\n\n"
            "Введите название заморозки (например: Майские праздники).",
            parse_mode="HTML",
        )
        return GF_TITLE
    context.user_data["gf_title"] = title

    start_date = context.user_data["gf_start_date"]
    end_date = context.user_data["gf_end_date"]
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Применить", callback_data="gf_apply_confirm")],
        [InlineKeyboardButton("❌ Отмена", callback_data="gf_cancel_confirm")],
    ])
    await update.message.reply_text(
        "🌍 <b>ПОДТВЕРЖДЕНИЕ</b>\n\n"
        f"• Название: <b>{title}</b>\n"
        f"• Период: <b>{start_date.strftime('%d.%m.%Y')} — {end_date.strftime('%d.%m.%Y')}</b>\n\n"
        "Применить массовую заморозку?",
        parse_mode="HTML",
        reply_markup=keyboard,
    )
    return GF_CONFIRM


async def handle_global_freeze_confirm_apply(update, context):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    start_date = context.user_data.get("gf_start_date")
    end_date = context.user_data.get("gf_end_date")
    title = context.user_data.get("gf_title", "Массовая заморозка")
    if not start_date or not end_date:
        await query.edit_message_text("❌ Сессия истекла. Начните заново: 🌍 Массовая заморозка")
        return ConversationHandler.END

    try:
        with get_db_session() as session:
            result = apply_global_freeze(
                session=session,
                start_date=start_date,
                end_date=end_date,
                title=title,
                created_by=user_id,
            )
            if not result.get("success"):
                await query.edit_message_text(f"❌ {result.get('message', 'Не удалось применить заморозку')}")
                return ConversationHandler.END
            await query.edit_message_text(
                f"✅ <b>Массовая заморозка применена</b>\n\n"
                f"• ID: {result['global_freeze_id']}\n"
                f"• Период: {result['start_date'].strftime('%d.%m.%Y')} — {result['end_date'].strftime('%d.%m.%Y')}\n"
                f"• Обновлено абонементов: {result['updated_subscriptions']}\n"
                f"• Пропущено: {result['skipped_subscriptions']}\n"
                f"• Суммарно добавлено тренировочных дней: {result.get('total_training_days_added', 0)}\n"
                f"• Название: {title}",
                parse_mode="HTML",
            )
    except Exception as e:
        logger.error(f"Ошибка применения массовой заморозки (UI flow): {e}", exc_info=True)
        await query.edit_message_text("❌ Ошибка при применении массовой заморозки")
    finally:
        context.user_data.pop("gf_start_date", None)
        context.user_data.pop("gf_end_date", None)
        context.user_data.pop("gf_title", None)
    return ConversationHandler.END


async def handle_global_freeze_confirm_cancel(update, context):
    query = update.callback_query
    await query.answer("Отменено")
    await query.edit_message_text("❌ Операция массовой заморозки отменена.")
    context.user_data.pop("gf_start_date", None)
    context.user_data.pop("gf_end_date", None)
    context.user_data.pop("gf_title", None)
    return ConversationHandler.END


async def deactivate_global_freeze(update, context):
    """
    Отключить массовую заморозку досрочно (остановить ограничения через is_active).

    После деактивации запускается миграция для абонементов (monthly), затронутых этой
    массовой заморозкой, чтобы пересобрать Attendance и trainings_remaining.
    """
    user_id = update.effective_user.id
    args = context.args or []

    if len(args) < 1:
        await update.message.reply_text("Формат: /global_freeze_deactivate <global_freeze_id>")
        return

    try:
        gf_id = int(args[0])
    except Exception:
        await update.message.reply_text("global_freeze_id должен быть числом")
        return

    try:
        with get_db_session() as session:
            user = UserService.get_user_by_telegram_id(session, user_id)
            if not user or get_user_role(user) not in ["coach", "admin"]:
                await update.message.reply_text("❌ У вас нет прав для этой команды")
                return

            result = deactivate_global_freeze_and_migrate(session, gf_id)

            if not result.get("success"):
                await update.message.reply_text(f"❌ {result.get('message', 'Ошибка')}")
                return

            if result.get("already_inactive"):
                await update.message.reply_text(
                    f"ℹ️ {result.get('message', 'Уже не активна')}\n"
                    f"• Название: {result.get('title', '')}"
                )
                return

            await update.message.reply_text(
                f"✅ Массовая заморозка #{gf_id} деактивирована.\n"
                f"• Мигрировано абонементов (monthly): {result.get('migrated', 0)}\n"
                f"• Название: {result.get('title', '')}"
            )
    except Exception as e:
        logger.error(f"Ошибка деактивации массовой заморозки: {e}", exc_info=True)
        await update.message.reply_text("❌ Ошибка при деактивации массовой заморозки")


def register_all_handlers(registrar: HandlerRegistrar) -> None:
    """
    Зарегистрировать все обработчики бота.
    
    Args:
        registrar: Регистратор обработчиков
    """
    logger.info("🔧 Начинаем регистрацию обработчиков...")
    
    # Сначала регистрируем обычные обработчики кнопок меню (они должны иметь приоритет)
    # Обработчики для кнопок меню
    logger.info("📝 Регистрируем обработчики кнопок меню...")
    registrar.register(
        MessageHandler(filters.Regex("^(📋 Список спортсменов)$"), athletes_list)
    )
    logger.info("✅ Зарегистрирован обработчик: 📋 Список спортсменов")
    registrar.register(
        MessageHandler(filters.Regex("^(🏋️ Начать тренировку)$"), start_training)
    )
    logger.info("✅ Зарегистрирован обработчик: 🏋️ Начать тренировку")
    registrar.register(
        MessageHandler(filters.Regex("^(📅 Мой календарь)$"), show_coach_calendar)
    )
    logger.info("✅ Зарегистрирован обработчик: 📅 Мой календарь")

    # ConversationHandler для добавления спортсмена (регистрируем после обычных обработчиков)
    logger.info("📝 Регистрируем ConversationHandler для добавления спортсмена...")
    conv_handler = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex("^(👥 Добавить спортсмена)$"), add_athlete_start)
        ],
        states={
            ATHLETE_FULL_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_athlete_full_name)
            ],
            ATHLETE_PHONE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_athlete_phone)
            ],
            ATHLETE_BIRTH_DATE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_athlete_birth_date)
            ],
            ATHLETE_MEDICAL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_athlete_medical)
            ],
            ATHLETE_AGE_GROUP: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_athlete_age_group)
            ],
            ATHLETE_SUBSCRIPTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_athlete_subscription)
            ],
            ATHLETE_TRAINING_DATE: [
                CallbackQueryHandler(handle_training_date_selection, pattern="^select_training_date_"),
                CallbackQueryHandler(handle_add_athlete_calendar_nav, pattern="^addath_cal_"),
                CallbackQueryHandler(handle_add_athlete_calendar_date_pick, pattern="^addath_date_"),
                CallbackQueryHandler(handle_add_athlete_calendar_ignore, pattern="^addath_ignore$"),
                CallbackQueryHandler(
                    handle_add_athlete_shift_confirm,
                    pattern=r"^addath_shift_confirm(?:_\d{12})?$",
                ),
                CallbackQueryHandler(handle_add_athlete_shift_cancel, pattern="^addath_shift_cancel$"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_athlete_creation),
            # Добавляем кнопки меню в fallbacks, чтобы они могли прерывать разговор
            MessageHandler(filters.Regex("^(📋 Список спортсменов)$"), athletes_list),
            MessageHandler(filters.Regex("^(🏋️ Начать тренировку)$"), start_training),
            MessageHandler(filters.Regex("^(📅 Мой календарь)$"), show_coach_calendar),
            MessageHandler(filters.Regex("^(👥 Добавить спортсмена)$"), add_athlete_start),
        ],
        name="add_athlete_conversation",
        persistent=False,
        allow_reentry=True
    )
    registrar.register(conv_handler)
    logger.info("✅ Зарегистрирован ConversationHandler для добавления спортсмена")

    # ConversationHandler для массовой заморозки (по датам)
    gf_conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex("^(🌍 Массовая заморозка)$"), start_global_freeze_flow)
        ],
        states={
            GF_ACTION_MENU: [
                CallbackQueryHandler(handle_gf_deact_confirm, pattern=r"^gf_deact_confirm_\d+$"),
                CallbackQueryHandler(handle_gf_deact_pick, pattern=r"^gf_deact_pick_\d+$"),
                CallbackQueryHandler(handle_gf_deact_abort, pattern=r"^gf_deact_abort$"),
                CallbackQueryHandler(handle_gf_edit_pick, pattern=r"^gf_edit_pick_\d+$"),
                CallbackQueryHandler(handle_global_freeze_action_create, pattern="^gf_action_create$"),
                CallbackQueryHandler(handle_global_freeze_action_cancel, pattern="^gf_action_cancel$"),
                CallbackQueryHandler(handle_global_freeze_action_edit, pattern="^gf_action_edit$"),
            ],
            GF_START_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_global_freeze_start_date)],
            GF_END_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_global_freeze_end_date)],
            GF_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_global_freeze_title)],
            GF_EDIT_TITLE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_global_freeze_edit_title_input)
            ],
            GF_CONFIRM: [
                CallbackQueryHandler(handle_global_freeze_confirm_apply, pattern="^gf_apply_confirm$"),
                CallbackQueryHandler(handle_global_freeze_confirm_cancel, pattern="^gf_cancel_confirm$"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_global_freeze),
            MessageHandler(filters.Regex("^(📋 Список спортсменов)$"), athletes_list),
            MessageHandler(filters.Regex("^(🏋️ Начать тренировку)$"), start_training),
            MessageHandler(filters.Regex("^(📅 Мой календарь)$"), show_coach_calendar),
            MessageHandler(filters.Regex("^(👥 Добавить спортсмена)$"), add_athlete_start),
        ],
        name="global_freeze_conversation",
        persistent=False,
        allow_reentry=True,
    )
    registrar.register(gf_conv)
    logger.info("✅ Зарегистрирован ConversationHandler для массовой заморозки")

    # Обработчики для списка спортсменов
    registrar.register(
        CallbackQueryHandler(handle_back_to_menu_main, pattern="^back_to_menu_main$")
    )
    registrar.register(
        CallbackQueryHandler(handle_show_more_info, pattern="^show_more_info$")
    )
    registrar.register(
        CallbackQueryHandler(athletes_categories, pattern="^athletes_categories$")
    )
    registrar.register(
        CallbackQueryHandler(athletes_list_filtered, pattern="^athletes_(all|active|inactive|active_children|active_adults|inactive_children|inactive_adults|children|adults)$")
    )

    # Обработчики для карточек
    registrar.register(
        CallbackQueryHandler(show_athlete_card, pattern="^athlete_")
    )
    registrar.register(
        CallbackQueryHandler(show_subscription_card, pattern="^subscription_")
    )
    registrar.register(
        CallbackQueryHandler(handle_back_to_list, pattern="^back_to_list$")
    )
    registrar.register(
        CallbackQueryHandler(handle_back_to_menu, pattern="^back_to_menu")
    )
    
    # Обработчики для функций карточки спортсмена
    registrar.register(
        CallbackQueryHandler(show_athlete_visits, pattern="^visits_")
    )
    registrar.register(
        CallbackQueryHandler(show_athlete_stats, pattern="^stats_")
    )
    registrar.register(
        CallbackQueryHandler(execute_restore_training, pattern="^restore_att_")
    )
    registrar.register(
        CallbackQueryHandler(show_restore_menu, pattern="^restore_")
    )
    registrar.register(
        CallbackQueryHandler(show_edit_athlete_menu, pattern="^edit_")
    )
    registrar.register(
        CallbackQueryHandler(select_subscription, pattern="^select_sub_")
    )
    registrar.register(
        CallbackQueryHandler(view_subscription_card, pattern="^view_sub_card_")
    )

    # Обработчики для отметки посещения
    registrar.register(
        CallbackQueryHandler(mark_attendance_start, pattern="^mark_attendance_")
    )
    registrar.register(
        CallbackQueryHandler(handle_attendance_training_list, pattern="^attendance_training_list$")
    )
    registrar.register(
        CallbackQueryHandler(select_training_for_attendance, pattern="^select_mark_training_")
    )
    # Сначала более специфичный паттерн: дата первой тренировки при добавлении спортсмена
    registrar.register(
        CallbackQueryHandler(handle_training_date_selection, pattern="^select_training_date_")
    )
    registrar.register(
        CallbackQueryHandler(handle_training_selection, pattern="^select_training_")
    )
    registrar.register(
        CallbackQueryHandler(execute_mark_attendance, pattern="^(mark_present|mark_absent)$")
    )

    # Команды быстрого доступа
    registrar.register(CommandHandler("card", show_athlete_card))
    registrar.register(CommandHandler("sub", show_subscription_card))

    # Обработчик команды /start
    registrar.register(CommandHandler("start", start))

    # Обработчик команды /menu
    registrar.register(CommandHandler("menu", coach_menu))

    # Команда для проверки абонементов
    registrar.register(CommandHandler("check_subs", check_all_subscriptions))
    # Ручной read-only аудит абонементов
    registrar.register(CommandHandler("audit_now", audit_subscriptions_now))
    # Команда массовой заморозки
    registrar.register(CommandHandler("global_freeze", create_global_freeze))
    # Деактивация массовой заморозки
    registrar.register(CommandHandler("global_freeze_deactivate", deactivate_global_freeze))

    # Остальные обработчики для кнопок меню (не дублируем уже зарегистрированные)
    registrar.register(
        MessageHandler(filters.Regex("^(📊 Статистика посещений)$"), handle_statistics)
    )
    registrar.register(
        MessageHandler(filters.Regex("^(💰 Финансовая статистика)$"), handle_financial_stats)
    )
    registrar.register(
        MessageHandler(filters.Regex("^(📅 Отметить посещение)$"), handle_attendance)
    )
    registrar.register(
        MessageHandler(filters.Regex("^(⚙️ Настройки)$"), handle_settings)
    )

    # Обработчик навигации по календарю
    registrar.register(
        CallbackQueryHandler(handle_calendar_navigation, pattern="^calendar_")
    )
    # Обработчик клика по дате в календаре
    registrar.register(
        CallbackQueryHandler(handle_calendar_date_click, pattern="^cal_date_")
    )
    # Обработчик клика по пустой кнопке календаря
    registrar.register(
        CallbackQueryHandler(handle_calendar_empty_click, pattern="^cal_empty$")
    )

    # Обработчики для спортсменов
    registrar.register(
        MessageHandler(filters.Regex("^(👤 Моя карточка)$"), show_my_athlete_card)
    )
    registrar.register(
        MessageHandler(filters.Regex("^(🎫 Мой абонемент)$"), show_my_subscription)
    )
    registrar.register(
        CallbackQueryHandler(show_my_subscription, pattern="^athlete_subscription_refresh$")
    )
    registrar.register(
        CallbackQueryHandler(handle_athlete_back_to_menu, pattern="^athlete_back_to_menu$")
    )

    # Обработчики истории абонементов
    registrar.register(
        CallbackQueryHandler(show_subscription_history, pattern="^subscription_history_")
    )
    registrar.register(
        CallbackQueryHandler(view_subscription_from_history, pattern="^view_sub_")
    )
    
    # Обработчик активации абонементов (обрабатывает activate_sub_*, activate_sub_new_*, activate_sub_type_*)
    registrar.register(
        CallbackQueryHandler(handle_activate_subscription, pattern="^activate_sub_")
    )
    # Календарь выбора даты активации (после выбора типа)
    registrar.register(CallbackQueryHandler(handle_activation_calendar_nav, pattern="^act_cal_"))
    registrar.register(CallbackQueryHandler(handle_activation_date_pick, pattern="^act_date_"))
    registrar.register(CallbackQueryHandler(handle_activation_ignore, pattern="^act_ignore$"))
    registrar.register(
        CallbackQueryHandler(handle_activation_shift_confirm, pattern=r"^act_shift_confirm_\d+_\d{12}$")
    )
    registrar.register(
        CallbackQueryHandler(handle_activation_shift_cancel, pattern=r"^act_shift_cancel_\d+$")
    )

    # Обработчики для заморозки абонемента
    registrar.register(
        CallbackQueryHandler(handle_freeze_subscription_start, pattern="^freeze_sub_")
    )
    registrar.register(
        CallbackQueryHandler(handle_freeze_calendar_nav, pattern="^freeze_cal_")
    )
    registrar.register(
        CallbackQueryHandler(handle_freeze_date_pick, pattern="^freeze_date_")
    )
    registrar.register(
        CallbackQueryHandler(handle_freeze_ignore, pattern="^freeze_ignore$")
    )
    registrar.register(
        CallbackQueryHandler(handle_unfreeze_subscription, pattern="^unfreeze_sub_")
    )

    logger.info("✅ Все обработчики зарегистрированы")

