from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

def back_to_menu_keyboard():
    """Клавиатура с кнопкой возврата в меню"""
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("🔙 Главное меню", callback_data="return_to_main_menu"))
    return keyboard