from database.models import Session, Coach, Admin, Athlete, Subscription, Training
from database.db_utils import create_user, create_athlete, create_subscription
from datetime import datetime, timedelta
from utils.time_utils import now_moscow
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

        # Создаем тренера по MMA
        coach_mma = create_user(
            session=session,
            telegram_id=26655492,  # ЗАМЕНИТЕ НА TELEGRAM ID ТРЕНЕРА MMA
            username="coach_mma",
            first_name="Тренер ММА",
            role="coach",
            sport_type="MMA"
        )

        # Создаем тренера по Тайскому Боксу
        coach_thai = create_user(
            session=session,
            telegram_id=555555556,  # TELEGRAM ID тренера по тайскому боксу
            username="coach_thai",
            first_name="Тренер Тайский Бокс",
            role="coach",
            sport_type="Тайский Бокс"
        )

        # Создаем запись спортсмена для тренера MMA
        athlete_mma = create_athlete(
            session=session,
            telegram_id=555555555,  # TELEGRAM ID спортсмена
            full_name="Иванов Алексей Петрович",
            phone="+79123456789",
            medical_info="Нет противопоказаний",
            sport_type="MMA",
            age_group="adults",
            created_by=coach_mma.id,
            height=180,
            weight=75
        )

        # Создаем запись спортсмена для тренера Тайский Бокс
        athlete_thai = create_athlete(
            session=session,
            telegram_id=555555556,  # Другой TELEGRAM ID для второго спортсмена
            full_name="Петров Дмитрий Сергеевич",
            phone="+79123456780",
            medical_info="Нет противопоказаний",
            sport_type="Тайский Бокс",
            age_group="adults",
            created_by=coach_thai.id,
            height=175,
            weight=70
        )

        # Создаем абонементы для спортсменов
        subscription_mma = create_subscription(
            session=session,
            athlete_id=athlete_mma.id,
            subscription_type="monthly"
        )

        subscription_thai = create_subscription(
            session=session,
            athlete_id=athlete_thai.id,
            subscription_type="monthly"
        )

        # Создаем тестовые тренировки
        base_now = now_moscow()
        training_dates = [
            base_now + timedelta(days=i)
            for i in range(7)
            if (base_now + timedelta(days=i)).weekday() in [0, 2, 4]  # Пн, Ср, Пт
        ]

        for date in training_dates[:3]:  # Создаем 3 ближайшие тренировки
            # Тренировка по MMA
            training_mma = Training(
                sport_type="MMA",
                age_group="adults",
                training_date=date.replace(hour=20, minute=0, second=0),
                coach_id=coach_mma.id
            )
            session.add(training_mma)

            # Тренировка по Тайскому Боксу
            training_thai = Training(
                sport_type="Тайский Бокс",
                age_group="adults",
                training_date=date.replace(hour=19, minute=0, second=0),
                coach_id=coach_thai.id
            )
            session.add(training_thai)

        session.commit()
        logger.info("✅ База данных инициализирована с тестовыми данными")
        logger.info(f"✅ Создан тренер MMA: {coach_mma.first_name} (ID: {coach_mma.telegram_id})")
        logger.info(f"✅ Создан тренер Тайский Бокс: {coach_thai.first_name} (ID: {coach_thai.telegram_id})")

    except Exception as e:
        logger.error(f"❌ Ошибка инициализации базы данных: {e}")
        session.rollback()
    finally:
        session.close()


if __name__ == "__main__":
    init_database()