import logging
from keyboards import back_to_menu_keyboard

logger = logging.getLogger(__name__)

def send_prices_info(bot, config, message):
    """Показ цен"""
    prices_text = """
💳 *СТОИМОСТЬ АБОНЕМЕНТОВ:*

*👶 ДЕТСКИЕ ГРУППЫ:*
• Тайский бокс дети - *6 000 ₽/мес*
• ММА дети - *6 000 ₽/мес*

*👨‍🦰 ВЗРОСЛЫЕ ГРУППЫ:*
• Тайский бокс - *6 000 ₽/мес*
• ММА - *6 000 ₽/мес*  
• Грэпплинг/БЖЖ - *6 000 ₽/мес*
• Бокс (утренние) - *6 000 ₽/мес*

*🎯 КОМБО И ИНДИВИДУАЛЬНО:*
• Тайский бокс + ММА - *11 000 ₽/мес*
• Индивидуальная тренировка - *3 000 ₽*
• Сплит тренировка (2 чел) - *4 000 ₽*

💪 *Первая тренировка - БЕСПЛАТНО!*"""

    # Всегда отправляем новое сообщение для сохранения истории
    bot.send_message(
        message.chat.id,
        prices_text,
        parse_mode='Markdown',
        reply_markup=back_to_menu_keyboard()
    )