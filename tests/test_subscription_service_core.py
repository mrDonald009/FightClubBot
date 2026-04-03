"""Unit-тесты core-веток SubscriptionService."""

from types import SimpleNamespace

import pytest

from core.exceptions import SubscriptionNotFoundError, ValidationError
from services.subscription_service import SubscriptionService


def test_get_subscription_or_raise_raises_when_missing(monkeypatch):
    monkeypatch.setattr(SubscriptionService, "get_subscription_by_id", lambda *_a, **_k: None)

    with pytest.raises(SubscriptionNotFoundError):
        SubscriptionService.get_subscription_or_raise(session=object(), subscription_id=42)


def test_get_active_subscription_filters_by_sport_type(monkeypatch):
    sub_mt = SimpleNamespace(is_active=True, sport_type="Муай-тай")
    sub_box = SimpleNamespace(is_active=True, sport_type="Бокс")
    athlete = SimpleNamespace(subscriptions=[sub_mt, sub_box])

    monkeypatch.setattr(
        "services.subscription_service.AthleteService.get_athlete_or_raise",
        lambda *_a, **_k: athlete,
    )

    got = SubscriptionService.get_active_subscription(
        session=object(),
        athlete_id=1,
        sport_type="Бокс",
    )
    assert got is sub_box


def test_get_active_subscription_returns_none_when_no_active(monkeypatch):
    athlete = SimpleNamespace(
        subscriptions=[SimpleNamespace(is_active=False, sport_type="Муай-тай")]
    )
    monkeypatch.setattr(
        "services.subscription_service.AthleteService.get_athlete_or_raise",
        lambda *_a, **_k: athlete,
    )

    got = SubscriptionService.get_active_subscription(session=object(), athlete_id=1)
    assert got is None


def test_create_subscription_rejects_unknown_type(monkeypatch):
    athlete = SimpleNamespace(sport_type="Муай-тай")
    monkeypatch.setattr(
        "services.subscription_service.AthleteService.get_athlete_or_raise",
        lambda *_a, **_k: athlete,
    )

    with pytest.raises(ValidationError):
        SubscriptionService.create_subscription(
            session=object(),
            athlete_id=1,
            subscription_type="yearly",
            sport_type="Муай-тай",
        )


def test_create_subscription_uses_athlete_sport_when_not_passed(monkeypatch):
    athlete = SimpleNamespace(sport_type="Муай-тай")
    captured = {}

    monkeypatch.setattr(
        "services.subscription_service.AthleteService.get_athlete_or_raise",
        lambda *_a, **_k: athlete,
    )

    def _fake_create_subscription(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(id=777)

    monkeypatch.setattr("services.subscription_service.db_create_subscription", _fake_create_subscription)

    created = SubscriptionService.create_subscription(
        session=object(),
        athlete_id=10,
        subscription_type="single",
        sport_type=None,
    )

    assert created.id == 777
    assert captured["sport_type"] == "Муай-тай"
    assert captured["subscription_type"] == "single"

