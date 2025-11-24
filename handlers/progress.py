import logging
from keyboards import back_to_menu_keyboard
from handlers.start import send_main_menu

logger = logging.getLogger(__name__)


def show_progress_info(bot, db, message):
    """Показ прогресса пользователя"""
    try:
        stats = db.get_user_stats(message.from_user.id)
        progress_text = _format_progress_text(stats)

        # Всегда отправляем новое сообщение для сохранения истории
        bot.send_message(message.chat.id, progress_text, parse_mode='Markdown',
                         reply_markup=back_to_menu_keyboard())

    except Exception as e:
        logger.error(f"Ошибка загрузки прогресса: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка загрузки прогресса")
        send_main_menu(bot, message.chat.id)


def _format_progress_text(stats):
    """Форматирует текст прогресса"""
    return f"""📊 *ВАШ ПРОГРЕСС*

🎯 Посещений всего: *{stats['total_workouts']}*
📈 Текущая серия: *{stats['current_streak']} дней*
🔥 Сожжено калорий: *~{stats['total_workouts'] * 500} ккал*

🏆 *ДОСТИЖЕНИЯ:*
{'✅' if stats['total_workouts'] >= 5 else '⏳'} Новичок (5 тренировок)
{'✅' if stats['current_streak'] >= 3 else '⏳'} Стабильность (3 дня подряд)
{'✅' if stats['total_workouts'] >= 10 else '⏳'} Боец (10 тренировок)"""