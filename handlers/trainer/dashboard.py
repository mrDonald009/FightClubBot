import logging
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

logger = logging.getLogger(__name__)


def show_trainer_dashboard(bot, db, message):
    """Показывает панель управления тренера"""
    try:
        dashboard_text = """👨‍🏫 *ПАНЕЛЬ ТРЕНЕРА*

*Доступные функции:*
• Сканирование QR-кодов
• Просмотр групп и участников  
• Отметка посещений
• Управление расписанием

👇 Выберите действие:"""

        keyboard = InlineKeyboardMarkup(row_width=1)

        buttons = [
            ("📱 Сканер QR-кодов", "trainer_qr_scanner"),
            ("👥 Мои группы", "trainer_groups"),
            ("✅ Отметить посещения", "trainer_attendance"),
            ("📅 Расписание", "trainer_schedule"),
            ("📊 Статистика", "trainer_analytics"),
            ("🔙 Главное меню", "return_to_main_menu")
        ]

        for text, callback in buttons:
            keyboard.add(InlineKeyboardButton(text, callback_data=callback))

        bot.send_message(
            message.chat.id,
            dashboard_text,
            parse_mode='Markdown',
            reply_markup=keyboard
        )

    except Exception as e:
        logger.error(f"Ошибка показа панели тренера: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка загрузки панели тренера")