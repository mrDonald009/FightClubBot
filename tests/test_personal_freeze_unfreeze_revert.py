"""Персональная заморозка: откат продления при досрочной ручной разморозке."""
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db_utils.freeze_personal import freeze_subscription, unfreeze_subscription
from database.models import Athlete, Base, Coach, SportType, Subscription
from utils.time_utils import now_moscow


@pytest.fixture
def mma_monthly_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    s = SessionLocal()
    st = SportType(name="MMA", display_name="MMA")
    s.add(st)
    s.flush()
    coach = Coach(telegram_id=900001, username="c", first_name="C", sport_type_id=st.id)
    s.add(coach)
    s.flush()
    athlete = Athlete(
        full_name="Test",
        sport_type="MMA",
        age_group="adults",
        created_by=coach.id,
    )
    s.add(athlete)
    s.flush()
    end = datetime(2026, 6, 22, 21, 30, 0)
    sub = Subscription(
        athlete_id=athlete.id,
        discipline_key="mma_group",
        sport_type="MMA",
        subscription_type="monthly",
        start_date=datetime(2026, 5, 27, 20, 0, 0),
        end_date=end,
        trainings_total=12,
        trainings_remaining=12,
        is_active=True,
    )
    s.add(sub)
    s.commit()
    yield s, sub, end
    s.close()


def test_unfreeze_before_frozen_until_reverts_extension(mma_monthly_session):
    session, sub, original_end = mma_monthly_session
    freeze_end_pick = datetime(2026, 5, 29, 0, 0, 0)
    at_freeze = datetime(2026, 5, 27, 19, 0, 0)
    during_freeze = datetime(2026, 5, 28, 12, 0, 0)

    with patch("database.db_utils.freeze_personal.now_moscow", return_value=at_freeze):
        res = freeze_subscription(session, sub.id, freeze_end_pick)
    assert res["success"]
    credited = res["training_days_count"]
    session.refresh(sub)
    assert sub.is_frozen
    assert sub.end_date != original_end
    assert sub.frozen_training_days_total == credited
    assert sub.last_freeze_pre_end_date == original_end

    with patch("database.db_utils.freeze_personal.now_moscow", return_value=during_freeze):
        unres = unfreeze_subscription(session, sub.id)
    assert unres["success"]
    assert unres.get("reverted_extension") is True
    session.refresh(sub)
    assert not sub.is_frozen
    assert sub.end_date == original_end
    assert sub.frozen_training_days_total == 0
    assert sub.last_freeze_pre_end_date is None


def test_unfreeze_after_frozen_until_keeps_extension(mma_monthly_session):
    session, sub, original_end = mma_monthly_session
    freeze_end_pick = datetime(2026, 5, 29, 0, 0, 0)
    at_freeze = datetime(2026, 5, 27, 19, 0, 0)
    after_period = datetime(2026, 5, 30, 10, 0, 0)

    with patch("database.db_utils.freeze_personal.now_moscow", return_value=at_freeze):
        res = freeze_subscription(session, sub.id, freeze_end_pick)
    assert res["success"]
    session.refresh(sub)
    extended_end = sub.end_date
    assert extended_end != original_end

    with patch("database.db_utils.freeze_personal.now_moscow", return_value=after_period):
        unres = unfreeze_subscription(session, sub.id)
    assert unres["success"]
    assert not unres.get("reverted_extension")
    session.refresh(sub)
    assert sub.end_date == extended_end
    assert sub.frozen_training_days_total == 2
