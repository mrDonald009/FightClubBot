"""Отмена тренировки: только абонементы этого тренера."""
from contextlib import contextmanager
from datetime import datetime
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db_utils.coach_absence import (
    apply_coach_absence,
    deactivate_coach_absence_and_migrate,
    is_group_monthly_subscription,
    is_training_in_coach_absence_for_subscription,
    subscription_belongs_to_coach,
)
from database.models import Athlete, Base, Coach, SportType, Subscription


@contextmanager
def _freeze_now(dt):
    with patch("database.db_utils.coach_absence.now_moscow", return_value=dt), patch(
        "database.db_utils.remaining.now_moscow", return_value=dt
    ):
        yield


@pytest.fixture
def two_coaches_two_athletes():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    st = SportType(name="MMA", display_name="MMA")
    s.add(st)
    s.flush()
    c1 = Coach(telegram_id=1001, username="c1", first_name="Coach1", sport_type_id=st.id)
    c2 = Coach(telegram_id=1002, username="c2", first_name="Coach2", sport_type_id=st.id)
    s.add_all([c1, c2])
    s.flush()
    a1 = Athlete(
        full_name="Athlete One",
        sport_type="MMA",
        age_group="adults",
        created_by=c1.id,
    )
    a2 = Athlete(
        full_name="Athlete Two",
        sport_type="MMA",
        age_group="adults",
        created_by=c2.id,
    )
    s.add_all([a1, a2])
    s.flush()
    sub1 = Subscription(
        athlete_id=a1.id,
        discipline_key="mma",
        sport_type="MMA",
        subscription_type="monthly",
        start_date=datetime(2026, 3, 1, 18, 0),
        end_date=datetime(2026, 4, 15, 20, 0),
        trainings_total=12,
        trainings_remaining=10,
        is_active=True,
    )
    sub2 = Subscription(
        athlete_id=a2.id,
        discipline_key="mma",
        sport_type="MMA",
        subscription_type="monthly",
        start_date=datetime(2026, 3, 1, 18, 0),
        end_date=datetime(2026, 4, 15, 20, 0),
        trainings_total=12,
        trainings_remaining=10,
        is_active=True,
    )
    s.add_all([sub1, sub2])
    s.commit()
    yield s, c1, c2, sub1, sub2
    s.close()


def test_subscription_belongs_to_coach(two_coaches_two_athletes):
    s, c1, c2, sub1, sub2 = two_coaches_two_athletes
    assert subscription_belongs_to_coach(sub1, c1.id)
    assert not subscription_belongs_to_coach(sub1, c2.id)
    assert subscription_belongs_to_coach(sub2, c2.id)


def test_apply_coach_absence_only_extends_coach_athletes(two_coaches_two_athletes):
    s, c1, c2, sub1, sub2 = two_coaches_two_athletes
    end1_before = sub1.end_date
    end2_before = sub2.end_date
    with _freeze_now(datetime(2026, 3, 20, 10, 0, 0)):
        r = apply_coach_absence(
            s,
            c1.id,
            datetime(2026, 3, 24),
            datetime(2026, 3, 28),
            "Болезнь",
            1,
        )
    assert r["success"]
    assert r["updated_subscriptions"] >= 1
    s.refresh(sub1)
    s.refresh(sub2)
    assert sub1.end_date >= end1_before
    assert sub2.end_date == end2_before


def test_coach_absence_blocks_deduct_for_that_coach(two_coaches_two_athletes):
    s, c1, c2, sub1, sub2 = two_coaches_two_athletes
    slot = datetime(2026, 3, 25, 18, 0, 0)
    with _freeze_now(datetime(2026, 3, 20, 10, 0, 0)):
        apply_coach_absence(
            s, c1.id, datetime(2026, 3, 24), datetime(2026, 3, 28), "Болезнь", 1
        )
    assert is_training_in_coach_absence_for_subscription(s, slot, sub1)
    assert not is_training_in_coach_absence_for_subscription(s, slot, sub2)


def test_apply_coach_absence_skips_single_and_individual(two_coaches_two_athletes):
    s, c1, _, sub1, _ = two_coaches_two_athletes
    a1 = sub1.athlete
    single = Subscription(
        athlete_id=a1.id,
        discipline_key="mma_single",
        sport_type="MMA",
        subscription_type="single",
        start_date=datetime(2026, 3, 25, 18, 0),
        end_date=datetime(2026, 3, 25, 19, 30),
        trainings_total=1,
        trainings_remaining=1,
        is_active=True,
        responsible_coach_id=c1.id,
    )
    individual = Subscription(
        athlete_id=a1.id,
        discipline_key="mma_ind",
        sport_type="MMA",
        subscription_type="individual",
        start_date=datetime(2026, 3, 26, 10, 0),
        end_date=datetime(2026, 3, 26, 11, 0),
        trainings_total=1,
        trainings_remaining=1,
        is_active=True,
        responsible_coach_id=c1.id,
    )
    s.add_all([single, individual])
    s.commit()
    single_end = single.end_date
    ind_start = individual.start_date
    with _freeze_now(datetime(2026, 3, 20, 10, 0, 0)):
        apply_coach_absence(
            s, c1.id, datetime(2026, 3, 24), datetime(2026, 3, 28), "Каникулы", 1
        )
    s.refresh(single)
    s.refresh(individual)
    assert is_group_monthly_subscription(sub1)
    assert not is_group_monthly_subscription(single)
    assert single.end_date == single_end
    assert individual.start_date == ind_start


def test_early_deactivate_reverts_coach_absence_extension(two_coaches_two_athletes):
    s, c1, _, sub1, _ = two_coaches_two_athletes
    original_end = sub1.end_date
    at_apply = datetime(2026, 3, 20, 10, 0, 0)
    at_deact = datetime(2026, 3, 25, 10, 0, 0)
    with _freeze_now(at_apply):
        r = apply_coach_absence(
            s, c1.id, datetime(2026, 3, 26), datetime(2026, 3, 30), "Болезнь", 1
        )
    ca_id = r["coach_absence_id"]
    s.refresh(sub1)
    assert sub1.end_date > original_end
    with _freeze_now(at_deact):
        deactivate_coach_absence_and_migrate(s, ca_id)
    s.refresh(sub1)
    assert sub1.end_date == original_end
