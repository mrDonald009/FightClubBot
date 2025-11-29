import logging
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ConversationHandler
from config import config
from handlers.start import start, show_coach_menu, show_admin_menu, show_athlete_menu
from handlers.coach_handlers import (
    coach_menu, add_athlete_start, add_athlete_full_name, add_athlete_phone,
    add_athlete_medical, add_athlete_sport_type, add_athlete_age_group, add_athlete_subscription,
    athletes_list, cancel_athlete_creation,  # ← ДОБАВЬТЕ cancel_athlete_creation
    ATHLETE_FULL_NAME, ATHLETE_PHONE, ATHLETE_MEDICAL, ATHLETE_SPORT_TYPE, ATHLETE_AGE_GROUP, ATHLETE_SUBSCRIPTION
)

# Отключаем логи HTTP запросов
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)


def main():
    """Запуск бота"""
    try:
        # Создаем приложение
        application = Application.builder().token(config.BOT_TOKEN).build()

        # Обработчик команды /start
        application.add_handler(CommandHandler("start", start))

        # Обработчик команды /menu
        application.add_handler(CommandHandler("menu", coach_menu))

        # ConversationHandler для добавления спортсмена
        conv_handler = ConversationHandler(
            entry_points=[MessageHandler(filters.Regex("^(👥 Добавить спортсмена)$"), add_athlete_start)],
            states={
                ATHLETE_FULL_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_athlete_full_name)],
                ATHLETE_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_athlete_phone)],
                ATHLETE_MEDICAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_athlete_medical)],
                ATHLETE_SPORT_TYPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_athlete_sport_type)],
                ATHLETE_AGE_GROUP: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_athlete_age_group)],
                ATHLETE_SUBSCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_athlete_subscription)],
            },
            fallbacks=[
                # Обработчики для прерывания процесса
                MessageHandler(filters.Regex("^(📋 Список спортсменов)$"), cancel_athlete_creation),
                MessageHandler(filters.Regex("^(📊 Статистика посещений)$"), cancel_athlete_creation),
                MessageHandler(filters.Regex("^(💰 Финансовая статистика)$"), cancel_athlete_creation),
                MessageHandler(filters.Regex("^(📅 Отметить посещение)$"), cancel_athlete_creation),
                MessageHandler(filters.Regex("^(⚙️ Настройки)$"), cancel_athlete_creation),
                CommandHandler("menu", cancel_athlete_creation),
                CommandHandler("start", cancel_athlete_creation),
                CommandHandler("cancel", cancel_athlete_creation),
            ]
        )

        application.add_handler(conv_handler)

        # Обработчики для кнопок меню (для обычного режима)
        application.add_handler(MessageHandler(filters.Regex("^(📋 Список спортсменов)$"), athletes_list))
        application.add_handler(MessageHandler(filters.Regex("^(📊 Статистика посещений)$"),
                                               lambda update, context: update.message.reply_text(
                                                   "📊 Функция в разработке")))
        application.add_handler(MessageHandler(filters.Regex("^(💰 Финансовая статистика)$"),
                                               lambda update, context: update.message.reply_text(
                                                   "💰 Функция в разработке")))
        application.add_handler(MessageHandler(filters.Regex("^(📅 Отметить посещение)$"),
                                               lambda update, context: update.message.reply_text(
                                                   "📅 Функция в разработке")))
        application.add_handler(MessageHandler(filters.Regex("^(⚙️ Настройки)$"),
                                               lambda update, context: update.message.reply_text(
                                                   "⚙️ Функция в разработке")))

        # Обработчик неизвестных команд
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,
                                               lambda update, context: update.message.reply_text(
                                                   "❌ Неизвестная команда. Используйте /menu")))

        # Запускаем бота
        print("✅ Бот запущен...")
        print("🤖 Отправьте /start в Telegram для начала работы")
        print("📝 Логи действий будут отображаться здесь")
        application.run_polling()

    except Exception as e:
        print(f"❌ Ошибка при запуске бота: {e}")


if __name__ == "__main__":
    main()