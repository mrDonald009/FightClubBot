"""Оплата при активации абонемента (таблица subscription_tariffs)."""
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db_utils.subscription_activation_payment import (
    record_payment_on_subscription_activation,
    resolve_subscription_activation_price_rubles,
)
from database.db_utils.subscription_tariffs import (
    tariff_preview_for_sport,
    tariff_preview_monthly_single_for_sport,
)
from database.models import (
    Athlete,
    Base,
    Coach,
    SportType,
    Subscription,
    SubscriptionPayment,
    SubscriptionTariff,
)

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


def test_tariff_preview_monthly_single():
    s, _coach = _session_coach()
    s.add(
        SubscriptionTariff(
            sport_type_name="MMA",
            tariff_kind="subscription_monthly",
            amount_rubles=12000,
            is_active=True,
        )
    )
    s.add(
        SubscriptionTariff(
            sport_type_name="MMA",
            tariff_kind="subscription_single",
            amount_rubles=800,
            is_active=True,
        )
    )
    s.add(
        SubscriptionTariff(
            sport_type_name="MMA",
            tariff_kind="individual_training",
            amount_rubles=3000,
            is_active=True,
        )
    )
    s.commit()
    assert tariff_preview_monthly_single_for_sport(s, "MMA") == (12000, 800)
    assert tariff_preview_for_sport(s, "MMA") == (12000, 800, 3000)
    s.close()


def test_record_payment_from_db_tariff():
    s, coach = _session_coach()
    s.add(
        SubscriptionTariff(
            sport_type_name="MMA",
            tariff_kind="subscription_monthly",
            amount_rubles=7000,
            is_active=True,
        )
    )
    s.commit()
    a = Athlete(
        full_name="ТарифБД",
        sport_type="MMA",
        age_group="adults",
        created_by=coach.id,
    )
    s.add(a)
    s.flush()
    sub = Subscription(
        athlete_id=a.id,
        discipline_key="mma_db",
        sport_type="MMA",
        subscription_type="monthly",
        is_active=True,
        start_date=datetime(2026, 3, 1, 10, 0, 0),
        end_date=datetime(2027, 3, 1, 10, 0, 0),
    )
    s.add(sub)
    s.flush()
    paid = datetime(2026, 3, 1, 10, 0, 0)
    record_payment_on_subscription_activation(s, sub, paid, recorded_by_telegram_id=1)
    s.commit()
    rows = s.query(SubscriptionPayment).all()
    s.close()
    assert len(rows) == 1
    assert rows[0].amount_rubles == 7000
    assert rows[0].payment_kind == "subscription_monthly"


def test_resolve_price_ignores_tariff_without_explicit_sport():
    """Строка с пустым видом спорта не участвует в подборе суммы."""
    s, coach = _session_coach()
    s.add(
        SubscriptionTariff(
            sport_type_name="",
            tariff_kind="subscription_monthly",
            amount_rubles=11111,
            is_active=True,
        )
    )
    s.commit()
    a = Athlete(
        full_name="Боец",
        sport_type="MMA",
        age_group="adults",
        created_by=coach.id,
    )
    s.add(a)
    s.flush()
    sub = Subscription(
        athlete_id=a.id,
        discipline_key="mma_wc",
        sport_type="MMA",
        subscription_type="monthly",
        is_active=True,
        start_date=datetime(2026, 4, 1, 10, 0, 0),
        end_date=datetime(2027, 4, 1, 10, 0, 0),
    )
    s.add(sub)
    s.flush()
    assert resolve_subscription_activation_price_rubles(s, sub) is None


def test_record_payment_on_activation():
    s, coach = _session_coach()
    s.add(
        SubscriptionTariff(
            sport_type_name="MMA",
            tariff_kind="subscription_monthly",
            amount_rubles=5000,
            is_active=True,
        )
    )
    s.commit()
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
    assert rows[0].payment_kind == "subscription_monthly"


def test_no_record_when_no_tariff_in_db():
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
