"""Даты абонемента: выравнивание start и end по расписанию и club_settings."""
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db_utils.club_settings import KEY_GROUP_TRAINING_DURATION_MINUTES
from database.db_utils.migrate import migrate_existing_subscription
from database.db_utils.schedule import (
    _align_start_date_to_schedule,
    _calculate_12th_training_date,
)
from database.models import Athlete, Base, ClubSetting, Coach, SportType, Subscription


@pytest.mark.db
def test_align_legacy_children_start_18_00_to_17_00():
    legacy = datetime(2026, 5, 11, 18, 0, 0)
    aligned = _align_start_date_to_schedule(legacy, "MMA", "children")
    assert aligned == datetime(2026, 5, 11, 17, 0, 0)


@pytest.mark.db
def test_monthly_end_from_aligned_start_and_db_duration():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    session.add(
        ClubSetting(key=KEY_GROUP_TRAINING_DURATION_MINUTES, value="90")
    )
    session.commit()

    end = _calculate_12th_training_date(
        datetime(2026, 5, 11, 18, 0, 0),
        "MMA",
        "children",
        session=session,
    )
    assert end == datetime(2026, 6, 5, 18, 30, 0)


@pytest.mark.db
def test_migrate_existing_subscription_aligns_start_and_end():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    st = SportType(name="MMA", display_name="MMA")
    session.add(st)
    session.flush()
    coach = Coach(telegram_id=9100, sport_type_id=st.id, sport_type="MMA")
    session.add(coach)
    session.flush()

    athlete = Athlete(
        full_name="Иванов Владимир Петрович",
        sport_type="MMA",
        age_group="children",
        created_by=coach.id,
    )
    session.add(athlete)
    session.flush()

    sub = Subscription(
        athlete_id=athlete.id,
        discipline_key="MMA:group",
        sport_type="MMA",
        subscription_type="monthly",
        is_active=True,
        start_date=datetime(2026, 5, 11, 18, 0, 0),
        end_date=datetime(2026, 6, 5, 18, 30, 0),
        trainings_total=12,
        trainings_remaining=6,
    )
    session.add(sub)
    session.add(
        ClubSetting(key=KEY_GROUP_TRAINING_DURATION_MINUTES, value="90")
    )
    session.commit()

    result = migrate_existing_subscription(session, sub.id)
    session.refresh(sub)

    assert result["success"] is True
    assert sub.start_date == datetime(2026, 5, 11, 17, 0, 0)
    assert sub.end_date == datetime(2026, 6, 5, 18, 30, 0)
