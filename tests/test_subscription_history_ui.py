"""UI-хелперы истории абонементов (индивидуальные брони)."""
from datetime import datetime

import pytest

from database.models import Subscription
from handlers.card_handlers import (
    _history_subscription_button_label,
    _history_subscription_list_lines,
    _is_individual_subscription,
    _parse_subscription_history_callback,
    _subscription_history_list_callback,
)


def test_parse_subscription_history_callbacks():
    assert _parse_subscription_history_callback("subscription_history_42") == (
        42,
        False,
        False,
    )
    assert _parse_subscription_history_callback("subscription_history_athlete_7") == (
        7,
        False,
        True,
    )
    assert _parse_subscription_history_callback(
        "subscription_history_individual_15"
    ) == (15, True, False)
    assert _parse_subscription_history_callback(
        "subscription_history_individual_athlete_3"
    ) == (3, True, True)


def test_subscription_history_list_callback():
    assert _subscription_history_list_callback(5, filter_individual=False, athlete_self=False) == (
        "subscription_history_5"
    )
    assert _subscription_history_list_callback(5, filter_individual=True, athlete_self=True) == (
        "subscription_history_individual_athlete_5"
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
