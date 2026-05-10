from sqlalchemy.orm import Session
from database.models import Athlete, Attendance, Subscription, Training
from utils.subscription_checker import SubscriptionChecker
from utils.training_manager import TrainingManager
from utils.time_utils import now_moscow, training_end_time

from .global_freeze import is_training_in_global_freeze
from .training_slots import TRAINING_FORMAT_INDIVIDUAL


def _deduct_individual_subscription(
    session: Session, subscription: Subscription, athlete: Athlete, now
) -> bool:
    """Списание единственной индивидуальной тренировки после окончания слота (как разовая по расписанию)."""
    if not subscription.start_date or not subscription.end_date:
        return False
    sport = subscription.sport_type or athlete.sport_type
    if not sport or not athlete.age_group:
        return False
    training_row = (
        session.query(Training)
        .filter(
            Training.training_format == TRAINING_FORMAT_INDIVIDUAL,
            Training.sport_type == sport,
            Training.age_group == athlete.age_group,
            Training.training_date == subscription.start_date,
            Training.is_cancelled.is_(False),
        )
        .first()
    )
    if not training_row:
        return False
    t_end = training_end_time(training_row.training_date)
    if now < t_end:
        return False
    if is_training_in_global_freeze(session, training_row.training_date):
        return False
    if training_row.training_date < subscription.start_date or t_end > subscription.end_date:
        return False
    existing_attendance = (
        session.query(Attendance)
        .filter_by(
            athlete_id=athlete.id,
            training_id=training_row.id,
            subscription_id=subscription.id,
        )
        .first()
    )
    if existing_attendance or subscription.trainings_remaining <= 0:
        return False
    session.add(
        Attendance(
            athlete_id=athlete.id,
            training_id=training_row.id,
            subscription_id=subscription.id,
            attended=False,
            marked_by=None,
            created_at=now_moscow(),
        )
    )
    subscription.trainings_remaining -= 1
    return True


def auto_deduct_daily_trainings(session: Session):
    """
    Автоматически списать тренировки для всех активных абонементов в тренировочные дни.
    Вызывается ежедневно для списания тренировок по расписанию.
    """
    now = now_moscow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    
    # Получаем все активные абонементы (исключая замороженные)
    active_subscriptions = session.query(Subscription).filter(
        Subscription.is_active == True,
        Subscription.is_frozen == False,  # Пропускаем замороженные абонементы
        Subscription.end_date >= today_start
    ).all()
    
    deducted_count = 0
    
    for subscription in active_subscriptions:
        athlete = session.query(Athlete).filter_by(id=subscription.athlete_id).first()
        if not athlete or not athlete.sport_type or not athlete.age_group:
            continue

        # Проверяем статус абонемента
        status = SubscriptionChecker.get_subscription_status(subscription)
        if status != "active":
            continue

        if subscription.subscription_type == "individual":
            if _deduct_individual_subscription(session, subscription, athlete, now):
                deducted_count += 1
            continue

        schedule = TrainingManager.TRAINING_SCHEDULE.get(athlete.sport_type, {}).get(athlete.age_group)
        if not schedule:
            continue
        
        days = schedule['days']
        
        # Проверяем, сегодня ли тренировочный день
        if now.weekday() not in days:
            continue
        
        # Создаем datetime для сегодняшней тренировки
        hour, minute = TrainingManager.get_hour_minute_for_weekday(schedule, now.weekday())
        training_datetime = today_start.replace(hour=hour, minute=minute, second=0, microsecond=0)
        # В период массовой заморозки списание не выполняем
        if is_training_in_global_freeze(session, training_datetime):
            continue
        
        # Время окончания тренировки = время начала + 1.5 часа
        training_end_datetime = training_end_time(training_datetime)
        
        # Важно: списание происходит только после завершения тренировки (через 1.5 часа после начала)
        # Если тренировка еще не завершилась, пропускаем
        if now < training_end_datetime:
            continue
        
        # Проверяем, что тренировка в пределах действия абонемента (по datetime)
        if not subscription.start_date or not subscription.end_date:
            continue
        # Тренировка должна начинаться не раньше start_date и заканчиваться не позже end_date.
        if training_datetime < subscription.start_date or training_end_datetime > subscription.end_date:
            continue
        
        # Проверяем, есть ли уже запись о посещении на эту тренировку
        existing_training = session.query(Training).filter_by(
            sport_type=athlete.sport_type,
            age_group=athlete.age_group,
            training_date=training_datetime,
            is_cancelled=False
        ).first()
        
        if not existing_training:
            # Создаем тренировку
            coach_id = athlete.created_by
            existing_training = Training(
                sport_type=athlete.sport_type,
                age_group=athlete.age_group,
                training_date=training_datetime,
                is_cancelled=False,
                coach_id=coach_id
            )
            session.add(existing_training)
            session.flush()
        
        # Проверяем, не списана ли уже тренировка
        existing_attendance = session.query(Attendance).filter_by(
            athlete_id=athlete.id,
            training_id=existing_training.id,
            subscription_id=subscription.id
        ).first()
        
        if not existing_attendance and subscription.trainings_remaining > 0:
            # Создаем запись о посещении с attended=False (неиспользовано по умолчанию)
            attendance = Attendance(
                athlete_id=athlete.id,
                training_id=existing_training.id,
                subscription_id=subscription.id,
                attended=False,  # По умолчанию неиспользовано
                marked_by=None,  # Автоматическое списание
                created_at=now_moscow()
            )
            session.add(attendance)
            
            # Списываем тренировку
            subscription.trainings_remaining -= 1
            deducted_count += 1
    
    if deducted_count > 0:
        session.commit()
    
    return deducted_count
