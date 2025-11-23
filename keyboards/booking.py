import datetime
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton


def days_selection_keyboard():
    """Инлайн-клавиатура для выбора дня"""
    today = datetime.datetime.now()
    tomorrow = today + datetime.timedelta(days=1)
    day_after_tomorrow = today + datetime.timedelta(days=2)

    keyboard = InlineKeyboardMarkup(row_width=1)

    keyboard.add(
        InlineKeyboardButton(
            f"📅 Сегодня ({today.strftime('%d.%m')})",
            callback_data="select_day_0"
        ),
        InlineKeyboardButton(
            f"📅 Завтра ({tomorrow.strftime('%d.%m')})",
            callback_data="select_day_1"
        ),
        InlineKeyboardButton(
            f"📅 Послезавтра ({day_after_tomorrow.strftime('%d.%m')})",
            callback_data="select_day_2"
        )
    )

    keyboard.add(
        InlineKeyboardButton("🔙 Главное меню", callback_data="return_to_main_menu")
    )

    return keyboard


def back_to_days_keyboard():
    """Клавиатура для возврата к выбору дня"""
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("🔙 Выбрать другой день", callback_data="menu_booking"))
    return keyboard