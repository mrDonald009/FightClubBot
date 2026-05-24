"""UI-хелперы истории абонементов (индивидуальные брони)."""
from datetime import datetime

from database.models import Subscription
from handlers.card_handlers import (
    _HISTORY_FILTER_ALL,
    _HISTORY_FILTER_GROUP,
    _HISTORY_FILTER_INDIVIDUAL,
    _HISTORY_FILTER_SINGLE,
    _history_subscription_buckets,
    _history_subscription_button_label,
    _history_subscription_list_lines,
    _is_individual_subscription,
    _parse_subscription_history_callback,
    _subscription_history_list_callback,
    _subscription_picker_should_show,
)


def test_parse_subscription_history_callbacks():
    assert _parse_subscription_history_callback("subscription_history_42") == (
        42,
        _HISTORY_FILTER_ALL,
        False,
    )
    assert _parse_subscription_history_callback("subscription_history_athlete_7") == (
        7,
        _HISTORY_FILTER_ALL,
        True,
    )
    assert _parse_subscription_history_callback(
        "subscription_history_individual_15"
    ) == (15, _HISTORY_FILTER_INDIVIDUAL, False)
    assert _parse_subscription_history_callback(
        "subscription_history_individual_athlete_3"
    ) == (3, _HISTORY_FILTER_INDIVIDUAL, True)
    assert _parse_subscription_history_callback(
        "subscription_history_group_9"
    ) == (9, _HISTORY_FILTER_GROUP, False)
    assert _parse_subscription_history_callback(
        "subscription_history_group_athlete_2"
    ) == (2, _HISTORY_FILTER_GROUP, True)
    assert _parse_subscription_history_callback(
        "subscription_history_single_11"
    ) == (11, _HISTORY_FILTER_SINGLE, False)
    assert _parse_subscription_history_callback(
        "subscription_history_single_athlete_4"
    ) == (4, _HISTORY_FILTER_SINGLE, True)


def test_subscription_history_list_callback():
    assert _subscription_history_list_callback(5, athlete_self=False) == (
        "subscription_history_5"
    )
    assert _subscription_history_list_callback(
        5, history_filter=_HISTORY_FILTER_INDIVIDUAL, athlete_self=True
    ) == (
        "subscription_history_individual_athlete_5"
    )
    assert _subscription_history_list_callback(
        5, history_filter=_HISTORY_FILTER_GROUP, athlete_self=False
    ) == (
        "subscription_history_group_5"
    )
    assert _subscription_history_list_callback(
        5, history_filter=_HISTORY_FILTER_SINGLE, athlete_self=False
    ) == (
        "subscription_history_single_5"
    )


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
    assert "🥊" not in label
    assert "MMA" not in label

    block = _history_subscription_list_lines(sub, 1)
    assert "Индивидуальная бронь #101" in block
    assert "16.05.2026 10:30" in block
    assert "MMA" in block


def test_group_history_button_shows_period_only():
    sub = Subscription(
        id=50,
        subscription_type="monthly",
        discipline_key="mma_group",
        sport_type="MMA",
        is_active=False,
        start_date=datetime(2026, 4, 1, 0, 0),
        end_date=datetime(2026, 5, 1, 0, 0),
        trainings_total=12,
        trainings_remaining=0,
        created_at=datetime(2026, 4, 1, 9, 0),
    )
    label = _history_subscription_button_label(sub)
    assert label == "01.04.2026—01.05.2026"
    assert "Месячный" not in label


def test_single_history_button_shows_slot_time():
    sub = Subscription(
        id=60,
        subscription_type="single",
        discipline_key="mma_group",
        sport_type="MMA",
        is_active=False,
        start_date=datetime(2026, 5, 22, 9, 0),
        end_date=datetime(2026, 5, 22, 10, 0),
        trainings_total=1,
        trainings_remaining=0,
    )
    label = _history_subscription_button_label(sub)
    assert label == "22.05.2026 09:00"


def test_subscription_picker_should_show_only_for_multiple_active():
    assert _subscription_picker_should_show([object()], [object()])
    assert not _subscription_picker_should_show([object()], [])
    assert not _subscription_picker_should_show([], [object()])
