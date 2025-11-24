import logging
from keyboards import back_to_menu_keyboard
from handlers.start import send_main_menu

logger = logging.getLogger(__name__)

def show_profile_info(bot, db, message):
    """Показ профиля пользователя"""
    try:
        user_id = message.from_user.id
        profile = db.get_user_profile(user_id)
        stats = db.get_user_stats(user_id)

        profile_text = f"""👤 *ВАШ ПРОФИЛЬ*

*Имя:* {profile['full_name']}
*Телеграм:* @{profile['username']}
*Телефон:* {profile['phone']}
*Дата регистрации:* {profile['registration_date']}

📊 *СТАТИСТИКА:*
• Всего тренировок: {stats['total_workouts']}
• Текущая серия: {stats['current_streak']} дней
• Активные записи: {len(db.get_user_bookings(user_id))}

💪 *УРОВЕНЬ:* {_get_user_level(stats['total_workouts'])}"""

        # Всегда отправляем новое сообщение для сохранения истории
        bot.send_message(
            message.chat.id,
            profile_text,
            parse_mode='Markdown',
            reply_markup=back_to_menu_keyboard()
        )

    except Exception as e:
        logger.error(f"Ошибка загрузки профиля: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка загрузки профиля")
        send_main_menu(bot, message.chat.id)

def _get_user_level(total_workouts):
    """Определяет уровень пользователя по количеству тренировок"""
    if total_workouts >= 50:
        return "🥇 МАСТЕР"
    elif total_workouts >= 25:
        return "🥈 ОПЫТНЫЙ"
    elif total_workouts >= 10:
        return "🥉 БОЕЦ"
    elif total_workouts >= 5:
        return "🎯 НОВИЧОК"
    else:
        return "🌱 НАЧИНАЮЩИЙ"