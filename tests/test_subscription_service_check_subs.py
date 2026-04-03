"""Тесты сервиса абонементов (уровень orchestrator/service)."""
from services.subscription_service import SubscriptionService


def test_check_and_update_subscriptions_delegates_to_subscription_checker(monkeypatch):
    """SubscriptionService.check_and_update_subscriptions должен делегировать в checker."""
    captured = {}

    class _FakeChecker:
        @staticmethod
        def check_and_update_subscriptions():
            captured["called"] = True
            return 7

    monkeypatch.setattr("utils.subscription_checker.SubscriptionChecker", _FakeChecker)

    updated = SubscriptionService.check_and_update_subscriptions()

    assert captured.get("called") is True
    assert updated == 7
