from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db_utils.auto_deduct import auto_deduct_daily_trainings
from database.models import Athlete, Attendance, Base, Coach, SportType, Subscription, Training
from utils.training_manager import TrainingManager


pytestmark = pytest.mark.db


def _weekday_anchor(base: datetime, target_weekday: int) -> datetime:
    day = base
    while day.weekday() != target_weekday:
        day += timedelta(days=1)
    return day


def test_auto_deduct_reuses_existing_group_training_on_same_day():
    """
    Регрессия: при смене времени в TRAINING_SCHEDULE не создавать второй group-слот
    в тот же день, если legacy-слот уже существует для этого тренера/группы.
    """
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    sport_type = "MMA"
    age_group = "children"
    schedule = TrainingManager.TRAINING_SCHEDULE[sport_type][age_group]
    weekday = schedule["days"][0]
    expected_hhmm = TrainingManager.get_time_str_for_weekday(schedule, weekday)
    expected_hour, expected_minute = map(int, expected_hhmm.split(":"))

    training_day = _weekday_anchor(datetime(2026, 5, 1), weekday)
    expected_dt = training_day.replace(
        hour=expected_hour, minute=expected_minute, second=0, microsecond=0
    )
    now = expected_dt + timedelta(hours=2)

    legacy_dt = expected_dt + timedelta(minutes=60)
    if legacy_dt.date() != expected_dt.date():
        legacy_dt = expected_dt - timedelta(minutes=60)
    assert legacy_dt != expected_dt

    st = SportType(name=sport_type, display_name=sport_type)
    session.add(st)
    session.flush()
    coach = Coach(telegram_id=99001, sport_type_id=st.id, sport_type=sport_type)
    session.add(coach)
    session.flush()

    athlete = Athlete(
        full_name="Регресс Тест",
        age_group=age_group,
        sport_type=sport_type,
        created_by=coach.id,
    )
    session.add(athlete)
    session.flush()

    subscription = Subscription(
        athlete_id=athlete.id,
        discipline_key="mma_children_group",
        sport_type=sport_type,
        subscription_type="monthly",
        is_active=True,
        is_frozen=False,
        start_date=expected_dt - timedelta(days=14),
        end_date=expected_dt + timedelta(days=30),
        trainings_total=12,
        trainings_remaining=12,
        created_at=expected_dt - timedelta(days=14),
    )
    session.add(subscription)

    legacy_training = Training(
        sport_type=sport_type,
        age_group=age_group,
        training_date=legacy_dt,
        is_cancelled=False,
        coach_id=coach.id,
    )
    session.add(legacy_training)
    session.commit()

    with patch("database.db_utils.auto_deduct.now_moscow", return_value=now), patch(
        "database.db_utils.auto_deduct.is_training_in_global_freeze", return_value=False
    ), patch(
        "database.db_utils.auto_deduct.SubscriptionChecker.get_subscription_status",
        return_value="active",
    ):
        deducted = auto_deduct_daily_trainings(session)

    assert deducted == 1

    day_start = expected_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    day_trainings = (
        session.query(Training)
        .filter(
            Training.sport_type == sport_type,
            Training.age_group == age_group,
            Training.coach_id == coach.id,
            Training.is_cancelled.is_(False),
            Training.training_date >= day_start,
            Training.training_date < day_end,
        )
        .all()
    )
    assert len(day_trainings) == 1
    assert day_trainings[0].id == legacy_training.id

    attendance = session.query(Attendance).filter_by(subscription_id=subscription.id).one()
    assert attendance.training_id == legacy_training.id
    assert attendance.attended is False
    session.close()
