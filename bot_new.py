import logging
import sys
from pathlib import Path

# Добавляем текущую директорию в путь для импортов
sys.path.append(str(Path(__file__).parent))

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ConversationHandler

# Импорты из существующего кода
try:
    # Пробуем импортировать из нового core.config
    try:
        from core.config import Config

        print("✅ Загружена новая конфигурация (core.config)")
        config_source = "new"
    except ImportError:
        # Если нет новой конфигурации, пробуем старую
        from config import config as old_config

        print("✅ Загружена старая конфигурация (config.config)")
        config_source = "old"

    from handlers.start import start
    from handlers.coach_handlers import (
        coach_menu, add_athlete_start, add_athlete_full_name, add_athlete_phone,
        add_athlete_medical, add_athlete_age_group, add_athlete_subscription,
        athletes_list, cancel_athlete_creation,
        handle_back_to_menu_main, handle_show_more_info,
        ATHLETE_FULL_NAME, ATHLETE_PHONE, ATHLETE_MEDICAL, ATHLETE_AGE_GROUP, ATHLETE_SUBSCRIPTION
    )
    from handlers.card_handlers import (
        show_athlete_card,
        show_subscription_card,
        handle_back_to_list,
        handle_back_to_menu
    )

except ImportError as e:
    print(f"❌ Ошибка импорта: {e}")
    print("⚠️ Проверьте наличие всех необходимых файлов:")
    print("   - handlers/start.py")
    print("   - handlers/coach_handlers.py")
    print("   - handlers/card_handlers.py")
    print("   - core/config.py или config.py")
    sys.exit(1)

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('bot_new.log', encoding='utf-8')
    ]
)

logger = logging.getLogger(__name__)


def ensure_test_coach():
    """Убедиться, что тестовый тренер существует"""
    from database.models import Session, User
    from database.db_utils import create_user

    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=26655492).first()

        if user:
            print(f"📋 Найден пользователь: {user.first_name}")
            if user.role != 'coach' or user.sport_type != 'MMA':
                print("🔄 Исправляем роль и спорт...")
                user.role = 'coach'
                user.sport_type = 'MMA'
                session.commit()
                print("✅ Пользователь исправлен!")
        else:
            print("❌ Пользователь не найден, создаем...")
            create_user(
                session=session,
                telegram_id=26655492,
                username="coach_mma",
                first_name="Тренер ММА",
                role="coach",
                sport_type="MMA"
            )
            print("✅ Тестовый тренер создан!")

    except Exception as e:
        print(f"❌ Ошибка при проверке тренера: {e}")
        session.rollback()
    finally:
        session.close()


def get_bot_token(config_source):
    """Получить токен бота в зависимости от источника конфигурации"""
    if config_source == "new":
        # Используем новую конфигурацию
        config = Config()
        return config.BOT_TOKEN
    else:
        # Используем старую конфигурацию
        return old_config.BOT_TOKEN


def setup_handlers(application):
    """Настроить все обработчики"""

    # ConversationHandler для добавления спортсмена
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
    print("✅ ConversationHandler добавлен")

    # Обработчики для списка спортсменов
    application.add_handler(CallbackQueryHandler(handle_back_to_menu_main, pattern="^back_to_menu_main$"))
    application.add_handler(CallbackQueryHandler(handle_show_more_info, pattern="^show_more_info$"))
    print("✅ Обработчики списка спортсменов добавлены")

    # Обработчики для карточек
    application.add_handler(CallbackQueryHandler(show_athlete_card, pattern="^athlete_"))
    application.add_handler(CallbackQueryHandler(show_subscription_card, pattern="^subscription_"))
    application.add_handler(CallbackQueryHandler(handle_back_to_list, pattern="^back_to_list$"))
    application.add_handler(CallbackQueryHandler(handle_back_to_menu, pattern="^back_to_menu"))
    print("✅ Обработчики карточек добавлены")

    # Команды быстрого доступа
    application.add_handler(CommandHandler("card", show_athlete_card))
    application.add_handler(CommandHandler("sub", show_subscription_card))

    # Обработчик команды /start
    application.add_handler(CommandHandler("start", start))
    print("✅ Обработчик /start добавлен")

    # Обработчик команды /menu
    application.add_handler(CommandHandler("menu", coach_menu))
    print("✅ Обработчик /menu добавлен")

    # Команда для проверки абонементов
    from utils.subscription_checker import SubscriptionChecker

    async def check_all_subscriptions(update: Update, context):
        """Проверить и обновить статусы всех абонементов"""
        user_id = update.effective_user.id

        # Проверяем права (только админ или тренер)
        from database.models import Session
        from database.db_utils import get_user_by_telegram_id

        session = Session()
        try:
            user = get_user_by_telegram_id(session, user_id)
            if not user or user.role not in ['coach', 'admin']:
                await update.message.reply_text("❌ У вас нет прав для этой команды")
                return
        finally:
            session.close()

        # Выполняем проверку
        updated_count = SubscriptionChecker.check_and_update_subscriptions()

        await update.message.reply_text(
            f"🔄 Проверка абонементов завершена\n"
            f"✅ Обновлено статусов: {updated_count}\n\n"
            f"Теперь все абонементы имеют актуальный статус."
        )

    application.add_handler(CommandHandler("check_subs", check_all_subscriptions))
    print("✅ Обработчик /check_subs добавлен")

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
    print("✅ Обработчики кнопок меню добавлены")


def main():
    """Запуск бота с новой архитектурой"""
    try:
        print("=" * 50)
        print("🚀 ЗАПУСК НОВОЙ АРХИТЕКТУРЫ FIGHTCLUBBOT")
        print("=" * 50)

        # Проверяем тестового тренера
        ensure_test_coach()

        # АВТОМАТИЧЕСКАЯ ПРОВЕРКА АБОНЕМЕНТОВ ПРИ ЗАПУСКЕ
        try:
            from utils.subscription_checker import SubscriptionChecker
            updated_count = SubscriptionChecker.check_and_update_subscriptions()
            if updated_count > 0:
                print(f"🔄 При запуске обновлено {updated_count} абонементов")
        except ImportError as e:
            print(f"⚠️ Не удалось загрузить SubscriptionChecker: {e}")
            print("⚠️ Проверка абонементов будет выполнена при открытии карточек")

        # Получаем токен бота
        bot_token = get_bot_token(config_source)
        print(f"✅ Токен бота получен ({config_source} конфигурация)")

        # Создаем приложение
        application = Application.builder().token(bot_token).build()
        print("🤖 Бот инициализирован")

        # Настраиваем обработчики
        setup_handlers(application)

        # Запускаем бота
        print("=" * 50)
        print("✅ ВСЕ ОБРАБОТЧИКИ ДОБАВЛЕНЫ")
        print("🤖 БОТ ЗАПУСКАЕТСЯ...")
        print("=" * 50)
        print("ℹ️  Логи будут сохраняться в bot_new.log")
        print("ℹ️  Для остановки нажмите Ctrl+C")
        print("=" * 50)

        application.run_polling()

    except Exception as e:
        print(f"❌ Критическая ошибка при запуске бота: {e}")
        logger.error(f"Critical error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()