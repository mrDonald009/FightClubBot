from telegram import KeyboardButton, ReplyKeyboardMarkup

# Кнопки меню администратора (прерывание диалогов, регистрация handlers)
ADMIN_MENU_BUTTONS = [
    "🌍 Массовая заморозка",
    "👥 Тренеры",
    "📊 Общая статистика",
    "💰 Финансы",
    "⚙️ Настройки",
]


def get_admin_main_menu():
    """Главное меню администратора клуба."""
    keyboard = [
        [KeyboardButton("🌍 Массовая заморозка")],
        [KeyboardButton("👥 Тренеры"), KeyboardButton("📊 Общая статистика")],
        [KeyboardButton("💰 Финансы"), KeyboardButton("⚙️ Настройки")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
