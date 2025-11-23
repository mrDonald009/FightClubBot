from keyboards import back_to_menu_keyboard

def show_leaderboard_info(bot, message):
    """Показ таблицы лидеров"""
    leaderboard_text = """🏆 *ТАБЛИЦА ЛИДЕРОВ* | Этот месяц

🥇 Алексей П. - *12 тренировок*
🥈 Мария К. - *11 тренировок*  
🥉 Дмитрий С. - *10 тренировок*
4. Анна М. - *9 тренировок*
5. Сергей В. - *8 тренировок*

*Ваше место:* входите в топ-10!

💪 *Следующая цель:* 15 тренировок в месяце"""

    if hasattr(message, 'message_id'):
        bot.send_message(
            leaderboard_text,
            message.chat.id,
            message.message_id,
            parse_mode='Markdown',
            reply_markup=back_to_menu_keyboard()
        )
    else:
        bot.send_message(message.chat.id, leaderboard_text, parse_mode='Markdown',
                         reply_markup=back_to_menu_keyboard())