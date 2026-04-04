"""Unit-тесты сервиса потока «Начать тренировку» (без Telegram)."""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from services.attendance_training_flow import (
    ATTENDANCE_LIST_PAGE_SIZE,
    build_step2_message_and_keyboard_rows,
    coach_training_access_error,
    parse_attendance_page_callback,
)


def test_parse_attendance_page_callback_valid():
    assert parse_attendance_page_callback("attpg_42_0") == (42, 0)
    assert parse_attendance_page_callback("attpg_1_3") == (1, 3)


def test_parse_attendance_page_callback_invalid():
    assert parse_attendance_page_callback("attpg_info") is None
    assert parse_attendance_page_callback("attpg_abc_0") is None
    assert parse_attendance_page_callback("mark_attendance_1_2") is None


def test_coach_training_access_error_admin_unrestricted():
    admin = SimpleNamespace()
    training = SimpleNamespace(coach_id=99, sport_type="Тайский Бокс")
    with patch(
        "services.attendance_training_flow.get_user_role",
        return_value="admin",
    ):
        assert coach_training_access_error(admin, training) is None


def test_coach_training_access_error_wrong_coach():
    coach = SimpleNamespace(id=5, sport_type="Тайский Бокс")
    training = SimpleNamespace(coach_id=9, sport_type="Тайский Бокс")

    with patch(
        "services.attendance_training_flow.get_user_role",
        return_value="coach",
    ):
        err = coach_training_access_error(coach, training)
        assert err is not None
        assert "только свои" in err.lower() or "свои" in err


def test_coach_training_access_error_sport_mismatch():
    coach = SimpleNamespace(id=1, sport_type="MMA", sport_type_rel=None)
    training = SimpleNamespace(coach_id=1, sport_type="Тайский Бокс")

    with patch(
        "services.attendance_training_flow.get_user_role",
        return_value="coach",
    ):
        err = coach_training_access_error(coach, training)
        assert err is not None


def test_build_step2_pagination_nav_when_many_athletes():
    training = SimpleNamespace(
        id=100,
        training_date=datetime(2026, 4, 4, 12, 30),
        sport_type="Тайский Бокс",
        age_group="children",
    )
    athletes = [
        SimpleNamespace(id=i, full_name=f"Спортсмен {i}") for i in range(ATTENDANCE_LIST_PAGE_SIZE + 5)
    ]
    msg, rows = build_step2_message_and_keyboard_rows(
        training, athletes, {}, page=0, page_size=ATTENDANCE_LIST_PAGE_SIZE
    )
    assert "Всего: <b>25</b>" in msg
    # последняя строка перед «К тренировкам» — навигация
    nav_found = any(
        any("attpg_100_1" in cd for _, cd in row) for row in rows[:-1]
    )
    assert nav_found
    bottom = rows[-1]
    assert any(cd == "attendance_training_list" for _, cd in bottom)


def test_build_step2_no_nav_when_few_athletes():
    training = SimpleNamespace(
        id=2,
        training_date=datetime(2026, 4, 4, 14, 0),
        sport_type="Тайский Бокс",
        age_group="adults",
    )
    athletes = [SimpleNamespace(id=1, full_name="Иванов Иван")]
    msg, rows = build_step2_message_and_keyboard_rows(training, athletes, {}, page=0)
    assert "Всего: <b>1</b>" in msg
    assert not any("attpg_" in str(row) for row in rows[:-1])
