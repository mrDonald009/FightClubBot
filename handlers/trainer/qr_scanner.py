import logging
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from database import Database

logger = logging.getLogger(__name__)


class QRScanner:
    def __init__(self, bot, db):
        self.bot = bot
        self.db = db

    def handle_qr_scan(self, message, qr_data):
        """Обрабатывает отсканированный QR-код"""
        try:
            success, result = self.verify_qr_code(qr_data, message.from_user.id)

            if success:
                user_data = result
                self._grant_access(message, user_data)
            else:
                self._deny_access(message, result)

        except Exception as e:
            logger.error(f"Ошибка обработки QR-кода: {e}")
            self.bot.send_message(message.chat.id, "❌ Ошибка обработки QR-кода")

    def _grant_access(self, message, user_data):
        """Предоставляет доступ и логирует вход"""
        keyboard = InlineKeyboardMarkup()
        keyboard.add(InlineKeyboardButton("✅ Зарегистрировать выход",
                                          callback_data=f"exit_{user_data['telegram_id']}"))

        access_text = f"""✅ *ДОСТУП РАЗРЕШЕН*

👤 *Участник:* {user_data['full_name']}
🆔 *ID:* {user_data['telegram_id']}
🕒 *Время:* {datetime.datetime.now().strftime('%H:%M')}

💡 *Статус:* Активный абонемент"""

        # Логируем успешный вход
        self._log_access(user_data['telegram_id'], user_data['qr_token'],
                         'entry', 'granted', message.from_user.id)

        self.bot.send_message(
            message.chat.id,
            access_text,
            parse_mode='Markdown',
            reply_markup=keyboard
        )

    def _deny_access(self, message, reason):
        """Отказывает в доступе"""
        deny_text = f"""❌ *ДОСТУП ЗАПРЕЩЕН*

*Причина:* {reason}

💡 *Рекомендации:*
• Проверить актуальность абонемента
• Обратиться к администратору"""

        self.bot.send_message(
            message.chat.id,
            deny_text,
            parse_mode='Markdown'
        )

    def _log_access(self, user_id, qr_token, access_type, status, verified_by):
        """Логирует попытку доступа"""
        with self.db.get_connection() as conn:
            conn.execute('''
                INSERT INTO access_logs 
                (user_id, qr_token, access_type, status, verified_by) 
                VALUES (?, ?, ?, ?, ?)
            ''', (user_id, qr_token, access_type, status, verified_by))