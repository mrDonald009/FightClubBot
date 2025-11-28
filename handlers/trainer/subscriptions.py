import logging
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from database import Database

logger = logging.getLogger(__name__)

def show_subscription_management(bot, db, message):
    """Панель управления абонементами для тренера"""
    try:
        text = """👨‍🏫 *УПРАВЛЕНИЕ АБОНЕМЕНТАМИ*

💡 *Доступные действия:*
• Добавить новый абонемент
• Продлить существующий
• Просмотр статистики
• Сканирование QR-кодов

👇 Выберите действие:"""

        keyboard = InlineKeyboardMarkup(row_width=1)
        buttons = [
            ("➕ Добавить абонемент", "trainer_add_subscription"),
            ("🔄 Продлить абонемент", "trainer_extend_subscription"),
            ("📊 Статистика пользователя", "trainer_user_stats"),
            ("📱 Сканер QR-кодов", "trainer_qr_scanner"),
            ("🔙 Назад", "trainer_dashboard")
        ]

        for btn_text, callback in buttons:
            keyboard.add(InlineKeyboardButton(btn_text, callback_data=callback))

        bot.send_message(
            message.chat.id,
            text,
            parse_mode='Markdown',
            reply_markup=keyboard
        )

    except Exception as e:
        logger.error(f"Ошибка показа управления абонементами: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка загрузки панели")

def show_add_subscription_menu(bot, db, message):
    """Меню добавления абонемента"""
    try:
        text = """➕ *ДОБАВЛЕНИЕ АБОНЕМЕНТА*

💎 *Доступные типы абонементов:*

*🥊 МЕСЯЧНЫЙ*
• 12 тренировок
• Срок: 30 дней
• Цена: 6 000 ₽

*🥋 КВАРТАЛЬНЫЙ* 
• 24 тренировки (+2 бонусные)
• Срок: 90 дней  
• Цена: 11 000 ₽

*🏆 ГОДОВОЙ*
• 48 тренировок (+12 бонусных)
• Срок: 365 дней
• Цена: 20 000 ₽

👇 Выберите тип абонемента:"""

        keyboard = InlineKeyboardMarkup(row_width=1)
        buttons = [
            ("🥊 Месячный (12 тр) - 6 000 ₽", "subscription_monthly"),
            ("🥋 Квартальный (24+2 тр) - 11 000 ₽", "subscription_quarterly"),
            ("🏆 Годовой (48+12 тр) - 20 000 ₽", "subscription_yearly"),
            ("🔙 Назад", "trainer_subscriptions")
        ]

        for btn_text, callback in buttons:
            keyboard.add(InlineKeyboardButton(btn_text, callback_data=callback))

        bot.send_message(
            message.chat.id,
            text,
            parse_mode='Markdown',
            reply_markup=keyboard
        )

    except Exception as e:
        logger.error(f"Ошибка показа меню добавления абонемента: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка загрузки меню")