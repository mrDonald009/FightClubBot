"""Время отправки ежедневного дайджеста тренерам."""
from datetime import date, datetime
from types import SimpleNamespace

import pytest

from core.startup import (
    coach_daily_summary_send_datetime,
    format_coach_daily_summary_message,
    format_coach_training_start_reminder_message,
)


def _slot(hour, minute, individual=False):
    return SimpleNamespace(
        training_datetime=datetime(2026, 5, 19, hour, minute, 0),
        is_individual_format=individual,
    )


def test_default_send_at_09_00_without_early_individual():
    today = date(2026, 5, 19)
    slots = [_slot(17, 0, individual=False), _slot(20, 0, individual=False)]
    assert coach_daily_summary_send_datetime(today, slots) == datetime(2026, 5, 19, 9, 0, 0)


def test_shift_when_individual_at_09_00():
    today = date(2026, 5, 19)
    slots = [_slot(9, 0, individual=True)]
    assert coach_daily_summary_send_datetime(today, slots) == datetime(2026, 5, 19, 8, 0, 0)


def test_shift_when_individual_at_10_00_inclusive():
    today = date(2026, 5, 19)
    slots = [_slot(10, 0, individual=True)]
    assert coach_daily_summary_send_datetime(today, slots) == datetime(2026, 5, 19, 9, 0, 0)


def test_stays_at_09_00_when_individual_minus_one_hour_is_after_nine():
    today = date(2026, 5, 19)
    slots = [_slot(10, 30, individual=True), _slot(11, 0, individual=True)]
    assert coach_daily_summary_send_datetime(today, slots) == datetime(2026, 5, 19, 9, 0, 0)


def test_shift_one_hour_before_earliest_early_individual():
    today = date(2026, 5, 19)
    slots = [_slot(8, 30, individual=True), _slot(8, 0, individual=True)]
    assert coach_daily_summary_send_datetime(today, slots) == datetime(2026, 5, 19, 7, 0, 0)


@pytest.mark.parametrize(
    "hour,minute,expected_hour,expected_minute",
    [(8, 0, 7, 0), (8, 30, 7, 30)],
)
def test_shift_for_single_early_individual(hour, minute, expected_hour, expected_minute):
    today = date(2026, 5, 19)
    slots = [_slot(hour, minute, individual=True)]
    got = coach_daily_summary_send_datetime(today, slots)
    assert got == datetime(2026, 5, 19, expected_hour, expected_minute, 0)


def test_format_coach_daily_summary_message_with_details():
    today = date(2026, 5, 19)
    slots = [
        SimpleNamespace(
            training_datetime=datetime(2026, 5, 19, 17, 0, 0),
            sport_type="MMA",
            age_group="children",
            is_individual_format=False,
        ),
        SimpleNamespace(
            training_datetime=datetime(2026, 5, 19, 8, 0, 0),
            sport_type="MMA",
            age_group="adults",
            is_individual_format=True,
        ),
        SimpleNamespace(
            training_datetime=datetime(2026, 5, 19, 18, 30, 0),
            sport_type="MMA",
            age_group="middle",
            is_individual_format=False,
        ),
    ]
    text = format_coach_daily_summary_message(today, slots)
    assert "Доброе утро!" in text
    assert "Сегодня - 19.05.2026" in text
    assert "У Вас запланировано 3 тренировки:" in text
    assert "1. 08:00 — MMA | Индивидуальная" in text
    assert "2. 17:00 — MMA | Детская — Групповая" in text
    assert "3. 18:30 — MMA | Средняя — Групповая" in text


def test_format_coach_daily_summary_message_empty():
    text = format_coach_daily_summary_message(date(2026, 5, 19), [])
    assert "нет запланированных тренировок" in text


def test_format_coach_training_start_reminder_message():
    slot = SimpleNamespace(
        training_datetime=datetime(2026, 5, 19, 9, 0, 0),
        sport_type="MMA",
        age_group="adults",
        is_individual_format=True,
    )
    text = format_coach_training_start_reminder_message(slot)
    assert text.startswith("Тренировка началась!")
    assert "🕒 09:00 | MMA — Индивидуальная" in text
    assert "выберите присутствующих спортсменов" in text
