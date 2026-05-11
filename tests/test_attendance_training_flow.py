"""Unit-тесты сервиса потока «Отметить посещения» (без Telegram)."""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.models import Athlete, Base, Coach, SportType, Subscription, Training
from services.attendance_training_flow import (
    ATTENDANCE_LIST_PAGE_SIZE,
    build_step2_message_and_keyboard_rows,
    coach_training_access_error,
    fetch_athletes_for_training_slot,
    format_today_trainings_count_ru,
    parse_attendance_page_callback,
)

pytestmark = pytest.mark.unit


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
        assert "ваши" in err.lower() or "тренером" in err.lower()


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
    assert "04.04.2026 12:30" in msg
    assert "Шаг 2 из 2" in msg
    # последняя строка перед «К тренировкам на сегодня» — навигация
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
    assert "Шаг 2 из 2" in msg
    assert not any("attpg_" in str(row) for row in rows[:-1])


@pytest.mark.parametrize(
    "n,expected_suffix",
    [
        (1, "1 тренировка"),
        (2, "2 тренировки"),
        (5, "5 тренировок"),
        (11, "11 тренировок"),
        (22, "22 тренировки"),
    ],
)
def test_format_today_trainings_count_ru(n, expected_suffix):
    assert format_today_trainings_count_ru(n) == expected_suffix


@pytest.mark.db
def test_fetch_athletes_individual_slot_only_matching_subscription():
    """На индивидуальной паре не показывать спортсменов с групповым абонементом за тот же день."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    st = SportType(name="MMA", display_name="MMA")
    session.add(st)
    session.flush()
    coach = Coach(telegram_id=9001, sport_type_id=st.id, sport_type="MMA")
    session.add(coach)
    session.flush()

    slot_start = datetime(2026, 5, 11, 11, 30, 0)
    slot_end = slot_start + timedelta(hours=1, minutes=30)

    ind_ath = Athlete(
        full_name="Морозов Егор Иванович",
        age_group="adults",
        sport_type="MMA",
        created_by=coach.id,
    )
    grp_ath = Athlete(
        full_name="Волков Алексей Сергеевич",
        age_group="adults",
        sport_type="MMA",
        created_by=coach.id,
    )
    session.add_all([ind_ath, grp_ath])
    session.flush()

    session.add(
        Subscription(
            athlete_id=ind_ath.id,
            discipline_key="mma_adults_ind_1",
            sport_type="MMA",
            subscription_type="individual",
            start_date=slot_start,
            end_date=slot_end,
            is_active=True,
            trainings_total=1,
            trainings_remaining=1,
        )
    )
    session.add(
        Subscription(
            athlete_id=grp_ath.id,
            discipline_key="mma_adults_monthly_1",
            sport_type="MMA",
            subscription_type="monthly",
            start_date=datetime(2026, 5, 1, 0, 0, 0),
            end_date=datetime(2026, 5, 31, 23, 59, 59),
            is_active=True,
            trainings_total=12,
            trainings_remaining=8,
        )
    )
    session.flush()

    training_ind = Training(
        sport_type="MMA",
        age_group="adults",
        training_date=slot_start,
        coach_id=coach.id,
        training_format="individual",
        is_cancelled=False,
    )
    session.add(training_ind)
    session.commit()

    athletes, _ = fetch_athletes_for_training_slot(session, training_ind)
    names = {a.full_name for a in athletes}
    assert names == {"Морозов Егор Иванович"}

    training_grp = Training(
        sport_type="MMA",
        age_group="adults",
        training_date=datetime(2026, 5, 11, 20, 0, 0),
        coach_id=coach.id,
        training_format=None,
        is_cancelled=False,
    )
    session.add(training_grp)
    session.commit()

    athletes_g, _ = fetch_athletes_for_training_slot(session, training_grp)
    names_g = {a.full_name for a in athletes_g}
    assert "Волков Алексей Сергеевич" in names_g
    assert "Морозов Егор Иванович" not in names_g

    session.close()
