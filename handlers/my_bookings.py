import logging
from keyboards import back_to_menu_keyboard
from handlers.start import send_main_menu

logger = logging.getLogger(__name__)

def show_my_bookings_info(bot, db, message):
    """Показ активных записей пользователя"""
    try:
        user_id = message.from_user.id
        bookings = db.get_user_bookings(user_id)

        if not bookings:
            bookings_text = "📭 *У вас нет активных записей на тренировки*"
        else:
            bookings_text = "📅 *ВАШИ ЗАПИСИ:*\n\n"
            for booking in bookings:
                bookings_text += f"📅 *{booking['date']}* в *{booking['time']}*\n"
                bookings_text += f"🥊 {booking['workout_name']}\n"
                bookings_text += f"👨‍🏫 Тренер: {booking['trainer']}\n"
                bookings_text += "─" * 25 + "\n\n"

        if hasattr(message, 'message_id'):
            bot.send_message(
                bookings_text,
                message.chat.id,
                message.message_id,
                parse_mode='Markdown',
                reply_markup=back_to_menu_keyboard()
            )
        else:
            bot.send_message(message.chat.id, bookings_text, parse_mode='Markdown',
                             reply_markup=back_to_menu_keyboard())

    except Exception as e:
        logger.error(f"Ошибка получения записей: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка загрузки записей")
        send_main_menu(bot, message.chat.id)