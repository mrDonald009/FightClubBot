from datetime import timedelta
from sqlalchemy.orm import Session
from database.models import Athlete, Attendance, Subscription, Training
from utils.subscription_checker import SubscriptionChecker
from utils.subscription_resolve import (
    active_subscriptions_all,
    subscription_for_coach_sport,
)
from utils.time_utils import now_moscow
from utils.attendance_display import count_implicit_absent_slots

from .users import get_coach_by_sport_type


def get_athlete_card_info(session: Session, athlete_id: int, preferred_sport_type: str = None):
    """
    Получить информацию для карточки спортсмена.

    preferred_sport_type: для тренера — абонемент по его виду спорта; иначе первый активный.
    """
    athlete = session.query(Athlete).filter_by(id=athlete_id).first()

    if not athlete:
        return None

    current_time = now_moscow()
    changed = False
    for sub in active_subscriptions_all(athlete):
        if sub.end_date and sub.end_date < current_time:
            sub.is_active = False
            changed = True
    if changed:
        session.commit()

    subscription = subscription_for_coach_sport(athlete, preferred_sport_type)

    coach = None
    if subscription and subscription.sport_type:
        coach = get_coach_by_sport_type(session, subscription.sport_type)
    elif athlete.sport_type:
        coach = get_coach_by_sport_type(session, athlete.sport_type)

    if not coach:
        coach = athlete.coach

    sport_type_for_stats = (
        subscription.sport_type if subscription and subscription.sport_type else athlete.sport_type
    )
    
    month_ago = now_moscow() - timedelta(days=30)

    total_trainings = 0
    if sport_type_for_stats:
        total_trainings = session.query(Training).filter(
            Training.sport_type == sport_type_for_stats,
            Training.age_group == athlete.age_group,
            Training.training_date >= month_ago,
            Training.is_cancelled == False
        ).count()

    attended_trainings = session.query(Attendance).filter(
        Attendance.athlete_id == athlete_id,
        Attendance.attended == True,
        Attendance.training.has(Training.training_date >= month_ago)
    ).count()

    missed_trainings = session.query(Attendance).filter(
        Attendance.athlete_id == athlete_id,
        Attendance.attended == False,
        Attendance.training.has(Training.training_date >= month_ago),
        Attendance.was_restored == False
    ).count()

    if sport_type_for_stats and athlete.age_group:
        missed_trainings += count_implicit_absent_slots(
            session,
            athlete_id,
            sport_type_for_stats,
            athlete.age_group,
            month_ago,
            current_time,
            now=current_time,
        )

    attendance_rate = round((attended_trainings / total_trainings * 100), 1) if total_trainings > 0 else 0

    # Форматируем медицинскую информацию
    medical_display = athlete.medical_info
    if medical_display and len(medical_display) > 100:
        medical_display = medical_display[:97] + "..."

    # Получаем отформатированный статус абонемента
    status_display = SubscriptionChecker.format_subscription_status(subscription)

    return {
        "athlete": athlete,
        "subscription": subscription,
        "coach": coach,
        "stats": {
            "total_trainings": total_trainings,
            "attended_trainings": attended_trainings,
            "missed_trainings": missed_trainings,
            "attendance_rate": attendance_rate,
            # В новой схеме связь с Telegram хранится прямо в athletes.telegram_id
            "has_telegram": bool(getattr(athlete, "telegram_id", None))
        },
        "medical_display": medical_display,
        "age_group_display": "Детская" if athlete.age_group == "children" else "Взрослая",
        "status_display": status_display  # Добавляем отформатированный статус
    }
