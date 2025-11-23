from keyboards import back_to_menu_keyboard

def send_prices_info(bot, config, message):
    """Показ цен"""
    prices_text = """
💳 *Стоимость абонементов:*

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

    if hasattr(message, 'message_id'):
        bot.send_message(
            prices_text,
            message.chat.id,
            message.message_id,
            parse_mode='Markdown',
            reply_markup=back_to_menu_keyboard()
        )
    else:
        bot.send_message(message.chat.id, prices_text, parse_mode='Markdown',
                         reply_markup=back_to_menu_keyboard())