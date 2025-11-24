import logging
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

logger = logging.getLogger(__name__)


def is_trainer(db, telegram_id):
    """Проверяет, является ли пользователь тренером"""
    try:
        with db.get_connection() as conn:
            result = conn.execute(
                'SELECT id FROM trainers WHERE telegram_id = ? AND is_active = 1',
                (telegram_id,)
            ).fetchone()
            return result is not None
    except Exception as e:
        logger.error(f"Ошибка проверки тренера: {e}")
        return False


def handle_trainer_command(bot, db, message):
    """Обрабатывает команду /trainer"""
    try:
        user_id = message.from_user.id

        if not is_trainer(db, user_id):
            bot.send_message(
                message.chat.id,
                "❌ *Доступ запрещен*\n\nЭта команда доступна только тренерам клуба.",
                parse_mode='Markdown'
            )
            return

        # Если пользователь тренер - показываем панель тренера
        from .dashboard import show_trainer_dashboard
        show_trainer_dashboard(bot, db, message)

    except Exception as e:
        logger.error(f"Ошибка обработки команды тренера: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка доступа к панели тренера")