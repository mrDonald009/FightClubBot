from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton


def get_coach_main_menu():
    """Главное меню тренера"""
    keyboard = [
        [KeyboardButton("👥 Добавить спортсмена"), KeyboardButton("📋 Список спортсменов")],
        [KeyboardButton("📝 Отметить посещения"), KeyboardButton("📅 Мой календарь")],
        [KeyboardButton("🤒 Отсутствие тренера"), KeyboardButton("📊 Статистика")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def get_athlete_list_keyboard(athletes):
    """Клавиатура для списка спортсменов"""
    keyboard = []

    for athlete in athletes:
        keyboard.append([
            InlineKeyboardButton(
                f"✏️ {athlete.first_name} {athlete.last_name or ''}",
                callback_data=f"edit_athlete_{athlete.id}"
            )
        ])

    # Кнопка возврата в меню
    keyboard.append([
        InlineKeyboardButton("🔙 Назад в меню", callback_data="back_to_menu")
    ])

    return InlineKeyboardMarkup(keyboard)


def get_edit_athlete_keyboard(athlete_id):
    """Клавиатура для редактирования спортсмена"""
    keyboard = [
        [InlineKeyboardButton("👤 Изменить имя", callback_data="edit_field_first_name")],
        [InlineKeyboardButton("📞 Изменить телефон", callback_data="edit_field_phone")],
        [InlineKeyboardButton("🥊 Изменить вид спорта", callback_data="edit_field_sport_type")],
        [InlineKeyboardButton("🏷️ Изменить статус", callback_data="edit_field_status")],
        [InlineKeyboardButton("❌ Отмена", callback_data="edit_cancel")]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_sport_type_keyboard():
    """Клавиатура для выбора вида спорта"""
    keyboard = [
        [InlineKeyboardButton("🥊 MMA", callback_data="sport_MMA")],
        [InlineKeyboardButton("🥊 Бокс", callback_data="sport_Boxing")],
        [InlineKeyboardButton("🥊 БЖЖ", callback_data="sport_BJJ")],
        [InlineKeyboardButton("🥊 Кикбоксинг", callback_data="sport_Kickboxing")],
        [InlineKeyboardButton("🥊 Тайский бокс", callback_data="sport_MuayThai")],
        [InlineKeyboardButton("🔙 Назад", callback_data="edit_cancel")]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_status_keyboard():
    """Клавиатура для выбора статуса"""
    keyboard = [
        [InlineKeyboardButton("✅ Активировать", callback_data="status_active")],
        [InlineKeyboardButton("⛔ Деактивировать", callback_data="status_inactive")],
        [InlineKeyboardButton("🔙 Назад", callback_data="edit_cancel")]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_after_edit_keyboard(athlete_id):
    """Клавиатура после успешного редактирования"""
    keyboard = [
        [InlineKeyboardButton("✏️ Продолжить редактирование",
                              callback_data=f"edit_athlete_{athlete_id}")],
        [InlineKeyboardButton("📋 Вернуться к списку",
                              callback_data="back_to_list")],
        [InlineKeyboardButton("🔙 В меню",
                              callback_data="back_to_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)