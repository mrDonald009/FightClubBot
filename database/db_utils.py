from sqlalchemy.orm import Session
from database.models import User, Athlete, Subscription
from datetime import datetime, timedelta

def get_user_by_telegram_id(session: Session, telegram_id: int):
    """Получить пользователя по telegram_id"""
    return session.query(User).filter_by(telegram_id=telegram_id).first()

def create_user(session: Session, telegram_id: int, username: str, first_name: str, role: str = "athlete", sport_type: str = None):
    """Создать нового пользователя"""
    user = User(
        telegram_id=telegram_id,
        username=username,
        first_name=first_name,
        role=role,
        sport_type=sport_type
    )
    session.add(user)
    session.commit()
    return user

def create_athlete(session: Session, user_id: int, full_name: str, phone: str, medical_info: str,
                   sport_type: str, age_group: str, created_by: int, height: int = None, weight: int = None):
    """Создать спортсмена"""
    athlete = Athlete(
        user_id=user_id,
        full_name=full_name,
        phone=phone,
        height=height,
        weight=weight,
        medical_info=medical_info,
        sport_type=sport_type,
        age_group=age_group,
        created_by=created_by
    )
    session.add(athlete)
    session.commit()
    return athlete

def create_subscription(session: Session, athlete_id: int, subscription_type: str):
    """Создать абонемент для спортсмена"""
    if subscription_type == "monthly":
        trainings_total = 12
        end_date = datetime.utcnow() + timedelta(days=30)
    else:  # single
        trainings_total = 1
        end_date = datetime.utcnow() + timedelta(days=1)

    subscription = Subscription(
        athlete_id=athlete_id,
        subscription_type=subscription_type,
        end_date=end_date,
        trainings_total=trainings_total,
        trainings_remaining=trainings_total
    )
    session.add(subscription)
    session.commit()
    return subscription