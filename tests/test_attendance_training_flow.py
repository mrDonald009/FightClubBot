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
    _surname_initials_button_label,
    build_step2_message_and_keyboard_rows,
    coach_training_access_error,
    fetch_athletes_for_training_slot,
    format_today_trainings_count_ru,
    is_training_in_live_attendance_window,
    parse_attendance_direct_mark_callback,
    parse_attendance_name_column_callback,
    parse_attendance_page_callback,
    resolve_training_from_attendance_callback,
)
from utils.training_manager import TrainingManager

pytestmark = pytest.mark.unit


def test_parse_attendance_page_callback_valid():
    assert parse_attendance_page_callback("attpg_42_0") == (42, 0)
    assert parse_attendance_page_callback("attpg_1_3") == (1, 3)


def test_parse_attendance_page_callback_invalid():
    assert parse_attendance_page_callback("attpg_info") is None
    assert parse_attendance_page_callback("attpg_abc_0") is None
    assert parse_attendance_page_callback("mark_attendance_1_2") is None


def test_parse_attendance_direct_mark_callback_valid():
    assert parse_attendance_direct_mark_callback("atmark_10_20_1") == (10, 20, True)
    assert parse_attendance_direct_mark_callback("atmark_10_20_0") == (10, 20, False)


def test_parse_attendance_direct_mark_callback_invalid():
    assert parse_attendance_direct_mark_callback("atmark_1_2") is None
    assert parse_attendance_direct_mark_callback("atmark_a_b_1") is None
    assert parse_attendance_direct_mark_callback("mark_present") is None


def test_parse_attendance_name_column_callback():
    assert parse_attendance_name_column_callback("attnm_10_20") == (10, 20)
    assert parse_attendance_name_column_callback("attnm_x_1") is None


def test_surname_initials_button_label():
    assert _surname_initials_button_label("Морозов Егор Иванович") == "Морозов Е.И."
    assert _surname_initials_button_label("Иванов Иван") == "Иванов И."
    assert _surname_initials_button_label("Волков") == "Волков"


def test_is_training_in_live_attendance_window():
    tr = SimpleNamespace(training_date=datetime(2026, 5, 11, 11, 30))
    assert is_training_in_live_attendance_window(tr, now=datetime(2026, 5, 11, 11, 30))
    assert is_training_in_live_attendance_window(tr, now=datetime(2026, 5, 11, 12, 30))
    assert is_training_in_live_attendance_window(tr, now=datetime(2026, 5, 11, 13, 0))
    assert not is_training_in_live_attendance_window(tr, now=datetime(2026, 5, 11, 11, 29))
    assert not is_training_in_live_attendance_window(tr, now=datetime(2026, 5, 11, 13, 1))


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
    assert "Отметьте присутсвтующих до окончания тренировки." in msg
    assert "при отсутствии отметки" in msg
    assert "Не был" in msg
    assert any(
        any(cd == "atmark_100_0_1" for _, cd in row) for row in rows
    )
    # последняя строка перед «К тренировкам на сегодня» — навигация
    nav_found = any(
        any("attpg_100_1" in cd for _, cd in row) for row in rows[:-2]
    )
    assert nav_found
    assert rows[-2] == [("🔙 К тренировкам на сегодня", "attendance_training_list")]
    assert rows[-1] == [("🏠 В меню", "back_to_menu_main")]


def test_build_step2_name_button_shows_status_icon_when_marked():
    training = SimpleNamespace(
        id=2,
        training_date=datetime(2026, 4, 4, 14, 0),
        sport_type="Тайский Бокс",
        age_group="adults",
    )
    athletes = [SimpleNamespace(id=1, full_name="Иванов Иван Петрович")]
    att_present = SimpleNamespace(attended=True, locked_at=None)
    _msg, rows = build_step2_message_and_keyboard_rows(
        training, athletes, {1: att_present}, page=0
    )
    assert rows[0][0][0] == "✅ Иванов И.П."

    att_absent = SimpleNamespace(attended=False, locked_at=None)
    _msg2, rows2 = build_step2_message_and_keyboard_rows(
        training, athletes, {1: att_absent}, page=0
    )
    assert rows2[0][0][0] == "❌ Иванов И.П."


def test_build_step2_no_nav_when_few_athletes():
    training = SimpleNamespace(
        id=2,
        training_date=datetime(2026, 4, 4, 14, 0),
        sport_type="Тайский Бокс",
        age_group="adults",
    )
    athletes = [SimpleNamespace(id=1, full_name="Иванов Иван Петрович")]
    msg, rows = build_step2_message_and_keyboard_rows(training, athletes, {}, page=0)
    assert "Отметьте присутсвтующих до окончания тренировки." in msg
    assert "при отсутствии отметки" in msg
    assert rows[0][0] == ("⏳ Иванов И.П.", "attnm_2_1")
    assert rows[0][1] == ("✅ Был", "atmark_2_1_1")
    assert rows[0][2] == ("❌ Не был", "atmark_2_1_0")
    assert rows[-2] == [("🔙 К тренировкам на сегодня", "attendance_training_list")]
    assert rows[-1] == [("🏠 В меню", "back_to_menu_main")]
    assert not any("attpg_" in str(row) for row in rows[:-2])


