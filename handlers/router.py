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
    list_active_global_freezes_overlapping_range,
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
    handle_add_athlete_individual_time_pick,
    handle_add_athlete_calendar_ignore,
    handle_add_athlete_shift_confirm,
    handle_add_athlete_shift_cancel,
    handle_training_date_selection,
    athletes_list,
    athletes_list_filtered,
    athletes_list_page,
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
    handle_activation_individual_shift_confirm,
    handle_activation_time_pick,
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
    handle_attendance_athletes_page,
    handle_attendance_page_info,
    handle_attendance_name_column,
    handle_attendance_slot_locked,
    mark_attendance_start,
    handle_training_selection,
    execute_mark_attendance,
    execute_mark_attendance_slot,
)
from handlers.coach_report_handlers import coach_report_entry, coach_statistics_callback

logger = logging.getLogger(__name__)

_GF_NOTICE_MAIN_HTML = (
    "ℹ️ Вводите данные <b>внимательно</b> и убедитесь в <b>правильности решения</b> "
    "— действие затрагивает активные абонементы."
)

_GF_NOTICE_CREATE_HTML = (
    ""
)

# Состояния диалога массовой заморозки
GF_ACTION_MENU, GF_START_DATE, GF_END_DATE, GF_TITLE, GF_CONFIRM = range(5)


# Заглушки для обработчиков, которые еще не реализованы
async def handle_statistics(update, context):
    """Обработчик для статистики посещений (в разработке)."""
    await update.message.reply_text("📊 Функция в разработке")


async def handle_financial_stats(update, context):
    """Обработчик для финансовой статистики (в разработке)."""
    await update.message.reply_text("💰 Функция в разработке")


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


def _format_global_freeze_history_html(session, limit: int = 15) -> str:
    """Короткая история массовых заморозок (активные и неактивные)."""
    from database.models import GlobalFreeze

    rows = (
        session.query(GlobalFreeze)
        .order_by(GlobalFreeze.id.desc())
        .limit(limit)
        .all()
    )
    if not rows:
        return "📭 <b>История массовых заморозок пуста.</b>"

    lines = [f"📚 <b>История массовых заморозок</b> (последние {len(rows)}):"]
    for g in rows:
        status = "🟢 Действует" if g.is_active else "⚪ Отключена"
        title = html.escape((g.title or "").strip() or "без названия")
        ds = g.start_date.strftime("%d.%m.%Y")
        de = g.end_date.strftime("%d.%m.%Y")
        created = g.created_at.strftime("%d.%m.%Y") if g.created_at else "—"
        initiator = str(g.created_by) if g.created_by else "не указан"
        lines.append(f"• <b>{title}</b>")
        lines.append(f"  Создано: {created}")
        lines.append(f"  Период действия: {ds}—{de}")
        lines.append(f"  Текущий статус: {status}")
        lines.append(f"  Инициатор: {initiator}")
    return "\n".join(lines)


def _gf_keyboard_button_label(g) -> str:
    t = (g.title or "").strip() or "без названия"
    if len(t) > 28:
        t = t[:25] + "…"
    return f"#{g.id} {t}"


def _gf_keyboard_button_label_from_parts(gf_id: int, title: str) -> str:
    """Безопасный label: работает с примитивами, не зависит от ORM-сессии."""
    t = (title or "").strip() or "без названия"
    if len(t) > 28:
        t = t[:25] + "…"
    return f"#{gf_id} {t}"


