"""Маркировка дней в месячной сетке «Мой календарь»."""
import pytest
from datetime import date, datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, joinedload

from database.models import Athlete, Base, Coach, SportType, Subscription
from services.attendance_training_flow import coach_calendar_day_button_text

pytestmark = pytest.mark.db


def _coach_mma(session):
    st = SportType(name="MMA", display_name="MMA")
    session.add(st)
    session.flush()
    coach = Coach(telegram_id=900301, first_name="C", sport_type_id=st.id)
    session.add(coach)
    session.commit()
    return (
        session.query(Coach)
        .options(joinedload(Coach.sport_type_rel))
        .filter_by(id=coach.id)
        .one()
    )


def test_calendar_marker_scheduled_without_athletes_is_dot():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    coach = _coach_mma(s)
    today = date(2026, 5, 24)
    # Среда 1 апреля 2026 — расписание MMA, без абонементов
    text = coach_calendar_day_button_text(
        s, coach, date(2026, 4, 1), today=today
    )
    assert text == "1•"


def test_calendar_marker_past_day_with_expired_sub_is_plus():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    coach = _coach_mma(s)
    athlete = Athlete(
        full_name="Морозов Егор",
        sport_type="MMA",
        age_group="adults",
        created_by=coach.id,
    )
    s.add(athlete)
    s.flush()
    s.add(
        Subscription(
            athlete_id=athlete.id,
            discipline_key="mma_group",
            sport_type="MMA",
            subscription_type="monthly",
            is_active=False,
            start_date=datetime(2026, 4, 1, 0, 0, 0),
            end_date=datetime(2026, 4, 30, 23, 59, 0),
        )
    )
    s.commit()
    today = date(2026, 5, 24)
    text = coach_calendar_day_button_text(
        s, coach, date(2026, 4, 17), today=today
    )
    assert text == "+17"


def test_calendar_marker_today_is_brackets():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    coach = _coach_mma(s)
    today = date(2026, 5, 24)
    text = coach_calendar_day_button_text(
        s, coach, today, today=today
    )
    assert text == "[24]"


def test_calendar_marker_non_training_day_is_plain():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    coach = _coach_mma(s)
    today = date(2026, 5, 24)
    # Суббота 4 апреля — нет группового расписания MMA
    text = coach_calendar_day_button_text(
        s, coach, date(2026, 4, 4), today=today
    )
    assert text == " 4"
