import logging
from keyboards import back_to_menu_keyboard

logger = logging.getLogger(__name__)

def show_leaderboard_info(bot, message):
    """Показ таблицы лидеров"""
    try:
        leaderboard_text = """🏆 *ТАБЛИЦА ЛИДЕРОВ* | Этот месяц

🥇 Алексей П. - *12 тренировок*
🥈 Мария К. - *11 тренировок*  
🥉 Дмитрий С. - *10 тренировок*
4️⃣ Анна М. - *9 тренировок*
5️⃣ Сергей В. - *8 тренировок*

*Ваше место:* входите в топ-10!

💪 *Следующая цель:* 15 тренировок в месяце"""

        # Всегда отправляем новое сообщение для сохранения истории
        bot.send_message(
            message.chat.id,
            leaderboard_text,
            parse_mode='Markdown',
            reply_markup=back_to_menu_keyboard()
        )

    except Exception as e:
        logger.error(f"Ошибка загрузки таблицы лидеров: {e}")
        bot.send_message(
            message.chat.id,
            "❌ Ошибка загрузки таблицы лидеров",
            reply_markup=back_to_menu_keyboard()
        )