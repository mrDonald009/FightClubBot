import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler, \
    ConversationHandler, ContextTypes
from config import config
from handlers.start import start, show_coach_menu, show_admin_menu, show_athlete_menu
from handlers.coach_handlers import (
    coach_menu, add_athlete_start, add_athlete_full_name, add_athlete_phone,
    add_athlete_medical, add_athlete_age_group, add_athlete_subscription,
    athletes_list, cancel_athlete_creation,
    ATHLETE_FULL_NAME, ATHLETE_PHONE, ATHLETE_MEDICAL, ATHLETE_AGE_GROUP, ATHLETE_SUBSCRIPTION
)
from handlers.card_handlers import (
    show_athlete_card,
    show_subscription_card,
    handle_back_to_list,
    handle_back_to_menu
)

# Код для проверки и исправления пользователя 26655492
from database.models import Session, User
from database.db_utils import create_user


def ensure_coach_user():
    """Убеждаемся, что пользователь 26655492 существует с правильными правами"""
    session = Session()
    try:
        print("🔍 Проверяем пользователя 26655492...")

        user = session.query(User).filter_by(telegram_id=26655492).first()

        if user:
            print(f"📋 Найден пользователь: {user.first_name}")
            print(f"   Текущая роль: {user.role}")
            print(f"   Текущий спорт: {user.sport_type}")

            # Проверяем и исправляем если нужно
            if user.role != 'coach' or user.sport_type != 'MMA':
                print("🔄 Исправляем роль и спорт...")
                user.role = 'coach'
                user.sport_type = 'MMA'
                session.commit()
                print("✅ Пользователь исправлен!")
            else:
                print("✅ Пользователь уже имеет правильные настройки")

        else:
            print("❌ Пользователь не найден, создаем...")
            # Создаем пользователя
            new_user = create_user(
                session=session,
                telegram_id=26655492,
                username="coach_mma",
                first_name="Тренер ММА",
                role="coach",
                sport_type="MMA"
            )
            print(f"✅ Создан пользователь: {new_user.first_name}")

        # Финальная проверка
        final_user = session.query(User).filter_by(telegram_id=26655492).first()
        print(
            f"🎯 Финальная проверка: {final_user.first_name}, роль: {final_user.role}, спорт: {final_user.sport_type}")

    except Exception as e:
        print(f"❌ Ошибка при проверке пользователя: {e}")
        session.rollback()
    finally:
        session.close()


# Вызываем функцию при запуске
ensure_coach_user()

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
        # Создаем приложение с параметром для избежания конфликтов
        application = Application.builder().token(config.BOT_TOKEN).get_updates_read_timeout(30).build()
        #application = Application.builder().token(config.BOT_TOKEN).build()
        print("🤖 БОТ ИНИЦИАЛИЗИРОВАН")
        print(f"🔑 TOKEN: {config.BOT_TOKEN[:10]}...")
        print(f"📁 DATABASE: {config.DATABASE_URL}")

        # ConversationHandler для добавления спортсмена - ДОЛЖЕН БЫТЬ ПЕРВЫМ!
        conv_handler = ConversationHandler(
            entry_points=[MessageHandler(filters.Regex("^(👥 Добавить спортсмена)$"), add_athlete_start)],
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
                ATHLETE_SUBSCRIPTION: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, add_athlete_subscription)
                ],
            },
            fallbacks=[
                CommandHandler("cancel", cancel_athlete_creation),
            ],
            name="add_athlete_conversation",
            persistent=False,
            allow_reentry=True
        )

        application.add_handler(conv_handler)
        print("✅ CONVERSATIONHANDLER ДЛЯ ДОБАВЛЕНИЯ СПОРТСМЕНА ДОБАВЛЕН")

        # Обработчики для карточек
        application.add_handler(CallbackQueryHandler(show_athlete_card, pattern="^athlete_"))
        application.add_handler(CallbackQueryHandler(show_subscription_card, pattern="^subscription_"))
        application.add_handler(CallbackQueryHandler(handle_back_to_list, pattern="^back_to_list$"))
        application.add_handler(CallbackQueryHandler(handle_back_to_menu, pattern="^back_to_menu"))
        application.add_handler(CallbackQueryHandler(handle_back_to_menu, pattern="^back_to_menu_main$"))

        # Команды быстрого доступа
        application.add_handler(CommandHandler("card", show_athlete_card))
        application.add_handler(CommandHandler("sub", show_subscription_card))

        # Обработчик команды /start
        application.add_handler(CommandHandler("start", start))
        print("✅ ОБРАБОТЧИК /start ДОБАВЛЕН")

        # Обработчик команды /menu
        application.add_handler(CommandHandler("menu", coach_menu))
        print("✅ ОБРАБОТЧИК /menu ДОБАВЛЕН")

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

        # Запускаем бота
        print("🚀 БОТ ЗАПУСКАЕТСЯ...")
        print("=" * 50)
        application.run_polling()

    except Exception as e:
        print(f"❌ КРИТИЧЕСКАЯ ОШИБКА ПРИ ЗАПУСКЕ БОТА: {e}")
        logger.error(f"Critical error: {e}")


if __name__ == "__main__":
    main()