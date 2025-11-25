import datetime
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton


def days_selection_keyboard():
    """Инлайн-клавиатура для выбора дня - расширенная версия на 7 дней"""
    today = datetime.datetime.now()

    keyboard = InlineKeyboardMarkup(row_width=2)

    # Создаем кнопки на 7 дней вперед
    days = []
    for i in range(7):
        day = today + datetime.timedelta(days=i)
        # Форматируем дату для отображения
        day_display = day.strftime('%d.%m')
        day_name = _get_day_name(day.weekday())
        days.append((f"📅 {day_display} ({day_name})", f"select_day_{i}"))

    # Добавляем кнопки по две в строку
    for i in range(0, len(days), 2):
        if i + 1 < len(days):
            keyboard.row(
                InlineKeyboardButton(days[i][0], callback_data=days[i][1]),
                InlineKeyboardButton(days[i + 1][0], callback_data=days[i + 1][1])
            )
        else:
            keyboard.add(InlineKeyboardButton(days[i][0], callback_data=days[i][1]))

    keyboard.add(
        InlineKeyboardButton("🔙 Главное меню", callback_data="return_to_main_menu")
    )

    return keyboard


def _get_day_name(weekday):
    """Возвращает русское название дня недели"""
    days = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    return days[weekday]


def back_to_days_keyboard():
    """Клавиатура для возврата к выбору дня"""
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("🔙 Выбрать другой день", callback_data="menu_booking"))
    return keyboard