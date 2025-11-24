from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

def main_menu():
    """Главное меню"""
    keyboard = InlineKeyboardMarkup(row_width=2)
    buttons = [
        ("🥊 Записаться на тренировку", "menu_booking"),
        ("📊 Мой прогресс", "menu_progress"),
        ("🎯 Челенджи и бонусы", "menu_challenges"),
        ("👤 Мой профиль", "menu_profile"),
        ("🏆 Таблица лидеров", "menu_leaderboard"),
        ("📅 Мои записи", "menu_my_bookings"),
        ("📋 Расписание", "menu_schedule"),
        ("💰 Цены", "menu_prices"),
        ("📞 Контакты", "menu_contacts"),
        ("ℹ️ Помощь", "menu_help")
    ]

    # Добавляем кнопки парами
    for i in range(0, len(buttons), 2):
        if i + 1 < len(buttons):
            keyboard.row(
                InlineKeyboardButton(buttons[i][0], callback_data=buttons[i][1]),
                InlineKeyboardButton(buttons[i + 1][0], callback_data=buttons[i + 1][1])
            )
        else:
            keyboard.add(InlineKeyboardButton(buttons[i][0], callback_data=buttons[i][1]))

    return keyboard