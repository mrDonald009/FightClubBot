"""Быстрая запись на групповую/разовую из календаря тренера."""
from datetime import datetime
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.models import Athlete, Base, Coach, SportType, Subscription
from handlers.coach_handlers import (
    _athletes_eligible_for_group_booking,
    _build_cal_group_time_keyboard,
    _cal_group_slot_token,
    _parse_cal_group_slot_token,
)
from database.db_utils.subscriptions import create_subscription

pytestmark = pytest.mark.db


def _coach_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    st = SportType(name="MMA", display_name="MMA")
    s.add(st)
    s.flush()
    coach = Coach(telegram_id=7101, sport_type_id=st.id, sport_type="MMA")
    s.add(coach)
    s.commit()
    return s, coach, engine


def test_cal_group_slot_token_roundtrip():
    dt = datetime(2030, 6, 10, 20, 0, 0)
    token = _cal_group_slot_token(dt, "adults")
    assert token == "203006102000_a"
    parsed_dt, ag = _parse_cal_group_slot_token(token)
    assert parsed_dt == dt
    assert ag == "adults"


def test_build_cal_group_time_keyboard_lists_schedule_slots():
    s, coach, engine = _coach_session()
    day = datetime(2030, 6, 10).date()  # понедельник — MMA по расписанию
    with patch(
        "handlers.coach_handlers.now_moscow",
        return_value=datetime(2030, 6, 1, 8, 0, 0),
    ):
        kb = _build_cal_group_time_keyboard(
            s, coach, "MMA", day.year, day.month, day.day, kind="grp"
        )
    s.close()
    engine.dispose()
    assert kb is not None
    callbacks = [
        btn.callback_data
        for row in kb.inline_keyboard
        for btn in row
        if btn.callback_data and btn.callback_data.startswith("cal_grp_ts_")
    ]
    assert len(callbacks) >= 3  # children, middle, adults


def test_athletes_eligible_for_group_booking_filters_age_and_active():
    s, coach, engine = _coach_session()
    adult = Athlete(
        full_name="Взрослый Спортсмен",
        sport_type="MMA",
        age_group="adults",
        created_by=coach.id,
    )
    child = Athlete(
        full_name="Детский Спортсмен",
        sport_type="MMA",
        age_group="children",
        created_by=coach.id,
    )
    blocked = Athlete(
        full_name="С активным абонементом",
        sport_type="MMA",
        age_group="adults",
        created_by=coach.id,
    )
    s.add_all([adult, child, blocked])
    s.flush()
    active = create_subscription(
        s,
        blocked.id,
        "monthly",
        "MMA",
        discipline_key="mma_group",
        subscription_format="group",
        commit=False,
    )
    active.is_active = True
    active.start_date = datetime(2030, 5, 1, 0, 0, 0)
    active.end_date = datetime(2030, 7, 1, 0, 0, 0)
    s.commit()

    eligible = _athletes_eligible_for_group_booking(
        s, coach, age_group="adults", sport_type="MMA"
    )
    names = {a.full_name for a in eligible}
    assert "Взрослый Спортсмен" in names
    assert "Детский Спортсмен" not in names
    assert "С активным абонементом" not in names
    s.close()
    engine.dispose()


def test_group_book_prepare_does_not_deactivate_individual():
    s, coach, engine = _coach_session()
    athlete = Athlete(
        full_name="Individual и Group",
        sport_type="MMA",
        age_group="adults",
        created_by=coach.id,
    )
    s.add(athlete)
    s.flush()
    ind = create_subscription(
        s,
        athlete.id,
        "individual",
        "MMA",
        discipline_key="mma_individual",
        subscription_format="individual",
        commit=False,
    )
    ind.is_active = True
    ind.start_date = datetime(2030, 6, 5, 10, 0, 0)
    ind.end_date = datetime(2030, 6, 5, 11, 30, 0)
    s.commit()

    monthly = create_subscription(
        s,
        athlete.id,
        "monthly",
        "MMA",
        discipline_key="mma_group",
        subscription_format="group",
        commit=False,
    )
    monthly.is_active = True
    monthly.start_date = datetime(2030, 6, 10, 20, 0, 0)
    monthly.end_date = datetime(2030, 7, 10, 20, 0, 0)
    s.commit()

    assert ind.is_active is True
    assert (
        s.query(Subscription)
        .filter_by(athlete_id=athlete.id, subscription_type="monthly", is_active=True)
        .count()
        == 1
    )
    s.close()
    engine.dispose()
