"""Фильтр списка спортсменов тренера: вид спорта в профиле или в абонементе."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, joinedload

from database.models import Athlete, Base, Coach, SportType, Subscription
from handlers.coach_handlers import load_athletes_for_list

pytestmark = pytest.mark.db


def _session_coach_mma():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    st = SportType(name="MMA", display_name="MMA")
    s.add(st)
    s.flush()
    coach = Coach(telegram_id=900101, first_name="Coach", sport_type_id=st.id)
    s.add(coach)
    s.commit()
    coach = (
        s.query(Coach)
        .options(joinedload(Coach.sport_type_rel))
        .filter_by(id=coach.id)
        .one()
    )
    return s, coach


def test_coach_list_includes_athlete_when_only_subscription_matches_coach_sport():
    """Профиль — другой вид спорта, но есть абонемент по виду тренера."""
    s, coach = _session_coach_mma()
    athlete = Athlete(
        full_name="Иванов",
        sport_type="Тайский Бокс",
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
            is_active=True,
        )
    )
    s.commit()

    athletes, _header = load_athletes_for_list(s, coach)
    assert [a.id for a in athletes] == [athlete.id]


def test_coach_list_excludes_when_profile_and_subscriptions_are_other_sport():
    s, coach = _session_coach_mma()
    athlete = Athlete(
        full_name="Петров",
        sport_type="Тайский Бокс",
        age_group="adults",
        created_by=coach.id,
    )
    s.add(athlete)
    s.flush()
    s.add(
        Subscription(
            athlete_id=athlete.id,
            discipline_key="thai_boxing_group",
            sport_type="Тайский Бокс",
            is_active=True,
        )
    )
    s.commit()

    athletes, _ = load_athletes_for_list(s, coach)
    assert athletes == []


def test_coach_list_includes_athlete_when_profile_matches_without_subscription():
    s, coach = _session_coach_mma()
    athlete = Athlete(
        full_name="Сидоров",
        sport_type="MMA",
        age_group="adults",
        created_by=coach.id,
    )
    s.add(athlete)
    s.commit()

    athletes, _ = load_athletes_for_list(s, coach)
    assert [a.id for a in athletes] == [athlete.id]
