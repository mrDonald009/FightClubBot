"""UI-хелперы истории абонементов (индивидуальные брони)."""
from datetime import datetime

from database.models import Subscription
from handlers.card_handlers import (
    _HISTORY_FILTER_ALL,
    _HISTORY_FILTER_GROUP,
    _HISTORY_FILTER_INDIVIDUAL,
    _HISTORY_FILTER_SINGLE,
    _VISIT_HISTORY_MODE_MONTH,
    _VISIT_HISTORY_MODE_OLDER_MENU,
    _VISIT_HISTORY_MODE_OLDER_MONTH,
    _filter_subscriptions_last_month,
    _filter_subscriptions_older_month,
    _group_subscriptions_by_month,
    _has_older_subscriptions,
    _history_subscription_buckets,
    _history_subscription_button_label,
    _history_subscription_list_lines,
    _is_individual_subscription,
    _parse_subscription_history_callback,
    _subscription_history_list_callback,
    _subscription_history_section_callback,
    _subscription_picker_should_show,
)


def test_parse_subscription_history_callbacks():
    assert _parse_subscription_history_callback("subscription_history_42") == (
        42,
        _HISTORY_FILTER_ALL,
        False,
        _VISIT_HISTORY_MODE_MONTH,
        None,
    )
    assert _parse_subscription_history_callback("subscription_history_athlete_7") == (
        7,
        _HISTORY_FILTER_ALL,
        True,
        _VISIT_HISTORY_MODE_MONTH,
        None,
    )
    assert _parse_subscription_history_callback(
        "subscription_history_individual_15"
    ) == (15, _HISTORY_FILTER_INDIVIDUAL, False, _VISIT_HISTORY_MODE_MONTH, None)
    assert _parse_subscription_history_callback(
        "subscription_history_individual_athlete_3"
    ) == (3, _HISTORY_FILTER_INDIVIDUAL, True, _VISIT_HISTORY_MODE_MONTH, None)
    assert _parse_subscription_history_callback(
        "subscription_history_group_9"
    ) == (9, _HISTORY_FILTER_GROUP, False, _VISIT_HISTORY_MODE_MONTH, None)
    assert _parse_subscription_history_callback(
        "subscription_history_single_11_older"
    ) == (11, _HISTORY_FILTER_SINGLE, False, _VISIT_HISTORY_MODE_OLDER_MENU, None)
    assert _parse_subscription_history_callback(
        "subscription_history_individual_15_older_202605"
    ) == (15, _HISTORY_FILTER_INDIVIDUAL, False, _VISIT_HISTORY_MODE_OLDER_MONTH, "202605")


def test_subscription_history_list_callback():
    assert _subscription_history_list_callback(5, athlete_self=False) == (
        "subscription_history_5"
    )
    assert _subscription_history_list_callback(
        5, history_filter=_HISTORY_FILTER_INDIVIDUAL, athlete_self=True
    ) == (
        "subscription_history_individual_athlete_5"
    )


def test_subscription_history_section_callback():
    assert _subscription_history_section_callback(
        5,
        history_filter=_HISTORY_FILTER_GROUP,
        athlete_self=False,
        month_mode=_VISIT_HISTORY_MODE_OLDER_MENU,
    ) == "subscription_history_group_5_older"
    assert _subscription_history_section_callback(
        5,
        history_filter=_HISTORY_FILTER_INDIVIDUAL,
        athlete_self=False,
        month_mode=_VISIT_HISTORY_MODE_OLDER_MONTH,
        older_yyyymm="202605",
    ) == "subscription_history_individual_5_older_202605"


def test_history_subscription_buckets():
    individual = Subscription(
        id=1,
        subscription_type="individual",
        discipline_key="mma_individual",
    )
    monthly = Subscription(id=2, subscription_type="monthly", discipline_key="mma_group")
    single = Subscription(id=3, subscription_type="single", discipline_key="mma_group")
    ind, mon, sgl = _history_subscription_buckets([individual, monthly, single])
    assert [s.id for s in ind] == [1]
    assert [s.id for s in mon] == [2]
    assert [s.id for s in sgl] == [3]


def test_individual_history_button_is_minimal():
    sub = Subscription(
        id=101,
        subscription_type="individual",
        discipline_key="mma_individual",
        sport_type="MMA",
        is_active=True,
        start_date=datetime(2026, 5, 16, 10, 30),
        end_date=datetime(2026, 5, 16, 11, 30),
        trainings_total=1,
        trainings_remaining=1,
        created_at=datetime(2026, 5, 10, 12, 0),
    )
    assert _is_individual_subscription(sub)
    label = _history_subscription_button_label(sub)
    assert label == "16.05.2026 10:30"

    block = _history_subscription_list_lines(sub, 1)
    assert "Индивидуальная бронь #101" in block
    assert "16.05.2026 10:30" in block


def test_subscription_history_month_filters():
    now = datetime(2026, 5, 24, 12, 0)
    recent = Subscription(
        id=1,
        subscription_type="individual",
        discipline_key="mma_individual",
        start_date=datetime(2026, 5, 20, 9, 0),
    )
    older = Subscription(
        id=2,
        subscription_type="individual",
        discipline_key="mma_individual",
        start_date=datetime(2026, 4, 10, 9, 0),
    )
    subs = [recent, older]
    assert len(_filter_subscriptions_last_month(subs, now=now)) == 1
    assert _has_older_subscriptions(subs, now=now)
    grouped = _group_subscriptions_by_month(subs, now=now)
    assert (2026, 4) in grouped
    april = _filter_subscriptions_older_month(subs, year=2026, month=4, now=now)
    assert len(april) == 1
    assert april[0].id == 2


def test_subscription_picker_should_show_only_for_multiple_active():
    assert _subscription_picker_should_show([object()], [object()])
    assert not _subscription_picker_should_show([object()], [])
    assert not _subscription_picker_should_show([], [object()])
