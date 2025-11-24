import logging
from keyboards import back_to_menu_keyboard

logger = logging.getLogger(__name__)

def show_challenges_info(bot, message):
    """Показ челенджей"""
    challenges_text = """🎯 *АКТИВНЫЕ ЧЕЛЛЕНДЖИ:*

🔥 *СИЛА ВОЛИ* - 0/7 дней
Посещайте тренировки 7 дней подряд
🎁 *Награда:* 1 бесплатная тренировка

👥 *ПРИВЕДИ ДРУГА*
Приведите друга и получите:
• 2 бесплатных занятия  
• Совместную тренировку с тренером
🎁 *Награда:* 2 бесплатных занятия

🏆 *МАРАФОН 30 ДНЕЙ*
Посещайте тренировки 30 дней подряд
🎁 *Награда:* Фирменная футболка клуба

💪 *Участвуйте и получайте бонусы!*"""

    if hasattr(message, 'message_id'):
        bot.send_message(
            message.chat.id,
            challenges_text,
            parse_mode='Markdown',
            reply_markup=back_to_menu_keyboard()
        )
    else:
        bot.send_message(
            message.chat.id,
            challenges_text,
            parse_mode='Markdown',
            reply_markup=back_to_menu_keyboard()
        )