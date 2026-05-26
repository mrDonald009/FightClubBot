"""Разовый абонемент: при заморозке переносится start_date вместе с end_date."""
from datetime import datetime
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db_utils.freeze_personal import freeze_subscription, unfreeze_subscription
from database.models import Athlete, Base, Coach, SportType, Subscription
from handlers.card_handlers import _append_subscription_freeze_ui_lines
from utils.time_utils import training_end_time


@pytest.fixture
def mma_single_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    s = SessionLocal()
    st = SportType(name="MMA", display_name="MMA")
    s.add(st)
    s.flush()
    coach = Coach(telegram_id=900003, username="c", first_name="C", sport_type_id=st.id)
    s.add(coach)
    s.flush()
    athlete = Athlete(
        full_name="Single Tester",
        sport_type="MMA",
        age_group="middle",
        created_by=coach.id,
    )
    s.add(athlete)
    s.flush()
    start = datetime(2026, 5, 29, 18, 30, 0)
    end = training_end_time(start)
    sub = Subscription(
        athlete_id=athlete.id,
        discipline_key="mma_group",
        sport_type="MMA",
        subscription_type="single",
        start_date=start,
        end_date=end,
        trainings_total=1,
        trainings_remaining=1,
        is_active=True,
    )
    s.add(sub)
    s.commit()
    yield s, sub, start, end
    s.close()


def test_single_freeze_shifts_start_date_to_new_slot(mma_single_session):
    session, sub, original_start, original_end = mma_single_session
    at_freeze = datetime(2026, 5, 28, 10, 0, 0)
    freeze_end_pick = datetime(2026, 5, 29, 12, 0, 0)

    with patch("database.db_utils.freeze_personal.now_moscow", return_value=at_freeze):
        res = freeze_subscription(session, sub.id, freeze_end_pick)
    assert res["success"], res
    session.refresh(sub)
    assert sub.is_frozen
    assert sub.start_date != original_start
    assert sub.end_date != original_end
    assert sub.start_date.date() == sub.end_date.date()
    assert sub.start_date > original_start
    assert sub.last_freeze_pre_start_date == original_start

    text = _append_subscription_freeze_ui_lines("", sub, now=at_freeze)
    assert "Тренировка перенесена" in text
    assert "Продлено на" not in text


def test_late_unfreeze_recalculates_start_to_match_end_slot(mma_single_session):
    """После окончания периода заморозки start_date выравнивается под end_date."""
    session, sub, original_start, _original_end = mma_single_session
    at_freeze = datetime(2026, 5, 28, 10, 0, 0)
    after_period = datetime(2026, 5, 30, 10, 0, 0)

    with patch("database.db_utils.freeze_personal.now_moscow", return_value=at_freeze):
        freeze_subscription(session, sub.id, datetime(2026, 5, 29, 12, 0, 0))
    session.refresh(sub)
    new_end = sub.end_date
    assert new_end > original_start

    sub.start_date = original_start
    session.commit()

    with patch("database.db_utils.freeze_personal.now_moscow", return_value=after_period):
        unres = unfreeze_subscription(session, sub.id)
    assert unres["success"]
    assert not unres.get("reverted_extension")
    session.refresh(sub)
    assert sub.start_date.date() == new_end.date()
    assert sub.start_date < sub.end_date


def test_single_early_unfreeze_restores_start_and_end(mma_single_session):
    session, sub, original_start, original_end = mma_single_session
    at_freeze = datetime(2026, 5, 28, 10, 0, 0)
    during = datetime(2026, 5, 29, 19, 0, 0)

    with patch("database.db_utils.freeze_personal.now_moscow", return_value=at_freeze):
        freeze_subscription(session, sub.id, datetime(2026, 5, 29, 12, 0, 0))
    with patch("database.db_utils.freeze_personal.now_moscow", return_value=during):
        unres = unfreeze_subscription(session, sub.id)
    assert unres["success"]
    assert unres.get("reverted_extension")
    session.refresh(sub)
    assert sub.start_date == original_start
    assert sub.end_date == original_end
