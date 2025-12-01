from sqlalchemy.exc import SQLAlchemyError
from datetime import datetime
from .models import Session, User, AthleteInfo, Training, Payment


def create_user(session, telegram_id, username, first_name, role='athlete', sport_type='MMA', **kwargs):
    """Создать нового пользователя"""
    try:
        user = User(
            telegram_id=telegram_id,
            username=username,
            first_name=first_name,
            role=role,
            sport_type=sport_type,
            **kwargs
        )
        session.add(user)
        session.commit()
        return user
    except SQLAlchemyError as e:
        session.rollback()
        raise e


def get_user_by_telegram_id(session, telegram_id):
    """Получить пользователя по Telegram ID"""
    return session.query(User).filter_by(telegram_id=telegram_id).first()


def create_athlete_info(session, user_id, medical_notes, age_group, subscription_type, **kwargs):
    """Создать информацию о спортсмене"""
    try:
        athlete_info = AthleteInfo(
            user_id=user_id,
            medical_notes=medical_notes,
            age_group=age_group,
            subscription_type=subscription_type,
            **kwargs
        )
        session.add(athlete_info)
        session.commit()
        return athlete_info
    except SQLAlchemyError as e:
        session.rollback()
        raise e


def get_coach_athletes(session, coach_telegram_id):
    """Получить спортсменов тренера"""
    coach = get_user_by_telegram_id(session, coach_telegram_id)
    if not coach or coach.role != 'coach':
        return []

    # Находим спортсменов, у которых есть тренировки с этим тренером
    athletes = session.query(User).join(
        Training, User.id == Training.athlete_id
    ).filter(
        Training.coach_id == coach.id
    ).distinct().all()

    return athletes


def create_training(session, athlete_id, coach_id, training_date, training_time, notes=None):
    """Создать тренировку"""
    try:
        training = Training(
            athlete_id=athlete_id,
            coach_id=coach_id,
            training_date=training_date,
            training_time=training_time,
            notes=notes
        )
        session.add(training)
        session.commit()
        return training
    except SQLAlchemyError as e:
        session.rollback()
        raise e


def get_athlete_trainings(session, athlete_id):
    """Получить тренировки спортсмена"""
    return session.query(Training).filter_by(athlete_id=athlete_id).order_by(
        Training.training_date.desc(), Training.training_time.desc()
    ).all()


def get_coach_trainings(session, coach_id):
    """Получить тренировки тренера"""
    return session.query(Training).filter_by(coach_id=coach_id).order_by(
        Training.training_date, Training.training_time
    ).all()


def mark_attendance(session, training_id, attended=True):
    """Отметить посещение тренировки"""
    try:
        training = session.query(Training).filter_by(id=training_id).first()
        if training:
            training.attendance = attended
            session.commit()
            return True
        return False
    except SQLAlchemyError:
        session.rollback()
        return False


def create_payment(session, user_id, amount, description=None, payment_method='cash'):
    """Создать платеж"""
    try:
        payment = Payment(
            user_id=user_id,
            amount=amount,
            description=description,
            payment_method=payment_method
        )
        session.add(payment)
        session.commit()
        return payment
    except SQLAlchemyError as e:
        session.rollback()
        raise e


def get_user_payments(session, user_id):
    """Получить платежи пользователя"""
    return session.query(Payment).filter_by(user_id=user_id).order_by(
        Payment.payment_date.desc()
    ).all()


def count_coach_athletes(session, coach_telegram_id):
    """Подсчитать количество спортсменов у тренера"""
    coach = get_user_by_telegram_id(session, coach_telegram_id)
    if not coach or coach.role != 'coach':
        return 0

    return len(get_coach_athletes(session, coach_telegram_id))


def get_athlete_info(session, user_id):
    """Получить дополнительную информацию о спортсмене"""
    return session.query(AthleteInfo).filter_by(user_id=user_id).first()


def update_user(session, user_id, **kwargs):
    """Обновить данные пользователя"""
    try:
        user = session.query(User).filter_by(id=user_id).first()
        if not user:
            return None

        for key, value in kwargs.items():
            if hasattr(user, key):
                setattr(user, key, value)

        session.commit()
        return user
    except SQLAlchemyError as e:
        session.rollback()
        raise e


def update_athlete_info(session, user_id, **kwargs):
    """Обновить информацию о спортсмене"""
    try:
        athlete_info = session.query(AthleteInfo).filter_by(user_id=user_id).first()

        if not athlete_info:
            # Если записи нет, создаем новую
            athlete_info = AthleteInfo(user_id=user_id, **kwargs)
            session.add(athlete_info)
        else:
            # Обновляем существующую запись
            for key, value in kwargs.items():
                if hasattr(athlete_info, key):
                    setattr(athlete_info, key, value)

        session.commit()
        return athlete_info
    except SQLAlchemyError as e:
        session.rollback()
        raise e


def get_all_athletes(session):
    """Получить всех спортсменов"""
    return session.query(User).filter_by(role='athlete').order_by(
        User.first_name
    ).all()


def get_athlete_by_id(session, athlete_id):
    """Получить спортсмена по ID"""
    return session.query(User).filter_by(id=athlete_id, role='athlete').first()


def delete_user(session, user_id):
    """Удалить пользователя"""
    try:
        user = session.query(User).filter_by(id=user_id).first()
        if user:
            session.delete(user)
            session.commit()
            return True
        return False
    except SQLAlchemyError:
        session.rollback()
        return False