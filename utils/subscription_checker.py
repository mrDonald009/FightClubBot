import logging
from datetime import datetime, timedelta
from database.models import Session, Subscription, Athlete

logger = logging.getLogger(__name__)


class SubscriptionChecker:
    """Проверяет и обновляет статусы абонементов"""

    @staticmethod
    def check_and_update_subscriptions():
        """Проверить все абонементы и обновить статусы"""
        session = Session()
        try:
            print("🔄 Проверка статусов абонементов...")

            # Находим активные абонементы с истекшим сроком
            expired_subscriptions = session.query(Subscription).filter(
                Subscription.is_active == True,
                Subscription.end_date < datetime.utcnow()
            ).all()

            updated_count = 0
            for subscription in expired_subscriptions:
                logger.info(f"🔴 Абонемент #{subscription.id} истек, деактивируем")
                subscription.is_active = False
                updated_count += 1

            if updated_count > 0:
                session.commit()
                print(f"✅ Обновлено {updated_count} абонементов")
            else:
                print("✅ Все абонементы актуальны")

            return updated_count

        except Exception as e:
            logger.error(f"❌ Ошибка при проверке абонементов: {e}")
            session.rollback()
            return 0
        finally:
            session.close()

    @staticmethod
    def get_subscription_status(subscription):
        """Получить текущий статус абонемента с учетом даты"""
        if not subscription:
            return "no_subscription"

        if not subscription.is_active:
            return "inactive"

        # Проверяем дату окончания
        if subscription.end_date:
            current_time = datetime.utcnow()

            # Если абонемент истек
            if subscription.end_date < current_time:
                return "expired"

            # Если осталось меньше 7 дней
            days_left = (subscription.end_date - current_time).days
            if days_left <= 7:
                return "expiring_soon"

            # Если все в порядке
            return "active"

        # Если нет даты окончания (бессрочный)
        return "active"

    @staticmethod
    def format_subscription_status(subscription):
        """Отформатировать статус для отображения"""
        status = SubscriptionChecker.get_subscription_status(subscription)

        status_map = {
            "no_subscription": "❌ Нет абонемента",
            "inactive": "❌ Неактивен",
            "expired": "🔴 Истек",
            "expiring_soon": "🟡 Истекает скоро",
            "active": "✅ Активен"
        }

        return status_map.get(status, "❓ Неизвестно")

    @staticmethod
    def check_specific_athlete(athlete_id):
        """Проверить и обновить абонемент конкретного спортсмена"""
        session = Session()
        try:
            athlete = session.query(Athlete).filter_by(id=athlete_id).first()
            if not athlete or not athlete.current_subscription:
                return {"updated": False, "message": "Спортсмен или абонемент не найден"}

            subscription = athlete.current_subscription
            old_status = subscription.is_active

            # Проверяем дату окончания
            if subscription.is_active and subscription.end_date:
                if subscription.end_date < datetime.utcnow():
                    subscription.is_active = False
                    session.commit()

                    return {
                        "updated": True,
                        "message": f"Абонемент #{subscription.id} деактивирован (истек {subscription.end_date.strftime('%d.%m.%Y')})",
                        "old_status": old_status,
                        "new_status": subscription.is_active
                    }

            return {"updated": False, "message": "Статус актуален"}

        except Exception as e:
            logger.error(f"❌ Ошибка проверки спортсмена {athlete_id}: {e}")
            session.rollback()
            return {"updated": False, "message": f"Ошибка: {str(e)}"}
        finally:
            session.close()