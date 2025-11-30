import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler, \
    ConversationHandler, ContextTypes
from config import config
from handlers.start import start, show_coach_menu, show_admin_menu, show_athlete_menu
from handlers.coach_handlers import (
    coach_menu, add_athlete_start, add_athlete_full_name, add_athlete_phone,
    add_athlete_medical, add_athlete_sport_type, add_athlete_age_group, add_athlete_subscription,
    athletes_list, cancel_athlete_creation,
    ATHLETE_FULL_NAME, ATHLETE_PHONE, ATHLETE_MEDICAL, ATHLETE_SPORT_TYPE, ATHLETE_AGE_GROUP, ATHLETE_SUBSCRIPTION
)

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(),  # Вывод в консоль
        logging.FileHandler('bot.log', encoding='utf-8')  # Запись в файл
    ]
)

# Отключаем логи HTTP запросов
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


def main():
    """Запуск бота"""
    try:
        # Создаем приложение
        application = Application.builder().token(config.BOT_TOKEN).build()
        print("🤖 БОТ ИНИЦИАЛИЗИРОВАН")
        print(f"🔑 TOKEN: {config.BOT_TOKEN[:10]}...")
        print(f"📁 DATABASE: {config.DATABASE_URL}")

        # Обработчик команды /start
        application.add_handler(CommandHandler("start", start))
        print("✅ ОБРАБОТЧИК /start ДОБАВЛЕН")

        # Обработчик команды /menu
        application.add_handler(CommandHandler("menu", coach_menu))
        print("✅ ОБРАБОТЧИК /menu ДОБАВЛЕН")

        # ConversationHandler для добавления спортсмена - ДОЛЖЕН БЫТЬ ПЕРВЫМ ИЗ MessageHandler
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
                MessageHandler(filters.Regex("^(👥 Добавить спортсмена)$"), cancel_athlete_creation),
                MessageHandler(filters.Regex("^(📋 Список спортсменов)$"), cancel_athlete_creation),
                MessageHandler(filters.Regex("^(📊 Статистика посещений)$"), cancel_athlete_creation),
                MessageHandler(filters.Regex("^(💰 Финансовая статистика)$"), cancel_athlete_creation),
                MessageHandler(filters.Regex("^(📅 Отметить посещение)$"), cancel_athlete_creation),
                MessageHandler(filters.Regex("^(⚙️ Настройки)$"), cancel_athlete_creation),
                CommandHandler("menu", cancel_athlete_creation),
                CommandHandler("start", cancel_athlete_creation),
                CommandHandler("cancel", cancel_athlete_creation),
            ],
            name="add_athlete_conversation",
            persistent=False
        )

        application.add_handler(conv_handler)
        print("✅ CONVERSATIONHANDLER ДЛЯ ДОБАВЛЕНИЯ СПОРТСМЕНА ДОБАВЛЕН")

        # Обработчики для кнопок меню
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
        print("✅ ОБРАБОТЧИКИ КНОПОК МЕНЮ ДОБАВЛЕНЫ")

        # Обработчик для ВСЕХ текстовых сообщений - ДОЛЖЕН БЫТЬ ПОСЛЕДНИМ
        async def handle_all_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
            """Обработчик всех текстовых сообщений"""
            user_id = update.effective_user.id
            text = update.message.text
            print(f"📝 ПОЛЬЗОВАТЕЛЬ {user_id} ОТПРАВИЛ ТЕКСТ: '{text}'")

            # Проверяем, не является ли текст одной из кнопок меню
            menu_buttons = [
                "👥 Добавить спортсмена", "📋 Список спортсменов",
                "📊 Статистика посещений", "💰 Финансовая статистика",
                "📅 Отметить посещение", "⚙️ Настройки"
            ]

            if text in menu_buttons:
                print(f"🎯 ТЕКСТ ЯВЛЯЕТСЯ КНОПКОЙ МЕНЮ: {text}")
                # Перенаправляем в соответствующий обработчик
                if text == "👥 Добавить спортсмена":
                    await add_athlete_start(update, context)
                elif text == "📋 Список спортсменов":
                    await athletes_list(update, context)
                else:
                    await update.message.reply_text(f"{text} - функция в разработке")
            else:
                print(f"❌ НЕИЗВЕСТНЫЙ ТЕКСТ: '{text}'")
                await update.message.reply_text("❌ Неизвестная команда. Используйте кнопки меню или /menu")

        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_all_text))
        print("✅ ОБРАБОТЧИК ВСЕХ ТЕКСТОВЫХ СООБЩЕНИЙ ДОБАВЛЕН")

        # Запускаем бота
        print("🚀 БОТ ЗАПУСКАЕТСЯ...")
        print("=" * 50)
        application.run_polling()

    except Exception as e:
        print(f"❌ КРИТИЧЕСКАЯ ОШИБКА ПРИ ЗАПУСКЕ БОТА: {e}")
        logger.error(f"Critical error: {e}")


if __name__ == "__main__":
    main()