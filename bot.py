import logging
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes
)
from config import config
from handlers.start import start, show_coach_menu, show_athlete_menu
from handlers.coach_handlers import (
    coach_menu,
    add_athlete_start, add_athlete_full_name, add_athlete_phone,
    add_athlete_medical, add_athlete_age_group, add_athlete_subscription,
    athletes_list, cancel_athlete_creation,
    ATHLETE_FULL_NAME, ATHLETE_PHONE, ATHLETE_MEDICAL, ATHLETE_AGE_GROUP, ATHLETE_SUBSCRIPTION,
    # Функции для редактирования
    edit_athlete_start, edit_choose_field, edit_input_value, edit_cancel,
    back_to_list_callback,
    EDIT_ATHLETE_START, EDIT_CHOOSE_FIELD, EDIT_INPUT_VALUE
)

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('bot.log', encoding='utf-8')
    ]
)

# Отключаем логи HTTP запросов
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


def ensure_test_coach():
    """Создаем тестового тренера для разработки"""
    from database.models import Session, User
    from database.db_utils import create_user

    session = Session()
    try:
        coach_telegram_id = 26655492  # Ваш тестовый ID

        user = session.query(User).filter_by(telegram_id=coach_telegram_id).first()

        if not user:
            logger.info("Создаем тестового тренера...")
            create_user(
                session=session,
                telegram_id=coach_telegram_id,
                username="coach_mma",
                first_name="Тренер ММА",
                role="coach",
                sport_type="MMA"
            )
            logger.info("✅ Тестовый тренер создан")
        else:
            # Обновляем роль если нужно
            if user.role != 'coach' or user.sport_type != 'MMA':
                user.role = 'coach'
                user.sport_type = 'MMA'
                session.commit()
                logger.info("✅ Роль тренера обновлена")

    except Exception as e:
        logger.error(f"Ошибка при создании тренера: {e}")
        session.rollback()
    finally:
        session.close()


def main():
    """Запуск бота"""
    try:
        # Создаем тестового тренера
        ensure_test_coach()

        # Создаем приложение
        application = Application.builder().token(config.BOT_TOKEN).build()
        logger.info("🤖 Бот FightClubBot инициализирован")
        logger.info(f"📁 База данных: {config.DATABASE_URL}")

        # ========== CONVERSATION HANDLER ДЛЯ ДОБАВЛЕНИЯ СПОРТСМЕНА ==========
        add_athlete_conv = ConversationHandler(
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
                CommandHandler("menu", coach_menu),
                MessageHandler(filters.Regex("^(📋 Список спортсменов)$"), athletes_list),
            ],
            name="add_athlete_conversation",
            persistent=False,
            allow_reentry=True
        )

        # ========== CONVERSATION HANDLER ДЛЯ РЕДАКТИРОВАНИЯ СПОРТСМЕНА ==========
        edit_athlete_conv = ConversationHandler(
            entry_points=[
                CallbackQueryHandler(edit_athlete_start, pattern='^edit_athlete_'),
                CallbackQueryHandler(back_to_list_callback, pattern='^back_to_list$')
            ],
            states={
                EDIT_CHOOSE_FIELD: [
                    CallbackQueryHandler(edit_choose_field, pattern='^(edit_field_|edit_cancel)'),
                ],
                EDIT_INPUT_VALUE: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, edit_input_value),
                    CallbackQueryHandler(edit_input_value, pattern='^(sport_|status_)'),
                ],
            },
            fallbacks=[
                CommandHandler("cancel", edit_cancel),
                CallbackQueryHandler(edit_cancel, pattern='^edit_cancel$'),
                CommandHandler("menu", coach_menu),
            ],
            name="edit_athlete_conversation",
            persistent=False,
            allow_reentry=True
        )

        # ========== РЕГИСТРАЦИЯ ОБРАБОТЧИКОВ ==========

        # 1. Обработчик команды /start
        application.add_handler(CommandHandler("start", start))
        logger.info("✅ Обработчик /start добавлен")

        # 2. Обработчик команды /menu
        application.add_handler(CommandHandler("menu", coach_menu))
        logger.info("✅ Обработчик /menu добавлен")

        # 3. ConversationHandler для добавления спортсмена
        application.add_handler(add_athlete_conv)
        logger.info("✅ ConversationHandler для добавления спортсмена добавлен")

        # 4. ConversationHandler для редактирования спортсмена
        application.add_handler(edit_athlete_conv)
        logger.info("✅ ConversationHandler для редактирования спортсмена добавлен")

        # 5. Обработчики для кнопок меню тренера
        application.add_handler(MessageHandler(filters.Regex("^(📋 Список спортсменов)$"), athletes_list))

        # 6. Заглушки для остальных функций тренера
        tренер_заглушки = [
            "📊 Статистика посещений",
            "💰 Финансовая статистика",
            "📅 Отметить посещение",
            "⚙️ Настройки"
        ]
        application.add_handler(MessageHandler(
            filters.Regex(f"^({'|'.join(tренер_заглушки)})$"),
            lambda update, context: update.message.reply_text(
                "🛠 Эта функция находится в разработке.\n"
                "Скоро будет доступна!",
                parse_mode='Markdown'
            )
        ))

        # 7. Обработчики для меню спортсмена (заглушки)
        athlete_actions = [
            "📅 Мои тренировки",
            "💳 Мои платежи",
            "👤 Мой профиль",
            "📞 Связаться с тренером"
        ]
        application.add_handler(MessageHandler(
            filters.Regex(f"^({'|'.join(athlete_actions)})$"),
            lambda update, context: update.message.reply_text(
                "🥊 *Функция спортсмена*\n\n"
                "Эта функция находится в разработке.\n"
                "Скоро будет доступна!",
                parse_mode='Markdown'
            )
        ))

        # 8. Общий обработчик для неизвестных команд
        application.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            lambda update, context: update.message.reply_text(
                "🤔 *Неизвестная команда*\n\n"
                "Используйте /menu для вызова меню или /start для начала работы.",
                parse_mode='Markdown'
            )
        ))

        # ========== ЗАПУСК БОТА ==========
        logger.info("🚀 Бот запускается...")
        logger.info("=" * 50)
        logger.info("📋 Доступные функции:")
        logger.info("  • /start - Начало работы")
        logger.info("  • /menu - Главное меню")
        logger.info("  • Добавление спортсменов")
        logger.info("  • Просмотр списка спортсменов")
        logger.info("  • Редактирование данных спортсменов")
        logger.info("=" * 50)

        application.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True
        )

    except Exception as e:
        logger.error(f"❌ Критическая ошибка при запуске бота: {e}", exc_info=True)


if __name__ == "__main__":
    main()