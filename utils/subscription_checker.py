import logging
from datetime import datetime, timedelta
from database.models import Session, Subscription, Athlete
from utils.subscription_resolve import active_subscriptions_all
from utils.time_utils import now_moscow

logger = logging.getLogger(__name__)


class SubscriptionChecker:
    """Проверяет и обновляет статусы абонементов"""

    @staticmethod
    def check_and_update_subscriptions():
        """Проверить все абонементы и обновить статусы"""
        session = Session()
        try:
            print("🔄 Проверка статусов абонементов...")

            from database.db_utils import expire_stale_subscription_freezes
            expire_stale_subscription_freezes(session, commit=False)

            # Находим активные абонементы с истекшим сроком или с 0 тренировок
            expired_subscriptions = session.query(Subscription).filter(
                Subscription.is_active == True
            ).all()

            updated_count = 0
            for subscription in expired_subscriptions:
                should_deactivate = False
                reason = ""
                
                # Проверяем дату окончания
                if subscription.end_date and subscription.end_date < now_moscow():
                    should_deactivate = True
                    reason = "истек срок действия"
                
                # Проверяем количество оставшихся тренировок
                if subscription.trainings_remaining is not None and subscription.trainings_remaining <= 0:
                    should_deactivate = True
                    reason = "закончились тренировки" if not reason else f"{reason} и закончились тренировки"
                
                if should_deactivate:
                    logger.info(f"🔴 Абонемент #{subscription.id} деактивирован: {reason}")
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
            current_time = now_moscow()

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
        """Проверить и обновить все активные абонементы спортсмена."""
        session = Session()
        try:
            athlete = session.query(Athlete).filter_by(id=athlete_id).first()
            if not athlete:
                return {"updated": False, "message": "Спортсмен не найден"}

            subs = active_subscriptions_all(athlete)
            if not subs:
                return {"updated": False, "message": "Нет активных абонементов"}

            updated_any = False
            messages = []
            for subscription in subs:
                old_status = subscription.is_active
                if not subscription.is_active:
                    continue
                should_deactivate = False
                reason = ""

                if subscription.end_date and subscription.end_date < now_moscow():
                    should_deactivate = True
                    reason = "истек срок действия"

                if (
                    subscription.trainings_remaining is not None
                    and subscription.trainings_remaining <= 0
                ):
                    should_deactivate = True
                    reason = (
                        "закончились тренировки"
                        if not reason
                        else f"{reason} и закончились тренировки"
                    )

                if should_deactivate:
                    subscription.is_active = False
                    updated_any = True
                    end_s = (
                        subscription.end_date.strftime("%d.%m.%Y")
                        if subscription.end_date
                        else "—"
                    )
                    messages.append(f"Абонемент #{subscription.id} деактивирован ({reason}, до {end_s})")

            if updated_any:
                session.commit()
                return {
                    "updated": True,
                    "message": "; ".join(messages),
                    "old_status": True,
                    "new_status": False,
                }

            return {"updated": False, "message": "Статус актуален"}

        except Exception as e:
            logger.error(f"❌ Ошибка проверки спортсмена {athlete_id}: {e}")
            session.rollback()
            return {"updated": False, "message": f"Ошибка: {str(e)}"}
        finally:
            session.close()