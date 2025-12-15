"""Роутер для регистрации всех обработчиков."""
import logging
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
from services.subscription_service import SubscriptionService
from handlers.start import start
from handlers.coach_handlers import (
    coach_menu,
    add_athlete_start,
    add_athlete_full_name,
    add_athlete_phone,
    add_athlete_medical,
    add_athlete_age_group,
    handle_training_date_selection,
    athletes_list,
    athletes_list_filtered,
    athletes_categories,
    cancel_athlete_creation,
    handle_back_to_menu_main,
    handle_show_more_info,
    start_training,
    show_coach_calendar,
    handle_calendar_navigation,
    handle_calendar_date_click,
    handle_calendar_empty_click,
    ATHLETE_FULL_NAME,
    ATHLETE_PHONE,
    ATHLETE_MEDICAL,
    ATHLETE_AGE_GROUP,
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
)
from handlers.attendance_handlers import (
    mark_attendance_start,
    handle_training_selection,
    execute_mark_attendance,
)

logger = logging.getLogger(__name__)


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
            if not user or user.role not in ['coach', 'admin']:
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


def register_all_handlers(registrar: HandlerRegistrar) -> None:
    """
    Зарегистрировать все обработчики бота.
    
    Args:
        registrar: Регистратор обработчиков
    """
    # ConversationHandler для добавления спортсмена
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
            ATHLETE_MEDICAL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_athlete_medical)
            ],
            ATHLETE_AGE_GROUP: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_athlete_age_group)
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_athlete_creation),
        ],
        name="add_athlete_conversation",
        persistent=False,
        allow_reentry=True
    )
    registrar.register(conv_handler)

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

    # Обработчики для отметки посещения
    registrar.register(
        CallbackQueryHandler(mark_attendance_start, pattern="^mark_attendance_")
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

    # Обработчики для кнопок меню
    registrar.register(
        MessageHandler(filters.Regex("^(📋 Список спортсменов)$"), athletes_list)
    )
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
        MessageHandler(filters.Regex("^(🏋️ Начать тренировку)$"), start_training)
    )
    registrar.register(
        MessageHandler(filters.Regex("^(📅 Мой календарь)$"), show_coach_calendar)
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

    logger.info("✅ Все обработчики зарегистрированы")

