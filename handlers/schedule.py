from keyboards import back_to_menu_keyboard

def show_schedule_info(bot, message):
    """Показ расписания"""
    schedule_text = """
📅 *Расписание тренировок:*

*ПОНЕДЕЛЬНИК:*
19:00 - Тайский бокс (взрослые)
20:30 - ММА (взрослые)

*ВТОРНИК:*
17:00 - Тайский бокс (дети 9-14 лет)
19:00 - Грэпплинг (взрослые)

*СРЕДА:*
19:00 - Тайский бокс (взрослые)
20:30 - ММА (взрослые)

*ЧЕТВЕРГ:*
17:00 - Тайский бокс (дети 9-14 лет)
19:00 - Грэпплинг (взрослые)

*ПЯТНИЦА:*
19:00 - Тайский бокс (взрослые)
20:30 - ММА (взрослые)

*СУББОТА:*
11:00 - Тайский бокс (дети 5-8 лет)
12:00 - ММА (дети)

*ВОСКРЕСЕНЬЕ* - ВЫХОДНОЙ

💡 *Первая тренировка - БЕСПЛАТНО!*"""

    if hasattr(message, 'message_id'):
        bot.send_message(
            schedule_text,
            message.chat.id,
            message.message_id,
            parse_mode='Markdown',
            reply_markup=back_to_menu_keyboard()
        )
    else:
        bot.send_message(message.chat.id, schedule_text, parse_mode='Markdown',
                         reply_markup=back_to_menu_keyboard())