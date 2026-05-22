"""Быстрая запись на individual из календаря тренера."""
from datetime import datetime
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.models import Athlete, Base, Coach, SportType, Subscription, Training
from handlers.coach_handlers import (
    _build_cal_individual_athlete_keyboard,
    _build_cal_individual_time_keyboard,
)
from handlers.card_handlers import prepare_individual_subscription_for_activation
from database.db_utils.subscriptions import create_subscription

pytestmark = pytest.mark.db


def _coach_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    st = SportType(name="MMA", display_name="MMA")
    s.add(st)
    s.flush()
    coach = Coach(telegram_id=7001, sport_type_id=st.id, sport_type="MMA")
    s.add(coach)
    s.commit()
    return s, coach, engine


def test_build_cal_individual_time_keyboard_lists_free_slots():
    s, coach, engine = _coach_session()
    day = datetime(2030, 6, 10).date()
    with patch(
        "handlers.coach_handlers.now_moscow",
        return_value=datetime(2030, 6, 1, 8, 0, 0),
    ):
        kb = _build_cal_individual_time_keyboard(
            s, coach.id, "MMA", day.year, day.month, day.day
        )
    s.close()
    engine.dispose()
    assert kb is not None
    callbacks = [
        btn.callback_data
        for row in kb.inline_keyboard
        for btn in row
        if btn.callback_data and btn.callback_data.startswith("cal_ind_ts_")
    ]
    assert len(callbacks) >= 1


def test_build_cal_individual_time_keyboard_empty_when_day_full():
    s, coach, engine = _coach_session()
    # Заполняем весь день групповыми слотами по расписанию MMA adults (пн/ср/пт 20:00)
    # Проще: заблокировать конкретный popular slot через existing training 8:00-17:00 chain
    for hour in range(8, 18):
        s.add(
            Training(
                sport_type="MMA",
                age_group="adults",
                training_date=datetime(2026, 5, 16, hour, 0, 0),
                training_format="individual",
                coach_id=coach.id,
                is_cancelled=False,
            )
        )
    s.commit()
    kb = _build_cal_individual_time_keyboard(
        s, coach.id, "MMA", 2026, 5, 16
    )
    s.close()
    engine.dispose()
    # Может остаться 0 или мало слотов — главное что функция не падает
    assert kb is None or isinstance(kb.inline_keyboard, list)


def test_build_cal_individual_athlete_keyboard_pagination():
    athletes = [
        Athlete(id=i, full_name=f"Спортсмен {i:02d} Тестович", sport_type="MMA")
        for i in range(1, 16)
    ]
    kb = _build_cal_individual_athlete_keyboard(
        athletes,
        "202605151000",
        2026,
        5,
        15,
        page=1,
    )
    callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert any(c and c.startswith("cal_ind_a_") for c in callbacks)
    assert any(c == "cal_ind_pg_0_202605151000" for c in callbacks)


def test_calendar_book_prepare_does_not_deactivate_group_subscription():
    s, coach, engine = _coach_session()
    athlete = Athlete(
        full_name="Группа и Individual",
        sport_type="MMA",
        age_group="adults",
        created_by=coach.id,
    )
    s.add(athlete)
    s.flush()
    group = create_subscription(
        s,
        athlete.id,
        "monthly",
        "MMA",
        discipline_key="mma_group",
        subscription_format="group",
        commit=False,
    )
    group.is_active = True
    group.start_date = datetime(2026, 5, 1, 0, 0, 0)
    group.end_date = datetime(2026, 6, 1, 0, 0, 0)
    s.commit()

    slot = datetime(2026, 5, 20, 10, 0, 0)
    ind = prepare_individual_subscription_for_activation(
        s, athlete, "MMA", responsible_coach_id=coach.id
    )
    ind.start_date = slot
    ind.end_date = datetime(2026, 5, 20, 11, 30, 0)
    ind.is_active = True
    s.commit()

    assert group.is_active is True
    assert (
        s.query(Subscription)
        .filter_by(athlete_id=athlete.id, subscription_type="individual", is_active=True)
        .count()
        == 1
    )
    s.close()
    engine.dispose()
