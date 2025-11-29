from database.models import Session, User, Athlete, Subscription, Training
from database.db_utils import create_user, create_athlete, create_subscription
from datetime import datetime, timedelta
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def init_database():
    """Инициализация базы данных с тестовыми данными"""
    session = Session()

    try:
        # Создаем администратора (замените на ваш Telegram ID)
        admin = create_user(
            session=session,
            telegram_id=123456789,  # ЗАМЕНИТЕ НА ВАШ TELEGRAM ID
            username="admin",
            first_name="Администратор",
            role="admin"
        )

        # Создаем тестового тренера
        coach = create_user(
            session=session,
            telegram_id=26655492,  # ЗАМЕНИТЕ НА TELEGRAM ID ТРЕНЕРА
            username="coach",
            first_name="Тренер",
            role="coach"
        )

        # Создаем тестового спортсмена
        athlete_user = create_user(
            session=session,
            telegram_id=555555555,  # TELEGRAM ID спортсмена
            username="athlete",
            first_name="Спортсмен",
            role="athlete"
        )

        # Создаем запись спортсмена
        athlete = create_athlete(
            session=session,
            user_id=athlete_user.id,
            full_name="Иванов Алексей Петрович",
            phone="+79123456789",
            medical_info="Нет противопоказаний",
            sport_type="MMA",
            age_group="adults",
            created_by=coach.id,
            height=180,
            weight=75
        )

        # Создаем абонемент для спортсмена
        subscription = create_subscription(
            session=session,
            athlete_id=athlete.id,
            subscription_type="monthly"
        )

        # Создаем тестовые тренировки
        training_dates = [
            datetime.now() + timedelta(days=i)
            for i in range(7)
            if (datetime.now() + timedelta(days=i)).weekday() in [0, 2, 4]  # Пн, Ср, Пт
        ]

        for date in training_dates[:3]:  # Создаем 3 ближайшие тренировки
            training = Training(
                sport_type="MMA",
                age_group="adults",
                training_date=date.replace(hour=20, minute=0, second=0)
            )
            session.add(training)

        session.commit()
        logger.info("✅ База данных инициализирована с тестовыми данными")

    except Exception as e:
        logger.error(f"❌ Ошибка инициализации базы данных: {e}")
        session.rollback()
    finally:
        session.close()


if __name__ == "__main__":
    init_database()