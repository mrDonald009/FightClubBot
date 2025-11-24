import qrcode
import io
import secrets
import datetime
import logging
from database import Database

logger = logging.getLogger(__name__)


class QRGenerator:
    def __init__(self, db: Database):
        self.db = db

    def generate_user_qr(self, telegram_id: int):
        """Генерирует персональный QR-код для пользователя"""
        try:
            # Создаем уникальный токен
            qr_token = f"FC_{secrets.token_hex(16)}"
            expires_at = datetime.datetime.now() + datetime.timedelta(days=30)

            # Сохраняем в базу
            success = self.db.create_user_qr_code(telegram_id, qr_token, expires_at)

            if not success:
                return None, None

            # Генерируем QR-код изображение
            qr = qrcode.QRCode(
                version=1,
                error_correction=qrcode.constants.ERROR_CORRECT_L,
                box_size=10,
                border=4,
            )

            # Формируем данные для QR-кода
            qr_data = f"{qr_token}|{telegram_id}"
            qr.add_data(qr_data)
            qr.make(fit=True)

            # Создаем изображение
            img = qr.make_image(fill_color="black", back_color="white")

            # Конвертируем в bytes для отправки в Telegram
            bio = io.BytesIO()
            img.save(bio, 'PNG')
            bio.seek(0)

            return bio, qr_token

        except Exception as e:
            logger.error(f"Ошибка генерации QR-кода: {e}")
            return None, None

    def verify_qr_code(self, qr_token: str, verifier_id: int = None):
        """Проверяет валидность QR-кода"""
        try:
            success, result = self.db.verify_qr_token(qr_token)

            if success:
                user_data = result

                # Логируем успешную попытку доступа
                self.db.log_access_attempt(
                    user_id=user_data['telegram_id'],
                    qr_token=qr_token,
                    access_type='entry',
                    status='granted',
                    verified_by=verifier_id
                )

                return True, user_data
            else:
                # Логируем неудачную попытку доступа
                if isinstance(result, dict):
                    self.db.log_access_attempt(
                        user_id=result.get('telegram_id'),
                        qr_token=qr_token,
                        access_type='entry',
                        status='denied',
                        verified_by=verifier_id,
                        reason=result
                    )
                return False, result

        except Exception as e:
            logger.error(f"Ошибка проверки QR-кода: {e}")
            return False, "❌ Ошибка проверки QR-кода"

    def get_user_qr_info(self, telegram_id: int):
        """Получает информацию о QR-коде пользователя"""
        try:
            qr_data = self.db.get_active_qr_code(telegram_id)

            if qr_data:
                return {
                    'qr_token': qr_data['qr_token'],
                    'created_at': qr_data['created_at'],
                    'expires_at': qr_data['expires_at'],
                    'last_used': qr_data['last_used'],
                    'is_active': bool(qr_data['is_active'])
                }
            else:
                return None

        except Exception as e:
            logger.error(f"Ошибка получения информации о QR-коде: {e}")
            return None

    def revoke_qr_code(self, telegram_id: int):
        """Отзывает QR-код пользователя"""
        try:
            with self.db.get_connection() as conn:
                conn.execute(
                    'UPDATE user_qr_codes SET is_active = 0 WHERE user_id = ?',
                    (telegram_id,)
                )
                return True
        except Exception as e:
            logger.error(f"Ошибка отзыва QR-кода: {e}")
            return False