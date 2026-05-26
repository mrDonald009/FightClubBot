"""UI и авто-разморозка персональной заморозки абонемента."""
from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.models import Athlete, AthleteFreeze, Base, Subscription
from database.db_utils.freeze_personal import (
    expire_stale_subscription_freezes,
    subscription_is_currently_frozen,
)
from handlers.card_handlers import (
    _append_subscription_freeze_ui_lines,
    _format_subscription_status_ui,
    _status_icon_from_status_text,
)
from utils.time_utils import now_moscow


@pytest.fixture
def memory_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    yield session
    session.close()


def test_subscription_is_currently_frozen_respects_until():
    now = now_moscow()
    sub = Subscription(
        is_frozen=True,
        frozen_from=now - timedelta(days=3),
        frozen_until=now - timedelta(hours=1),
    )
    assert not subscription_is_currently_frozen(sub, now=now)

    sub.frozen_until = now + timedelta(days=1)
    assert subscription_is_currently_frozen(sub, now=now)


def test_format_status_shows_frozen_when_active_and_in_freeze():
    now = now_moscow()
    sub = Subscription(
        is_active=True,
        is_frozen=True,
        end_date=now + timedelta(days=30),
        frozen_from=now - timedelta(days=1),
        frozen_until=now + timedelta(days=1),
    )
    assert _format_subscription_status_ui(sub) == "❄️ Заморожен"
    assert _status_icon_from_status_text("❄️ Заморожен") == "❄️"


def test_freeze_ui_lines_hide_expired_period_but_show_extension():
    now = now_moscow()
    sub = Subscription(
        is_active=True,
        is_frozen=True,
        end_date=now + timedelta(days=30),
        frozen_from=now - timedelta(days=3),
        frozen_until=now - timedelta(hours=1),
        frozen_training_days_total=3,
    )
    text = _append_subscription_freeze_ui_lines("", sub, now=now)
    assert "Заморожен до" not in text
    assert "Продлено на 3 тр. дней" in text

    sub.frozen_until = now + timedelta(days=1)
    text_active = _append_subscription_freeze_ui_lines("", sub, now=now)
    assert "Заморожен до" in text_active
    assert "Продлено на 3 тр. дней" in text_active


def test_expire_stale_subscription_freezes(memory_session):
    now = now_moscow()
    athlete = Athlete(full_name="Test", sport_type="MMA", age_group="children")
    memory_session.add(athlete)
    memory_session.flush()
    sub = Subscription(
        athlete_id=athlete.id,
        subscription_type="monthly",
        discipline_key="mma_group",
        sport_type="MMA",
        is_active=True,
        is_frozen=True,
        trainings_total=12,
        trainings_remaining=8,
        end_date=now + timedelta(days=20),
        frozen_from=now - timedelta(days=3),
        frozen_until=now - timedelta(hours=2),
    )
    memory_session.add(sub)
    memory_session.add(
        AthleteFreeze(
            athlete_id=athlete.id,
            frozen_from=sub.frozen_from,
            frozen_until=sub.frozen_until,
        )
    )
    memory_session.commit()

    updated = expire_stale_subscription_freezes(memory_session, athlete_id=athlete.id)
    memory_session.refresh(sub)

    assert updated == 1
    assert sub.is_frozen is False
    assert sub.frozen_from is None
    assert sub.frozen_until is None
    assert memory_session.query(AthleteFreeze).filter_by(athlete_id=athlete.id).count() == 0