async def _gf_safe_edit(
    update,
    context,
    text: str,
    *,
    parse_mode=None,
    reply_markup=None,
) -> None:
    """Редактирование сообщения с inline-кнопкой; при ошибке API — новое сообщение в чат."""
    query = update.callback_query
    if not query:
        logger.error("GF UI: _gf_safe_edit вызван без callback_query")
        return
    kwargs = {}
    if parse_mode is not None:
        kwargs["parse_mode"] = parse_mode
    if reply_markup is not None:
        kwargs["reply_markup"] = reply_markup
    try:
        await query.edit_message_text(text, **kwargs)
    except Exception as exc:
        logger.warning(
            "GF UI: edit_message_text не удался (%s), дублирую ответом в чат",
            exc,
            exc_info=True,
        )
        chat_id = None
        if query.message:
            chat_id = query.message.chat_id
        elif update.effective_chat:
            chat_id = update.effective_chat.id
        else:
            chat_id = query.from_user.id if query.from_user else None
        if chat_id is None:
            logger.error("GF UI: не удалось определить chat_id для запасного сообщения")
            return
        try:
            await context.bot.send_message(chat_id=chat_id, text=text, **kwargs)
        except Exception as send_exc:
            logger.error("GF UI: send_message тоже не удался: %s", send_exc, exc_info=True)


async def handle_global_freeze_flow_cancel(update, context):
    """Явная отмена потока массовой заморозки через inline-кнопку."""
    query = update.callback_query
    await query.answer("Отменено")
    context.user_data.pop("gf_start_date", None)
    context.user_data.pop("gf_end_date", None)
    context.user_data.pop("gf_title", None)
    await _gf_safe_edit(update, context, "✅ Операция прервана.")
    return ConversationHandler.END


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
    main_status_block = "" if status_block.startswith("📭 ") else f"{status_block}\n\n"

    await update.message.reply_text(
        "🌍 <b>МАССОВАЯ ЗАМОРОЗКА</b>\n\n"
        f"{main_status_block}"
        "Выберите действие:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Создать новую", callback_data="gf_action_create")],
            [InlineKeyboardButton("❌ Отмена массовой заморозки", callback_data="gf_action_cancel")],
            [InlineKeyboardButton("📚 История массовых заморозок", callback_data="gf_action_history")],
        ]),
    )
    return GF_ACTION_MENU


