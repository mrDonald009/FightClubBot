from datetime import datetime, timedelta

from sqlalchemy.orm import Session
from database.models import Athlete, Subscription, Training
from utils.discipline_keys import discipline_key_for
from utils.time_utils import now_moscow
from utils.training_manager import TrainingManager


def create_subscription(
    session: Session,
    athlete_id: int,
    subscription_type: str = None,
    sport_type: str = None,
    *,
    discipline_key: str = None,
    subscription_format: str = "group",
    responsible_coach_id: int = None,
    commit: bool = True,
):
    """
    Создать абонемент для спортсмена.
    При commit=False только add+flush (для одной транзакции со спортсменом).

    Неактивная запись с тем же (athlete_id, discipline_key) переиспользуется (без дубликата).
    """
    athlete = session.query(Athlete).filter_by(id=athlete_id).first()
    if not athlete:
        raise ValueError(f"Спортсмен с id={athlete_id} не найден")

    if not sport_type:
        sport_type = athlete.sport_type

    fmt = (subscription_format or "group").strip().lower()
    dk = discipline_key or discipline_key_for(sport_type, format=fmt)
    rc = responsible_coach_id if responsible_coach_id is not None else athlete.created_by

    created_at = now_moscow()

    if subscription_type is None:
        trainings_total = None
        trainings_remaining = None
    elif subscription_type == "monthly":
        trainings_total = 12
        trainings_remaining = 12
    elif subscription_type == "single":
        trainings_total = 1
        trainings_remaining = 1
    elif subscription_type == "individual":
        trainings_total = 1
        trainings_remaining = 1
    else:
        raise ValueError(f"Неизвестный тип абонемента: {subscription_type}")

    # Индивидуальные брони — отдельная строка на каждую тренировку (слот в start_date).
    if subscription_type != "individual":
        existing = (
            session.query(Subscription)
            .filter_by(athlete_id=athlete_id, discipline_key=dk)
            .first()
        )
        if existing:
            if existing.is_active:
                raise ValueError(
                    f"У спортсмена уже есть активный абонемент по направлению {dk}"
                )
            existing.sport_type = sport_type
            existing.subscription_type = subscription_type
            existing.responsible_coach_id = rc
            existing.trainings_total = trainings_total
            existing.trainings_remaining = trainings_remaining
            existing.start_date = None
            existing.end_date = None
            existing.is_active = False
            existing.created_at = created_at
            session.flush()
            if commit:
                session.commit()
            return existing

    subscription = Subscription(
        athlete_id=athlete_id,
        discipline_key=dk,
        responsible_coach_id=rc,
        sport_type=sport_type,
        subscription_type=subscription_type,
        start_date=None,
        end_date=None,
        trainings_total=trainings_total,
        trainings_remaining=trainings_remaining,
        is_active=False,
        created_at=created_at,
    )
    session.add(subscription)
    session.flush()
    if commit:
        session.commit()
    return subscription


def _create_and_deduct_scheduled_trainings(
    session: Session,
    subscription: Subscription,
    athlete: Athlete,
    start_date: datetime,
    end_date: datetime,
):
    """
    Создать тренировки по расписанию для абонемента без списания.
    """
    schedule = TrainingManager.TRAINING_SCHEDULE.get(athlete.sport_type, {}).get(
        athlete.age_group
    )
    if not schedule:
        return

    days = schedule["days"]

    # Получаем тренера спортсмена
    coach_id = athlete.created_by

    # Проходим по всем дням от start_date до end_date
    current_day = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
    end_day = end_date.replace(hour=23, minute=59, second=59, microsecond=999)

    while current_day <= end_day:
        # Проверяем, это ли день тренировки по расписанию
        if current_day.weekday() in days:
            # Создаем datetime с правильным временем
            hour, minute = TrainingManager.get_hour_minute_for_weekday(
                schedule, current_day.weekday()
            )
            training_datetime = current_day.replace(
                hour=hour, minute=minute, second=0, microsecond=0
            )

            # Пропускаем будущие тренировки (после end_date)
            if training_datetime > end_date:
                break

            # Проверяем, есть ли уже такая тренировка
            existing_training = (
                session.query(Training)
                .filter_by(
                    sport_type=athlete.sport_type,
                    age_group=athlete.age_group,
                    training_date=training_datetime,
                    is_cancelled=False,
                )
                .first()
            )

            if not existing_training:
                # Создаем новую тренировку
                training = Training(
                    sport_type=athlete.sport_type,
                    age_group=athlete.age_group,
                    training_date=training_datetime,
                    is_cancelled=False,
                    coach_id=coach_id,
                )
                session.add(training)
                session.flush()
            else:
                training = existing_training

            # Важно: НЕ списываем тренировку при активации.
            # Списание делается по факту (авто-списание в день тренировки или отметка посещения).

        current_day += timedelta(days=1)
