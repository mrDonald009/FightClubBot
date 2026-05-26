from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.models import Athlete, Base, Subscription
from handlers.card_handlers import prepare_individual_subscription_for_activation

pytestmark = pytest.mark.db


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_prepare_individual_from_active_group_creates_separate_inactive_individual():
    s = _session()
    athlete = Athlete(
        full_name="Тест Спортсмен",
        sport_type="MMA",
        age_group="adults",
        created_by=1,
    )
    s.add(athlete)
    s.flush()
    s.add(
        Subscription(
            athlete_id=athlete.id,
            discipline_key="mma_group",
            sport_type="MMA",
            subscription_type="monthly",
            is_active=True,
            start_date=datetime(2026, 5, 1, 0, 0, 0),
            end_date=datetime(2026, 6, 1, 0, 0, 0),
            trainings_total=12,
            trainings_remaining=12,
            created_at=datetime(2026, 5, 1, 10, 0, 0),
        )
    )
    s.commit()

    sub = prepare_individual_subscription_for_activation(
        s, athlete, "MMA", responsible_coach_id=1
    )

    assert sub.subscription_type == "individual"
    assert sub.discipline_key == "mma_individual"
    assert sub.is_active is False
    assert (
        s.query(Subscription)
        .filter_by(athlete_id=athlete.id, discipline_key="mma_group", is_active=True)
        .count()
        == 1
    )
    s.close()


def test_prepare_individual_creates_new_even_if_inactive_draft_exists():
    s = _session()
    athlete = Athlete(
        full_name="Тест Спортсмен 2",
        sport_type="MMA",
        age_group="adults",
        created_by=2,
    )
    s.add(athlete)
    s.flush()
    existing = Subscription(
        athlete_id=athlete.id,
        discipline_key="mma_individual",
        sport_type="MMA",
        subscription_type="individual",
        is_active=False,
        start_date=None,
        end_date=None,
        trainings_total=1,
        trainings_remaining=1,
        created_at=datetime(2026, 5, 2, 10, 0, 0),
    )
    s.add(existing)
    s.commit()

    sub = prepare_individual_subscription_for_activation(
        s, athlete, "MMA", responsible_coach_id=2
    )
    assert sub.id != existing.id
    assert (
        s.query(Subscription)
        .filter_by(athlete_id=athlete.id, discipline_key="mma_individual")
        .count()
        == 2
    )
    s.close()


def test_prepare_individual_creates_new_when_active_exists():
    s = _session()
    athlete = Athlete(
        full_name="Тест Спортсмен 3",
        sport_type="MMA",
        age_group="adults",
        created_by=3,
    )
    s.add(athlete)
    s.flush()
    active_ind = Subscription(
        athlete_id=athlete.id,
        discipline_key="mma_individual",
        sport_type="MMA",
        subscription_type="individual",
        is_active=True,
        start_date=datetime(2026, 5, 11, 14, 0, 0),
        end_date=datetime(2026, 5, 11, 15, 0, 0),
        trainings_total=1,
        trainings_remaining=1,
        created_at=datetime(2026, 5, 11, 10, 0, 0),
    )
    s.add(active_ind)
    s.commit()

    sub = prepare_individual_subscription_for_activation(
        s, athlete, "MMA", responsible_coach_id=3
    )
    assert sub.id != active_ind.id
    assert sub.is_active is False
    assert active_ind.is_active is True
    assert (
        s.query(Subscription)
        .filter_by(athlete_id=athlete.id, discipline_key="mma_individual")
        .count()
        == 2
    )
    s.close()