async def handle_global_freeze_action_history(update, context):
    """Показать историю массовых заморозок из меню GF."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    try:
        with get_db_session() as session:
            user = UserService.get_user_by_telegram_id(session, user_id)
            if not user or get_user_role(user) not in ["coach", "admin"]:
                await _gf_safe_edit(update, context, "❌ У вас нет прав для этой функции")
                return ConversationHandler.END
            text = _format_global_freeze_history_html(session)
    except Exception as e:
        logger.error(f"Ошибка (gf_action_history): {e}", exc_info=True)
        await _gf_safe_edit(update, context, "❌ Ошибка при формировании истории")
        return ConversationHandler.END

    await _gf_safe_edit(update, context, text, parse_mode="HTML")
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
                await _gf_safe_edit(update, context, "❌ У вас нет прав для этой функции")
                return ConversationHandler.END
            status_block = _format_current_global_freezes_html(session)
    except Exception as e:
        logger.error(f"Ошибка проверки прав (gf_action_create): {e}", exc_info=True)
        await _gf_safe_edit(update, context, "❌ Ошибка проверки прав")
        return ConversationHandler.END

    context.user_data.pop("gf_start_date", None)
    context.user_data.pop("gf_end_date", None)
    context.user_data.pop("gf_title", None)

    await _gf_safe_edit(
        update,
        context,
        f"{status_block}\n\n"
        f"{_GF_NOTICE_MAIN_HTML}\n"
        f"{_GF_NOTICE_CREATE_HTML}\n\n"
        "<b>Введите дату начала</b> (ДД.ММ.ГГГГ):",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("❌ Отменить операцию", callback_data="gf_cancel_flow")]]
        ),
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
                await _gf_safe_edit(update, context, "❌ У вас нет прав для этой функции")
                return ConversationHandler.END
            rows = _list_active_global_freezes(session)
            # Важно: после выхода из `with` ORM-объекты могут стать detached.
            # Поэтому извлекаем id/title заранее.
            rows_safe = [(g.id, (g.title or "").strip() or "без названия") for g in rows]
    except Exception as e:
        logger.error(f"Ошибка (gf_action_cancel): {e}", exc_info=True)
        await _gf_safe_edit(update, context, "❌ Ошибка при загрузке списка")
        return ConversationHandler.END

    if not rows_safe:
        await _gf_safe_edit(
            update,
            context,
            "📭 Нет <b>активных</b> массовых заморозок для деактивации.",
            parse_mode="HTML",
        )
        return ConversationHandler.END

    kb = [
        [
            InlineKeyboardButton(
                _gf_keyboard_button_label_from_parts(gf_id, title),
                callback_data=f"gf_deact_pick_{gf_id}",
            )
        ]
        for gf_id, title in rows_safe
    ]
    await _gf_safe_edit(
        update,
        context,
        "❌ <b>Снять действие</b> заморозки\n\n"
        "Выберите запись (будет пересчёт <b>месячных</b> абонементов).",
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
    gf_title = None
    gf_is_active = False
    gf_start_str = None
    gf_end_str = None
    try:
        with get_db_session() as session:
            user = UserService.get_user_by_telegram_id(session, user_id)
            if not user or get_user_role(user) not in ["coach", "admin"]:
                await _gf_safe_edit(update, context, "❌ У вас нет прав для этой функции")
                return ConversationHandler.END
            from database.models import GlobalFreeze

            gf = session.query(GlobalFreeze).filter_by(id=gf_id).first()
            if gf:
                gf_is_active = bool(gf.is_active)
                gf_title = (gf.title or "").strip() or "без названия"
                gf_start_str = gf.start_date.strftime("%d.%m.%Y") if gf.start_date else ""
                gf_end_str = gf.end_date.strftime("%d.%m.%Y") if gf.end_date else ""
    except Exception as e:
        logger.error(f"Ошибка (gf_deact_pick): {e}", exc_info=True)
        await _gf_safe_edit(update, context, "❌ Ошибка")
        return ConversationHandler.END

    if not gf_is_active:
        await _gf_safe_edit(update, context, "❌ Запись не найдена или уже не активна.")
        return ConversationHandler.END

    title = html.escape(gf_title or "без названия")
    ds = gf_start_str or ""
    de = gf_end_str or ""
    await _gf_safe_edit(
        update,
        context,
        "⚠️ <b>Подтверждение деактивации</b>\n\n"
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
                await _gf_safe_edit(update, context, "❌ У вас нет прав для этой функции")
                return ConversationHandler.END
            result = deactivate_global_freeze_and_migrate(session, gf_id)
    except Exception as e:
        logger.error(f"Ошибка (gf_deact_confirm): {e}", exc_info=True)
        await _gf_safe_edit(update, context, "❌ Ошибка при деактивации")
        return ConversationHandler.END

    if not result.get("success"):
        await _gf_safe_edit(update, context, f"❌ {result.get('message', 'Ошибка')}")
        return ConversationHandler.END

    if result.get("already_inactive"):
        esc = html.escape((result.get("title") or "").strip())
        await _gf_safe_edit(
            update,
            context,
            f"ℹ️ {html.escape(result.get('message', ''))}\nНазвание: <b>{esc}</b>",
            parse_mode="HTML",
        )
        return ConversationHandler.END

    audit_summary = ""
    try:
        with get_db_session() as session:
            report = run_subscription_audit(session)
        audit_summary = (
            f"\n• Audit: critical={report.get('severity_counts', {}).get('critical', 0)}, "
            f"warning={report.get('severity_counts', {}).get('warning', 0)}"
        )
    except Exception as audit_exc:
        logger.error("Ошибка post-deactivate (UI flow) аудита GF: %s", audit_exc, exc_info=True)

    esc = html.escape((result.get("title") or "").strip())
    await _gf_safe_edit(
        update,
        context,
        f"✅ Массовая заморозка деактивирована.\n\n"
        f"• Проверено абонементов: {result.get('checked', 0)}\n"
        f"• Обновлено абонементов: {result.get('updated_subscriptions', result.get('migrated', 0))}\n"
        f"• Без изменений: {result.get('skipped_subscriptions', 0)}\n"
        f"• Название: <b>{esc}</b>"
        f"{audit_summary}",
        parse_mode="HTML",
    )
    return ConversationHandler.END


async def handle_gf_deact_abort(update, context):
    query = update.callback_query
    await query.answer("Отменено")
    await _gf_safe_edit(update, context, "↩️ Деактивация отменена.")
    return ConversationHandler.END


async def handle_global_freeze_start_date(update, context):
    dt = _parse_ui_date(update.message.text or "")
    if not dt:
        await update.message.reply_text("❌ Неверный формат. Введите дату начала как ДД.ММ.ГГГГ")
        return GF_START_DATE
    context.user_data["gf_start_date"] = dt
    await update.message.reply_text(
        f"Начало: <b>{dt.strftime('%d.%m.%Y')}</b>\n"
        "<b>Дата окончания</b> (ДД.ММ.ГГГГ), не раньше начала:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("❌ Отменить операцию", callback_data="gf_cancel_flow")]]
        ),
    )
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

    sd0 = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
    ed0 = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    span_days = (ed0 - sd0).days + 1
    span_hint = ""
    if span_days > 21:
        span_hint = f"\n⚠️ Долго ({span_days} дн.) — проверьте даты."

    await update.message.reply_text(
        f"Конец: <b>{dt.strftime('%d.%m.%Y')}</b>{span_hint}\n"
        "<b>Название</b> заморозки:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("❌ Отменить операцию", callback_data="gf_cancel_flow")]]
        ),
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
    esc_title = html.escape(title)

    overlap_note = ""
    try:
        with get_db_session() as session:
            overlapping = list_active_global_freezes_overlapping_range(session, start_date, end_date)
            if overlapping:
                oids = ", ".join(str(g.id) for g in overlapping[:5])
                if len(overlapping) > 5:
                    oids += ", …"
                overlap_note = (
                    f"\n\n⚠️ Период пересекается с уже действующей массовой заморозкой "
                    f"(записи: <code>{html.escape(oids)}</code>). "
                    "Выберите другой период."
                )
    except Exception as e:
        logger.error(f"Ошибка проверки пересечения GF перед подтверждением: {e}", exc_info=True)
        overlap_note = "\n\n⚠️ Проверка пересечений не удалась — лучше отложить применение."

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Применить", callback_data="gf_apply_confirm")],
        [InlineKeyboardButton("❌ Отмена", callback_data="gf_cancel_confirm")],
    ])
    await update.message.reply_text(
        "🌍 <b>Подтвердите</b>\n\n"
        f"{esc_title}\n"
        f"<b>{start_date.strftime('%d.%m.%Y')} — {end_date.strftime('%d.%m.%Y')}</b>\n\n"
        "Убедитесь, что всё верно."
        f"{overlap_note}\n\n"
        "Применить?",
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
        await _gf_safe_edit(
            update, context, "❌ Сессия истекла. Начните заново: 🌍 Массовая заморозка"
        )
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
                await _gf_safe_edit(
                    update, context, f"❌ {result.get('message', 'Не удалось применить заморозку')}"
                )
                return ConversationHandler.END
            audit_summary = ""
            try:
                report = run_subscription_audit(session)
                audit_summary = (
                    f"\n• Audit: critical={report.get('severity_counts', {}).get('critical', 0)}, "
                    f"warning={report.get('severity_counts', {}).get('warning', 0)}"
                )
            except Exception as audit_exc:
                logger.error("Ошибка post-apply аудита GF: %s", audit_exc, exc_info=True)
            esc_ok_title = html.escape((title or "").strip())
            await _gf_safe_edit(
                update,
                context,
                f"✅ <b>Массовая заморозка применена</b>\n\n"
                f"• ID: {result['global_freeze_id']}\n"
                f"• Период: {result['start_date'].strftime('%d.%m.%Y')} — {result['end_date'].strftime('%d.%m.%Y')}\n"
                f"• Обновлено абонементов: {result['updated_subscriptions']}\n"
                f"• Пропущено: {result['skipped_subscriptions']}\n"
                f"• Суммарно добавлено тренировочных дней: {result.get('total_training_days_added', 0)}\n"
                f"• Название: <b>{esc_ok_title}</b>"
                f"{audit_summary}",
                parse_mode="HTML",
            )
    except Exception as e:
        logger.error(f"Ошибка применения массовой заморозки (UI flow): {e}", exc_info=True)
        await _gf_safe_edit(update, context, "❌ Ошибка при применении массовой заморозки")
    finally:
        context.user_data.pop("gf_start_date", None)
        context.user_data.pop("gf_end_date", None)
        context.user_data.pop("gf_title", None)
    return ConversationHandler.END


async def handle_global_freeze_confirm_cancel(update, context):
    query = update.callback_query
    await query.answer("Отменено")
    await _gf_safe_edit(update, context, "❌ Операция массовой заморозки отменена.")
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

            audit_summary = ""
            try:
                report = run_subscription_audit(session)
                audit_summary = (
                    f"\n• Audit: critical={report.get('severity_counts', {}).get('critical', 0)}, "
                    f"warning={report.get('severity_counts', {}).get('warning', 0)}"
                )
            except Exception as audit_exc:
                logger.error("Ошибка post-deactivate аудита GF: %s", audit_exc, exc_info=True)

            await update.message.reply_text(
                f"✅ Массовая заморозка деактивирована.\n"
                f"• Проверено абонементов: {result.get('checked', 0)}\n"
                f"• Обновлено абонементов: {result.get('updated_subscriptions', result.get('migrated', 0))}\n"
                f"• Без изменений: {result.get('skipped_subscriptions', 0)}\n"
                f"• Название: {result.get('title', '')}"
                f"{audit_summary}"
            )
    except Exception as e:
        logger.error(f"Ошибка деактивации массовой заморозки: {e}", exc_info=True)
        await update.message.reply_text("❌ Ошибка при деактивации массовой заморозки")


async def global_freeze_history(update, context):
    """Показать историю массовых заморозок."""
    user_id = update.effective_user.id
    try:
        with get_db_session() as session:
            user = UserService.get_user_by_telegram_id(session, user_id)
            if not user or get_user_role(user) not in ["coach", "admin"]:
                await update.message.reply_text("❌ У вас нет прав для этой команды")
                return
            text = _format_global_freeze_history_html(session)
            await update.message.reply_text(text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Ошибка команды /global_freeze_history: {e}", exc_info=True)
        await update.message.reply_text("❌ Ошибка при формировании истории массовых заморозок")


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
        MessageHandler(
            filters.Regex(
                "^(📅 Отметить посещение|📅 Отметить посещения|📝 Отметить посещения)$"
            ),
            start_training,
        )
    )
    logger.info("✅ Зарегистрирован обработчик: 📝 Отметить посещения")
    registrar.register(
        MessageHandler(filters.Regex("^(📅 Мой календарь)$"), show_coach_calendar)
    )
    logger.info("✅ Зарегистрирован обработчик: 📅 Мой календарь")
    registrar.register(
        MessageHandler(filters.Regex("^(📊 Статистика)$"), coach_report_entry)
    )
    logger.info("✅ Зарегистрирован обработчик: 📊 Статистика")

    # Массовая заморозка — РАНЬШЕ диалога добавления спортсмена: иначе при «залипшем» состоянии
    # add_athlete команда /cancel обрабатывается первым зарегистрированным CH и показывает текст про спортсмена.
    logger.info("📝 Регистрируем ConversationHandler для массовой заморозки...")
    gf_conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex("^(🌍 Массовая заморозка)$"), start_global_freeze_flow)
        ],
        states={
            GF_ACTION_MENU: [
                CallbackQueryHandler(handle_global_freeze_flow_cancel, pattern=r"^gf_cancel_flow$"),
                CallbackQueryHandler(handle_gf_deact_confirm, pattern=r"^gf_deact_confirm_\d+$"),
                CallbackQueryHandler(handle_gf_deact_pick, pattern=r"^gf_deact_pick_\d+$"),
                CallbackQueryHandler(handle_gf_deact_abort, pattern=r"^gf_deact_abort$"),
                CallbackQueryHandler(handle_global_freeze_action_create, pattern="^gf_action_create$"),
                CallbackQueryHandler(handle_global_freeze_action_cancel, pattern="^gf_action_cancel$"),
                CallbackQueryHandler(handle_global_freeze_action_history, pattern="^gf_action_history$"),
            ],
            GF_START_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_global_freeze_start_date)],
            GF_END_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_global_freeze_end_date)],
            GF_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_global_freeze_title)],
            GF_CONFIRM: [
                CallbackQueryHandler(handle_global_freeze_confirm_apply, pattern="^gf_apply_confirm$"),
                CallbackQueryHandler(handle_global_freeze_confirm_cancel, pattern="^gf_cancel_confirm$"),
            ],
        },
        fallbacks=[
            # Inline-кнопки GF из любого состояния (иначе при вводе дат callback с меню «отмена» теряется)
            CallbackQueryHandler(handle_global_freeze_flow_cancel, pattern=r"^gf_cancel_flow$"),
            CallbackQueryHandler(handle_gf_deact_confirm, pattern=r"^gf_deact_confirm_\d+$"),
            CallbackQueryHandler(handle_gf_deact_pick, pattern=r"^gf_deact_pick_\d+$"),
            CallbackQueryHandler(handle_gf_deact_abort, pattern=r"^gf_deact_abort$"),
            CallbackQueryHandler(handle_global_freeze_confirm_apply, pattern="^gf_apply_confirm$"),
            CallbackQueryHandler(handle_global_freeze_confirm_cancel, pattern="^gf_cancel_confirm$"),
            CallbackQueryHandler(handle_global_freeze_action_create, pattern="^gf_action_create$"),
            CallbackQueryHandler(handle_global_freeze_action_cancel, pattern="^gf_action_cancel$"),
            CallbackQueryHandler(handle_global_freeze_action_history, pattern="^gf_action_history$"),
            CommandHandler("cancel", cancel_global_freeze),
            MessageHandler(filters.Regex("^(📋 Список спортсменов)$"), athletes_list),
            MessageHandler(
                filters.Regex(
                    "^(📅 Отметить посещение|📅 Отметить посещения|📝 Отметить посещения)$"
                ),
                start_training,
            ),
            MessageHandler(filters.Regex("^(📅 Мой календарь)$"), show_coach_calendar),
            MessageHandler(filters.Regex("^(📊 Статистика)$"), coach_report_entry),
            MessageHandler(filters.Regex("^(👥 Добавить спортсмена)$"), add_athlete_start),
        ],
        name="global_freeze_conversation",
        persistent=False,
        allow_reentry=True,
    )
    registrar.register(gf_conv)
    # Если диалог GF не в памяти (перезапуск, клик по старому сообщению), ConversationHandler
    # не забирает callback — без этих обработчиков кнопки «отмена» / деактивация «молчат».
    # Регистрируются после CH: срабатывают только когда check_update у CH вернул None.
    for _cb, _pat in (
        (handle_global_freeze_flow_cancel, r"^gf_cancel_flow$"),
        (handle_gf_deact_confirm, r"^gf_deact_confirm_\d+$"),
        (handle_gf_deact_pick, r"^gf_deact_pick_\d+$"),
        (handle_gf_deact_abort, r"^gf_deact_abort$"),
        (handle_global_freeze_confirm_apply, r"^gf_apply_confirm$"),
        (handle_global_freeze_confirm_cancel, r"^gf_cancel_confirm$"),
        (handle_global_freeze_action_create, r"^gf_action_create$"),
        (handle_global_freeze_action_cancel, r"^gf_action_cancel$"),
        (handle_global_freeze_action_history, r"^gf_action_history$"),
    ):
        registrar.register(CallbackQueryHandler(_cb, pattern=_pat))
    logger.info("✅ Зарегистрирован ConversationHandler для массовой заморозки (+ резервные callback)")

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
                CallbackQueryHandler(
                    handle_add_athlete_individual_time_pick,
                    pattern=r"^addath_it_\d{12}$",
                ),
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
            MessageHandler(filters.Regex("^(📋 Список спортсменов)$"), athletes_list),
            MessageHandler(
                filters.Regex(
                    "^(📅 Отметить посещение|📅 Отметить посещения|📝 Отметить посещения)$"
                ),
                start_training,
            ),
            MessageHandler(filters.Regex("^(📅 Мой календарь)$"), show_coach_calendar),
            MessageHandler(filters.Regex("^(📊 Статистика)$"), coach_report_entry),
            MessageHandler(filters.Regex("^(👥 Добавить спортсмена)$"), add_athlete_start),
        ],
        name="add_athlete_conversation",
        persistent=False,
        allow_reentry=True
    )
    registrar.register(conv_handler)
    logger.info("✅ Зарегистрирован ConversationHandler для добавления спортсмена")

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
    registrar.register(
        CallbackQueryHandler(athletes_list_page, pattern=r"^alpg_[a-z]{2}_\d+$")
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
        CallbackQueryHandler(handle_attendance_slot_locked, pattern="^attendance_slot_locked$")
    )
    registrar.register(
        CallbackQueryHandler(handle_attendance_athletes_page, pattern=r"^attpg_\d+_\d+$")
    )
    registrar.register(
        CallbackQueryHandler(handle_attendance_page_info, pattern="^attpg_info$")
    )
    registrar.register(
        CallbackQueryHandler(handle_attendance_name_column, pattern=r"^attnm_\d+_\d+$")
    )
    registrar.register(
        CallbackQueryHandler(
            execute_mark_attendance_slot,
            pattern=r"^atmark_\d+_\d+_[01]$",
        )
    )
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
    registrar.register(
        CallbackQueryHandler(coach_statistics_callback, pattern=r"^cst")
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
    registrar.register(CommandHandler("global_freeze_history", global_freeze_history))
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
    registrar.register(
        CallbackQueryHandler(
            handle_activation_individual_shift_confirm,
            pattern=r"^act_ishift_\d+_\d{12}$",
        )
    )
    registrar.register(
        CallbackQueryHandler(handle_activation_time_pick, pattern=r"^act_time_\d+_\d{12}$")
    )
    registrar.register(CallbackQueryHandler(handle_activation_ignore, pattern="^act_ignore$"))
    registrar.register(
        CallbackQueryHandler(handle_activation_shift_confirm, pattern=r"^act_shift_confirm_\d+_\d{12}$")
    )
    registrar.register(
        CallbackQueryHandler(handle_activation_shift_cancel, pattern=r"^act_shift_cancel_\d+$")
    )

    # Обработчики для заморозки спортсмена (все активные абонементы)
    registrar.register(
        CallbackQueryHandler(handle_freeze_subscription_start, pattern=r"^freeze_athlete_\d+_\d+$")
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
        CallbackQueryHandler(handle_unfreeze_subscription, pattern=r"^unfreeze_athlete_\d+_\d+$")
    )

    logger.info("✅ Все обработчики зарегистрированы")

