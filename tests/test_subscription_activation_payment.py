"""Оплата при активации абонемента (фиксированные суммы из env)."""
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db_utils.subscription_activation_payment import (
    activation_price_rubles,
    record_payment_on_subscription_activation,
)
from database.models import Athlete, Base, Coach, SportType, Subscription, SubscriptionPayment

pytestmark = pytest.mark.db


def _session_coach():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    st = SportType(name="MMA", display_name="MMA")
    s.add(st)
    s.flush()
    coach = Coach(telegram_id=1, sport_type_id=st.id)
    s.add(coach)
    s.commit()
    return s, coach


def test_activation_price_from_env(monkeypatch):
    monkeypatch.setenv("SUBSCRIPTION_PRICE_MONTHLY_RUB", "12000")
    monkeypatch.setenv("SUBSCRIPTION_PRICE_SINGLE_RUB", "800")
    assert activation_price_rubles("monthly") == 12000
    assert activation_price_rubles("single") == 800
    assert activation_price_rubles(None) is None


def test_record_payment_on_activation(monkeypatch):
    monkeypatch.setenv("SUBSCRIPTION_PRICE_MONTHLY_RUB", "5000")
    s, coach = _session_coach()
    a = Athlete(
        full_name="Тест",
        sport_type="MMA",
        age_group="adults",
        created_by=coach.id,
    )
    s.add(a)
    s.flush()
    sub = Subscription(
        athlete_id=a.id,
        discipline_key="mma_t",
        sport_type="MMA",
        subscription_type="monthly",
        is_active=True,
        start_date=datetime(2026, 3, 1, 10, 0, 0),
        end_date=datetime(2027, 3, 1, 10, 0, 0),
    )
    s.add(sub)
    s.flush()
    paid = datetime(2026, 3, 1, 10, 0, 0)
    record_payment_on_subscription_activation(
        s, sub, paid, recorded_by_telegram_id=999
    )
    s.commit()
    rows = s.query(SubscriptionPayment).all()
    s.close()
    assert len(rows) == 1
    assert rows[0].amount_rubles == 5000
    assert rows[0].paid_at == paid
    assert rows[0].recorded_by_telegram_id == 999


def test_no_record_when_env_missing(monkeypatch):
    monkeypatch.delenv("SUBSCRIPTION_PRICE_MONTHLY_RUB", raising=False)
    monkeypatch.delenv("SUBSCRIPTION_PRICE_SINGLE_RUB", raising=False)
    s, coach = _session_coach()
    a = Athlete(
        full_name="Тест2",
        sport_type="MMA",
        age_group="adults",
        created_by=coach.id,
    )
    s.add(a)
    s.flush()
    sub = Subscription(
        athlete_id=a.id,
        discipline_key="mma_t2",
        sport_type="MMA",
        subscription_type="monthly",
        is_active=True,
        start_date=datetime(2026, 3, 1, 10, 0, 0),
        end_date=datetime(2027, 3, 1, 10, 0, 0),
    )
    s.add(sub)
    s.flush()
    record_payment_on_subscription_activation(
        s, sub, datetime(2026, 3, 1, 10, 0, 0), recorded_by_telegram_id=1
    )
    s.commit()
    n = s.query(SubscriptionPayment).count()
    s.close()
    assert n == 0
