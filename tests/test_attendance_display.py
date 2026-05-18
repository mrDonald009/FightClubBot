"""Логика иконок/подписей посещения: без имплицитного «не был»."""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from utils.attendance_display import (
    attendance_icon_for_slot,
    attendance_label_ru_for_slot,
    is_effective_absent_no_row,
)
from utils.time_utils import training_end_time

pytestmark = pytest.mark.unit


def test_icon_pending_before_training_end():
    start = datetime(2026, 6, 1, 10, 0)
    end = training_end_time(start)
    now = end - timedelta(minutes=5)
    assert attendance_icon_for_slot(None, start, now=now) == "⏳"
    assert "Не отмечено" in attendance_label_ru_for_slot(None, start, now=now)


def test_icon_unmarked_no_row_after_training_end():
    start = datetime(2026, 6, 1, 10, 0)
    end = training_end_time(start)
    now = end + timedelta(minutes=30)
    assert attendance_icon_for_slot(None, start, now=now) == "⏳"
    assert is_effective_absent_no_row(None, start, now=now) is False


def test_icon_explicit_present_and_absent():
    start = datetime(2026, 6, 2, 18, 0)
    now = datetime(2099, 1, 1, 0, 0)
    att_yes = SimpleNamespace(attended=True)
    att_no = SimpleNamespace(attended=False)
    assert attendance_icon_for_slot(att_yes, start, now=now) == "✅"
    assert attendance_icon_for_slot(att_no, start, now=now) == "❌"
