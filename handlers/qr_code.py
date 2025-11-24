import logging
import datetime
from keyboards import back_to_menu_keyboard
from qr_generator import QRGenerator

logger = logging.getLogger(__name__)


def show_qr_code(bot, db, message):
    """Показывает персональный QR-код пользователя"""
    try:
        generator = QRGenerator(db)
        qr_image, qr_token = generator.generate_user_qr(message.from_user.id)

        if qr_image:
            qr_info = generator.get_user_qr_info(message.from_user.id)

            if qr_info:
                expires_date = qr_info['expires_at'][:10] if qr_info['expires_at'] else "Неизвестно"
                last_used = qr_info['last_used'][:16] if qr_info['last_used'] else "Никогда"
            else:
                expires_date = "Неизвестно"
                last_used = "Никогда"

            qr_text = f"""🎫 *ВАШ ПЕРСОНАЛЬНЫЙ QR-КОД*

💡 *Как использовать:*
• Покажите этот код на входе в клуб
• Код обновляется каждые 30 дней
• Действует только при активном абонементе

📊 *Информация о коде:*
• Создан: {datetime.datetime.now().strftime('%d.%m.%Y')}
• Действует до: {expires_date}
• Последнее использование: {last_used}

⚠️ *Не передавайте код третьим лицам!*"""

            bot.send_photo(
                message.chat.id,
                qr_image,
                caption=qr_text,
                parse_mode='Markdown',
                reply_markup=back_to_menu_keyboard()
            )
        else:
            error_text = """❌ *Не удалось сгенерировать QR-код*

Возможные причины:
• Ошибка базы данных
• Проблема с генерацией изображения

Пожалуйста, попробуйте позже или обратитесь к администратору."""

            bot.send_message(
                message.chat.id,
                error_text,
                parse_mode='Markdown',
                reply_markup=back_to_menu_keyboard()
            )

    except Exception as e:
        logger.error(f"Ошибка показа QR-кода: {e}")
        bot.send_message(
            message.chat.id,
            "❌ Ошибка загрузки QR-кода. Попробуйте позже.",
            reply_markup=back_to_menu_keyboard()
        )