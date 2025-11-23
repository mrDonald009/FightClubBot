import logging
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

logger = logging.getLogger(__name__)

def send_contacts_info(bot, config, message):
    """Показ контактов с инлайн-кнопками"""
    contacts_text = f"""
📞 *Контакты клуба:*

📍 *Адрес:*
{config.GYM_ADDRESS}

📱 *Телефон:*
{config.GYM_PHONE}

🕒 *Режим работы:*
Пн-Пт: 17:00 - 21:00
Сб: 10:00 - 14:00
Вс: Выходной

💪 *Первая тренировка - БЕСПЛАТНО!*"""

    keyboard = InlineKeyboardMarkup()
    keyboard.add(
        InlineKeyboardButton("💬 Telegram", url="https://t.me/Zimin03"),
        InlineKeyboardButton("📱 WhatsApp", url="https://wa.me/79251506975")
    )
    keyboard.add(
        InlineKeyboardButton("📷 Instagram", url="https://www.instagram.com/luber.fight.club"),
        InlineKeyboardButton("🗺️ На картах",
                           url="https://yandex.ru/maps/?ll=37.884696,55.700928&z=17&pt=37.884696,55.700928,pm2grm")
    )
    keyboard.add(
        InlineKeyboardButton("🔙 Главное меню", callback_data="return_to_main_menu")
    )

    # Всегда отправляем новое сообщение для сохранения истории
    bot.send_message(
        message.chat.id,
        contacts_text,
        parse_mode='Markdown',
        reply_markup=keyboard
    )