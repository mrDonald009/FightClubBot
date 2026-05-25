"""Заморозки в архиве абонементов."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.models import (
    Athlete,
    Base,
    GlobalFreeze,
    GlobalFreezeApplication,
    Subscription,
)
from handlers.card_handlers import (
    _append_subscription_archive_freeze_summary,
    _history_subscription_button_label,
    _render_subscription_archive_freeze_detail_message,
    _subscription_has_freeze_history,
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


def test_subscription_has_freeze_history_personal_and_global(memory_session):
    athlete = Athlete(full_name="Test", sport_type="MMA", age_group="children")
    memory_session.add(athlete)
    memory_session.flush()

    sub = Subscription(
        athlete_id=athlete.id,
        discipline_key="MMA:group",
        sport_type="MMA",
        subscription_type="monthly",
        frozen_training_days_total=2,
    )
    memory_session.add(sub)
    memory_session.commit()

    assert _subscription_has_freeze_history(sub, memory_session)

    sub.frozen_training_days_total = 0
    assert not _subscription_has_freeze_history(sub, memory_session)

    gf = GlobalFreeze(
        title="Каникулы",
        start_date=datetime(2026, 1, 1, 0, 0),
        end_date=datetime(2026, 1, 7, 23, 59),
        is_active=False,
    )
    memory_session.add(gf)
    memory_session.flush()
    memory_session.add(
        GlobalFreezeApplication(
            global_freeze_id=gf.id,
            subscription_id=sub.id,
            training_days_added=0,
            old_end_date=datetime(2026, 6, 1, 18, 0),
            new_end_date=datetime(2026, 6, 1, 18, 0),
        )
    )
    memory_session.commit()
    assert _subscription_has_freeze_history(sub, memory_session)


def test_archive_freeze_summary_and_detail(memory_session):
    now = now_moscow()
    athlete = Athlete(full_name="Иванов И.", sport_type="MMA", age_group="children")
    memory_session.add(athlete)
    memory_session.flush()

    sub = Subscription(
        athlete_id=athlete.id,
        discipline_key="MMA:group",
        sport_type="MMA",
        subscription_type="monthly",
        is_active=True,
        is_frozen=True,
        frozen_from=now - timedelta(days=1),
        frozen_until=now + timedelta(days=2),
        frozen_training_days_total=3,
        frozen_days_total=5,
        end_date=now + timedelta(days=30),
    )
    memory_session.add(sub)
    memory_session.flush()

    gf = GlobalFreeze(
        title="Новогодние каникулы",
        start_date=datetime(2026, 1, 1, 0, 0),
        end_date=datetime(2026, 1, 10, 23, 59),
        is_active=False,
    )
    memory_session.add(gf)
    memory_session.flush()
    memory_session.add(
        GlobalFreezeApplication(
            global_freeze_id=gf.id,
            subscription_id=sub.id,
            training_days_added=2,
            old_end_date=datetime(2026, 6, 1, 18, 0),
            new_end_date=datetime(2026, 6, 8, 18, 0),
            created_at=datetime(2026, 1, 2, 10, 0),
        )
    )
    memory_session.commit()

    summary = _append_subscription_archive_freeze_summary("", sub, memory_session, now=now)
    assert "Продлено на 3 тр. дней" in summary
    assert "Массовых заморозок: 1" in summary

    detail = _render_subscription_archive_freeze_detail_message(
        memory_session, sub, athlete, now=now
    )
    assert "❄️ <b>ЗАМОРОЗКИ</b>" in detail
    assert "Новогодние каникулы" in detail
    assert "+2 тр. дн." in detail
    assert "личная заморозка" in detail


def test_history_button_shows_freeze_icon(memory_session):
    sub = Subscription(
        id=7,
        subscription_type="monthly",
        start_date=datetime(2026, 5, 1, 17, 0),
        end_date=datetime(2026, 6, 1, 18, 0),
        frozen_training_days_total=1,
    )
    label = _history_subscription_button_label(sub, session=memory_session)
    assert label.startswith("❄️")
