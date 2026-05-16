"""Несколько индивидуальных броней на одного спортсмена."""
from datetime import datetime

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from database.db_utils.subscriptions import create_subscription
from database.models import Athlete, Base, Subscription
from handlers.card_handlers import prepare_individual_subscription_for_activation

pytestmark = pytest.mark.db


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    return s, engine


def test_two_active_individual_subscriptions_same_athlete():
    s, engine = _session()
    athlete = Athlete(
        full_name="Мульти Индив",
        sport_type="MMA",
        age_group="adults",
        created_by=1,
    )
    s.add(athlete)
    s.flush()

    sub1 = create_subscription(
        s,
        athlete.id,
        "individual",
        "MMA",
        discipline_key="mma_individual",
        subscription_format="individual",
        commit=False,
    )
    sub1.start_date = datetime(2026, 5, 10, 8, 0, 0)
    sub1.end_date = datetime(2026, 5, 10, 9, 0, 0)
    sub1.is_active = True

    sub2 = create_subscription(
        s,
        athlete.id,
        "individual",
        "MMA",
        discipline_key="mma_individual",
        subscription_format="individual",
        commit=False,
    )
    sub2.start_date = datetime(2026, 5, 12, 14, 0, 0)
    sub2.end_date = datetime(2026, 5, 12, 15, 0, 0)
    sub2.is_active = True
    s.commit()

    rows = (
        s.query(Subscription)
        .filter_by(athlete_id=athlete.id, subscription_type="individual", is_active=True)
        .order_by(Subscription.start_date.asc())
        .all()
    )
    s.close()
    engine.dispose()
    assert len(rows) == 2
    assert rows[0].start_date.hour == 8
    assert rows[1].start_date.hour == 14


def test_prepare_individual_always_creates_new_row():
    s, engine = _session()
    athlete = Athlete(
        full_name="Новая бронь",
        sport_type="MMA",
        age_group="adults",
        created_by=2,
    )
    s.add(athlete)
    s.flush()
    active = Subscription(
        athlete_id=athlete.id,
        discipline_key="mma_individual",
        sport_type="MMA",
        subscription_type="individual",
        is_active=True,
        start_date=datetime(2026, 5, 11, 10, 0, 0),
        end_date=datetime(2026, 5, 11, 11, 0, 0),
        trainings_total=1,
        trainings_remaining=1,
    )
    s.add(active)
    s.commit()

    draft = prepare_individual_subscription_for_activation(
        s, athlete, "MMA", responsible_coach_id=2
    )
    s.commit()

    assert draft.id != active.id
    assert draft.is_active is False
    assert (
        s.query(Subscription)
        .filter_by(athlete_id=athlete.id, subscription_type="individual")
        .count()
        == 2
    )
    s.close()
    engine.dispose()


def test_unique_individual_slot_per_athlete():
    s, engine = _session()
    athlete = Athlete(
        full_name="Дубль слота",
        sport_type="MMA",
        age_group="adults",
        created_by=3,
    )
    s.add(athlete)
    s.flush()
    slot = datetime(2026, 5, 15, 9, 0, 0)
    s.add(
        Subscription(
            athlete_id=athlete.id,
            discipline_key="mma_individual",
            sport_type="MMA",
            subscription_type="individual",
            is_active=True,
            start_date=slot,
            end_date=datetime(2026, 5, 15, 10, 0, 0),
            trainings_total=1,
            trainings_remaining=1,
        )
    )
    s.commit()

    with pytest.raises(Exception):
        s.add(
            Subscription(
                athlete_id=athlete.id,
                discipline_key="mma_individual",
                sport_type="MMA",
                subscription_type="individual",
                is_active=True,
                start_date=slot,
                end_date=datetime(2026, 5, 15, 10, 0, 0),
                trainings_total=1,
                trainings_remaining=1,
            )
        )
        s.commit()
    s.rollback()
    s.close()
    engine.dispose()