def test_build_step2_individual_slot_title_without_age_group():
    training = SimpleNamespace(
        id=3,
        training_date=datetime(2026, 5, 11, 14, 0),
        sport_type="MMA",
        age_group="adults",
        training_format="individual",
    )
    athletes = [SimpleNamespace(id=1, full_name="Тестов Тест Тестович")]
    msg, _rows = build_step2_message_and_keyboard_rows(training, athletes, {}, page=0)
    assert "11.05.2026 14:00" in msg
    assert "Индивидуальная" in msg
    assert "MMA" in msg
    assert "взрослая группа" not in msg
    assert "детская группа" not in msg


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


@pytest.mark.db
def test_fetch_athletes_individual_slot_lists_all_ages_same_start():
    """Индивидуальный слот: спортсмены дети и взрослые с тем же start_date попадают в один список."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    st = SportType(name="MMA", display_name="MMA")
    session.add(st)
    session.flush()
    coach = Coach(telegram_id=9002, sport_type_id=st.id, sport_type="MMA")
    session.add(coach)
    session.flush()

    slot_start = datetime(2026, 5, 11, 14, 0, 0)
    slot_end = slot_start + timedelta(hours=1, minutes=30)

    adult = Athlete(
        full_name="Попов Алексей Сергеевич",
        age_group="adults",
        sport_type="MMA",
        created_by=coach.id,
    )
    child = Athlete(
        full_name="Морозов Никита Иванович",
        age_group="children",
        sport_type="MMA",
        created_by=coach.id,
    )
    session.add_all([adult, child])
    session.flush()

    for ath, dk in (
        (adult, "mma_adults_ind_slot"),
        (child, "mma_children_ind_slot"),
    ):
        session.add(
            Subscription(
                athlete_id=ath.id,
                discipline_key=dk,
                sport_type="MMA",
                subscription_type="individual",
                start_date=slot_start,
                end_date=slot_end,
                is_active=True,
                trainings_total=1,
                trainings_remaining=1,
            )
        )
    session.flush()

    training = Training(
        sport_type="MMA",
        age_group="adults",
        training_date=slot_start,
        coach_id=coach.id,
        training_format="individual",
        is_cancelled=False,
    )
    session.add(training)
    session.commit()

    athletes, _ = fetch_athletes_for_training_slot(session, training)
    names = {a.full_name for a in athletes}
    assert names == {"Попов Алексей Сергеевич", "Морозов Никита Иванович"}

    session.close()


@pytest.mark.db
def test_resolve_virtual_slot_reuses_existing_group_on_same_day():
    """
    Виртуальный слот «Отметить посещения» не должен создавать второй group-слот
    в тот же день, если уже есть активная групповая тренировка того же тренера.
    Время берётся из актуального TRAINING_SCHEDULE (без хардкода 17:00/18:00).
    """
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    sport_type = "MMA"
    age_group = "children"
    schedule = TrainingManager.TRAINING_SCHEDULE[sport_type][age_group]
    training_weekday = schedule["days"][0]
    expected_time = TrainingManager.get_time_str_for_weekday(schedule, training_weekday)
    expected_hour, expected_minute = map(int, expected_time.split(":"))

    base_day = datetime(2026, 5, 1)
    while base_day.weekday() != training_weekday:
        base_day += timedelta(days=1)
    today = base_day.replace(hour=9, minute=0, second=0, microsecond=0)
    expected_dt = today.replace(hour=expected_hour, minute=expected_minute, second=0, microsecond=0)

    legacy_dt = expected_dt + timedelta(minutes=60)
    if legacy_dt.date() != expected_dt.date():
        legacy_dt = expected_dt - timedelta(minutes=60)

    st = SportType(name=sport_type, display_name=sport_type)
    session.add(st)
    session.flush()
    coach = Coach(telegram_id=9010, sport_type_id=st.id, sport_type=sport_type)
    session.add(coach)
    session.flush()

    legacy_group = Training(
        sport_type=sport_type,
        age_group=age_group,
        training_date=legacy_dt,
        coach_id=coach.id,
        training_format=None,
        is_cancelled=False,
    )
    session.add(legacy_group)
    session.commit()

    callback = "select_mark_training_virtual_slot1"
    virtual_slots = {
        "slot1": {
            "sport_type": sport_type,
            "age_group": age_group,
            "hour": expected_hour,
            "minute": expected_minute,
            "coach_id": coach.id,
        }
    }

    with patch("services.attendance_training_flow.now_moscow", return_value=today):
        training, created, err = resolve_training_from_attendance_callback(
            session, callback, virtual_slots
        )

    assert err is None
    assert created is False
    assert training is not None
    assert training.id == legacy_group.id
    assert training.training_date == expected_dt
    assert (
        session.query(Training)
        .filter(
            Training.sport_type == sport_type,
            Training.age_group == age_group,
            Training.coach_id == coach.id,
            Training.is_cancelled.is_(False),
        )
        .count()
        == 1
    )
    session.close()
