"""Отмена группового занятия: день+слот, продление только monthly."""
from contextlib import contextmanager
from datetime import datetime
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db_utils.coach_absence import apply_coach_absence
from database.db_utils.group_training_cancellation import (
    apply_group_training_cancellation,
    revert_group_training_cancellation,
)
from database.models import (
    Athlete,
    Base,
    Coach,
    GroupTrainingCancellationApplication,
    SportType,
    Subscription,
    Training,
)


@contextmanager
def _freeze_now(dt):
    patches = [
        patch("database.db_utils.group_training_cancellation.now_moscow", return_value=dt),
        patch("database.db_utils.coach_absence.now_moscow", return_value=dt),
        patch("database.db_utils.remaining.now_moscow", return_value=dt),
    ]
    for p in patches:
        p.start()
    try:
        yield
    finally:
        for p in patches:
            p.stop()


@pytest.fixture
def coach_monthly_setup():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    st = SportType(name="MMA", display_name="MMA")
    s.add(st)
    s.flush()
    coach = Coach(telegram_id=1001, username="c1", first_name="C", sport_type_id=st.id)
    s.add(coach)
    s.flush()
    athlete = Athlete(
        full_name="A",
        sport_type="MMA",
        age_group="adults",
        created_by=coach.id,
    )
    s.add(athlete)
    s.flush()
    monthly = Subscription(
        athlete_id=athlete.id,
        discipline_key="mma",
        sport_type="MMA",
        subscription_type="monthly",
        start_date=datetime(2026, 3, 1, 18, 0),
        end_date=datetime(2026, 4, 15, 19, 30),
        trainings_total=12,
        trainings_remaining=10,
        is_active=True,
    )
    single = Subscription(
        athlete_id=athlete.id,
        discipline_key="mma_s",
        sport_type="MMA",
        subscription_type="single",
        start_date=datetime(2026, 3, 25, 18, 0),
        end_date=datetime(2026, 3, 25, 19, 30),
        trainings_total=1,
        trainings_remaining=1,
        is_active=True,
        responsible_coach_id=coach.id,
    )
    s.add_all([monthly, single])
    s.commit()
    yield s, coach, monthly, single
    s.close()


def test_cancel_group_slot_extends_monthly_only(coach_monthly_setup):
    s, coach, monthly, single = coach_monthly_setup
    slot_dt = datetime(2026, 3, 25, 18, 0)
    end_m_before = monthly.end_date
    end_s_before = single.end_date
    with _freeze_now(datetime(2026, 3, 20, 10, 0)):
        r = apply_group_training_cancellation(
            s, coach.id, "MMA", "adults", slot_dt, created_by=1
        )
    assert r["success"]
    s.refresh(monthly)
    s.refresh(single)
    assert monthly.end_date > end_m_before
    assert single.end_date == end_s_before
    t = s.query(Training).filter_by(id=r["training_id"]).first()
    assert t and t.is_cancelled


def test_revert_group_slot_restores_monthly_end_date(coach_monthly_setup):
    s, coach, monthly, _single = coach_monthly_setup
    slot_dt = datetime(2026, 3, 25, 18, 0)
    end_before = monthly.end_date
    with _freeze_now(datetime(2026, 3, 20, 10, 0)):
        r = apply_group_training_cancellation(
            s, coach.id, "MMA", "adults", slot_dt, created_by=1
        )
    assert r["success"]
    s.refresh(monthly)
    assert monthly.end_date > end_before
    training_id = r["training_id"]
    apps = (
        s.query(GroupTrainingCancellationApplication)
        .filter_by(training_id=training_id)
        .all()
    )
    assert len(apps) == 1
    assert apps[0].training_days_added > 0

    with _freeze_now(datetime(2026, 3, 20, 10, 0)):
        rev = revert_group_training_cancellation(
            s, training_id, coach_id=coach.id
        )
    assert rev["success"]
    assert rev.get("reverted", 0) >= 1
    s.refresh(monthly)
    assert monthly.end_date == end_before
    t = s.query(Training).filter_by(id=training_id).first()
    assert t and not t.is_cancelled


def test_period_apply_still_monthly_only(coach_monthly_setup):
    s, coach, monthly, single = coach_monthly_setup
    end_s = single.end_date
    with _freeze_now(datetime(2026, 3, 20, 10, 0)):
        apply_coach_absence(
            s, coach.id, datetime(2026, 3, 24), datetime(2026, 3, 28), "x", 1
        )
    s.refresh(single)
    assert single.end_date == end_s
