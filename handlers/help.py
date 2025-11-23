import logging
from keyboards import back_to_menu_keyboard

logger = logging.getLogger(__name__)

def send_help_info(bot, message):
    """Показ помощи"""
    help_text = """
ℹ️ *Помощь по боту:*

*Основные функции:*
🥊 *Запись на тренировки* - выбирайте день и время
📊 *Прогресс* - отслеживайте свои достижения  
🎯 *Челенджи* - участвуйте и получайте бонусы
📅 *Мои записи* - просмотр активных бронирований

*Команды:*
/start - Главное меню
/menu - Показать меню
/schedule - Расписание тренировок
/price - Цены и абонементы
/contacts - Контакты клуба

💡 *Первая тренировка - БЕСПЛАТНО!*"""

    if hasattr(message, 'message_id'):
        bot.send_message(
            help_text,
            message.chat.id,
            message.message_id,
            parse_mode='Markdown',
            reply_markup=back_to_menu_keyboard()
        )
    else:
        bot.send_message(message.chat.id, help_text, parse_mode='Markdown',
                         reply_markup=back_to_menu_keyboard())