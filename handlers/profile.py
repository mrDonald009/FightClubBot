import logging
from keyboards import back_to_menu_keyboard
from handlers.start import send_main_menu

logger = logging.getLogger(__name__)

def show_profile_info(bot, db, message):
    """Показ профиля пользователя"""
    try:
        user_id = message.from_user.id
        profile = db.get_user_profile(user_id)

        profile_text = f"""👤 *ВАШ ПРОФИЛЬ*

*Имя:* {profile['full_name']}
*Телеграм:* @{profile['username']}
*Дата регистрации:* {profile['registration_date']}"""

        if hasattr(message, 'message_id'):
            bot.send_message(
                profile_text,
                message.chat.id,
                message.message_id,
                parse_mode='Markdown',
                reply_markup=back_to_menu_keyboard()
            )
        else:
            bot.send_message(message.chat.id, profile_text, parse_mode='Markdown',
                             reply_markup=back_to_menu_keyboard())

    except Exception as e:
        logger.error(f"Ошибка загрузки профиля: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка загрузки профиля")
        send_main_menu(bot, message.chat.id)