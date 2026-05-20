"""Автовыравнивание групповых тренировок по TRAINING_SCHEDULE."""
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db_utils.training_slots import (
    find_group_training_on_calendar_day,
    reconcile_group_training_to_schedule,
)
from database.models import Attendance, Athlete, Base, Coach, SportType, Subscription, Training
from services.attendance_training_flow import build_today_attendance_slots


@pytest.mark.db
def test_reconcile_children_moves_18_00_to_17_00_on_wednesday():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    st = SportType(name="MMA", display_name="MMA")
    session.add(st)
    session.flush()
    coach = Coach(telegram_id=9001, sport_type_id=st.id, sport_type="MMA")
    session.add(coach)
    session.flush()

    legacy = Training(
        sport_type="MMA",
        age_group="children",
        training_date=datetime(2026, 5, 20, 18, 0, 0),
        coach_id=coach.id,
        training_format=None,
    )
    session.add(legacy)
    session.commit()

    result = reconcile_group_training_to_schedule(session, legacy)
    session.commit()

    assert result.training_date == datetime(2026, 5, 20, 17, 0, 0)


@pytest.mark.db
def test_find_group_training_realigns_legacy_on_day():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    st = SportType(name="MMA", display_name="MMA")
    session.add(st)
    session.flush()
    coach = Coach(telegram_id=9002, sport_type_id=st.id, sport_type="MMA")
    session.add(coach)
    session.flush()

    legacy = Training(
        sport_type="MMA",
        age_group="children",
        training_date=datetime(2026, 5, 20, 18, 0, 0),
        coach_id=coach.id,
    )
    session.add(legacy)
    session.commit()

    found = find_group_training_on_calendar_day(
        session,
        sport_type="MMA",
        age_group="children",
        day=datetime(2026, 5, 20).date(),
        coach_id=coach.id,
    )
    session.commit()

    assert found is not None
    assert found.id == legacy.id
    assert found.training_date == datetime(2026, 5, 20, 17, 0, 0)


@pytest.mark.db
def test_build_today_includes_middle_virtual_for_coach():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    st = SportType(name="MMA", display_name="MMA")
    session.add(st)
    session.flush()
    coach = Coach(telegram_id=9003, sport_type_id=st.id, sport_type="MMA")
    session.add(coach)
    session.commit()

    now = datetime(2026, 5, 20, 8, 0, 0)
    slots, _ = build_today_attendance_slots(session, coach, now)
    labels = {
        (s.training_datetime.strftime("%H:%M"), s.age_group)
        for s in slots
        if not s.is_individual_format
    }
    assert ("17:00", "children") in labels
    assert ("18:30", "middle") in labels
    assert ("20:00", "adults") in labels
