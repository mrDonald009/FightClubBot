"""UI-хелперы истории абонементов (индивидуальные брони)."""
from datetime import datetime

import pytest

from database.models import Subscription
from handlers.card_handlers import (
    _HISTORY_FILTER_ALL,
    _HISTORY_FILTER_GROUP,
    _HISTORY_FILTER_INDIVIDUAL,
    _count_past_individual_subscriptions,
    _history_subscription_button_label,
    _history_subscription_list_lines,
    _individual_slot_relative_hint,
    _individual_subscription_button_label,
    _individual_subscription_is_past_or_completed,
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


def test_individual_history_button_shows_slot_datetime():
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
    assert "#101" in label
    assert "16.05.2026 10:30" in label

    block = _history_subscription_list_lines(sub, 1)
    assert "Индивидуальная бронь #101" in block
    assert "16.05.2026 10:30" in block
    assert "MMA" in block


def test_group_history_button_shows_type_and_date():
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
    assert "Месячный" in label
    assert "01.04.2026" in label

    block = _history_subscription_list_lines(sub, 2)
    assert "Абонемент #50" in block
    assert "Месячный" in block
    assert "12" in block


def _make_individual_sub(**kwargs):
    defaults = dict(
        subscription_type="individual",
        discipline_key="mma_individual",
        sport_type="MMA",
        is_active=True,
        trainings_total=1,
        trainings_remaining=1,
    )
    defaults.update(kwargs)
    return Subscription(**defaults)


def test_individual_past_or_completed_by_end_date():
    now = datetime(2026, 5, 24, 12, 0)
    sub = _make_individual_sub(
        start_date=datetime(2026, 5, 23, 16, 0),
        end_date=datetime(2026, 5, 23, 17, 0),
    )
    assert _individual_subscription_is_past_or_completed(sub, now=now)


def test_individual_past_or_completed_by_zero_trainings():
    now = datetime(2026, 5, 24, 12, 0)
    sub = _make_individual_sub(
        start_date=datetime(2026, 5, 25, 10, 30),
        end_date=datetime(2026, 5, 25, 11, 30),
        trainings_remaining=0,
    )
    assert _individual_subscription_is_past_or_completed(sub, now=now)


def test_individual_upcoming_not_past():
    now = datetime(2026, 5, 23, 12, 0)
    sub = _make_individual_sub(
        start_date=datetime(2026, 5, 24, 10, 30),
        end_date=datetime(2026, 5, 24, 11, 30),
    )
    assert not _individual_subscription_is_past_or_completed(sub, now=now)


def test_individual_slot_relative_hint_tomorrow():
    now = datetime(2026, 5, 23, 12, 0)
    sub = _make_individual_sub(start_date=datetime(2026, 5, 24, 10, 30))
    assert _individual_slot_relative_hint(sub, now=now) == "завтра в 10:30"


def test_individual_picker_button_label_shows_relative_time():
    now = datetime(2026, 5, 23, 12, 0)
    sub = _make_individual_sub(start_date=datetime(2026, 5, 24, 10, 30))
    label = _individual_subscription_button_label(sub, now=now)
    assert "24.05 10:30" in label
    assert "завтра в 10:30" in label
    assert "(MMA)" in label
    assert label.startswith("🟢")


def test_individual_picker_button_label_today_uses_green_icon():
    now = datetime(2026, 5, 24, 9, 0)
    sub = _make_individual_sub(start_date=datetime(2026, 5, 24, 10, 30))
    label = _individual_subscription_button_label(sub, now=now)
    assert label.startswith("🟢")
    assert "сегодня в 10:30" in label


def test_count_past_individual_subscriptions():
    now = datetime(2026, 5, 24, 12, 0)
    past = _make_individual_sub(
        id=1,
        start_date=datetime(2026, 5, 23, 16, 0),
        end_date=datetime(2026, 5, 23, 17, 0),
    )
    upcoming = _make_individual_sub(
        id=2,
        start_date=datetime(2026, 5, 25, 10, 30),
        end_date=datetime(2026, 5, 25, 11, 30),
    )
    group = Subscription(
        id=3,
        subscription_type="monthly",
        discipline_key="mma_group",
        sport_type="MMA",
        is_active=True,
    )
    assert _count_past_individual_subscriptions([past, upcoming, group], now=now) == 1


def test_subscription_picker_should_show_only_for_multiple_active():
    assert _subscription_picker_should_show([object()], [object()])
    assert not _subscription_picker_should_show([object()], [])
    assert not _subscription_picker_should_show([], [object()])
