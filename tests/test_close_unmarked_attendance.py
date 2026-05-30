from datetime import datetime
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db_utils.close_unmarked_attendance import (
    close_unmarked_attendance_after_grace,
    lock_attendances_after_calendar_day_end,
)
from database.models import Athlete, Attendance, Base, GlobalFreeze, Subscription, Training


@pytest.mark.db
def test_close_unmarked_group_slot_uses_group_subscription_when_both_active():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    athlete = Athlete(
        full_name="Тестов Тест Тестович",
        age_group="adults",
        sport_type="MMA",
        created_by=1,
    )
    session.add(athlete)
    session.flush()

    # Создаем individual раньше, чтобы у него был меньший id.
    sub_individual = Subscription(
        athlete_id=athlete.id,
        discipline_key="mma_individual",
        sport_type="MMA",
        subscription_type="individual",
        start_date=datetime(2026, 5, 11, 14, 0, 0),
        end_date=datetime(2026, 5, 11, 15, 30, 0),
        trainings_total=1,
        trainings_remaining=1,
        is_active=True,
        created_at=datetime(2026, 5, 1, 9, 0, 0),
    )
    sub_group = Subscription(
        athlete_id=athlete.id,
        discipline_key="mma_group",
        sport_type="MMA",
        subscription_type="monthly",
        start_date=datetime(2026, 5, 1, 0, 0, 0),
        end_date=datetime(2026, 6, 30, 23, 59, 59),
        trainings_total=12,
        trainings_remaining=12,
        is_active=True,
        created_at=datetime(2026, 5, 1, 9, 5, 0),
    )
    session.add_all([sub_individual, sub_group])
    session.flush()

    training = Training(
        sport_type="MMA",
        age_group="adults",
        training_date=datetime(2026, 5, 11, 18, 0, 0),
        training_format=None,
        is_cancelled=False,
        coach_id=1,
    )
    session.add(training)
    session.commit()

    with patch(
        "database.db_utils.close_unmarked_attendance.now_moscow",
        return_value=datetime(2026, 5, 12, 12, 0, 0),
    ):
        inserted = close_unmarked_attendance_after_grace(session, lookback_days=10)

    assert inserted == 1
    attendance_rows = session.query(Attendance).all()
    assert len(attendance_rows) == 1
    assert attendance_rows[0].athlete_id == athlete.id
    assert attendance_rows[0].training_id == training.id
    assert attendance_rows[0].subscription_id == sub_group.id

    session.close()


@pytest.mark.db
def test_lock_attendances_after_calendar_day_end_only_past_days():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    training_yesterday = Training(
        sport_type="MMA",
        age_group="adults",
        training_date=datetime(2026, 5, 29, 17, 0, 0),
        coach_id=1,
        is_cancelled=False,
    )
    training_today = Training(
        sport_type="MMA",
        age_group="adults",
        training_date=datetime(2026, 5, 30, 17, 0, 0),
        coach_id=1,
        is_cancelled=False,
    )
    session.add_all([training_yesterday, training_today])
    session.flush()

    att_old = Attendance(
        athlete_id=1,
        training_id=training_yesterday.id,
        subscription_id=1,
        attended=True,
        marked_by=100,
    )
    att_today = Attendance(
        athlete_id=2,
        training_id=training_today.id,
        subscription_id=2,
        attended=False,
        marked_by=100,
    )
    session.add_all([att_old, att_today])
    session.commit()

    with patch(
        "database.db_utils.close_unmarked_attendance.now_moscow",
        return_value=datetime(2026, 5, 30, 12, 0, 0),
    ):
        n = lock_attendances_after_calendar_day_end(session)
        session.commit()

    assert n == 1
    session.refresh(att_old)
    session.refresh(att_today)
    assert att_old.locked_at is not None
    assert att_today.locked_at is None

    session.close()


@pytest.mark.db
def test_lock_attendances_after_calendar_day_end_present_not_deducted_in_global_freeze():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    athlete = Athlete(
        full_name="Фриз Тест",
        age_group="adults",
        sport_type="MMA",
        created_by=1,
    )
    session.add(athlete)
    session.flush()

    subscription = Subscription(
        athlete_id=athlete.id,
        discipline_key="mma_group",
        sport_type="MMA",
        subscription_type="monthly",
        start_date=datetime(2026, 5, 1, 0, 0, 0),
        end_date=datetime(2026, 6, 30, 23, 59, 59),
        trainings_total=12,
        trainings_remaining=5,
        is_active=True,
        created_at=datetime(2026, 5, 1, 9, 0, 0),
    )
    session.add(subscription)
    session.flush()

    training = Training(
        sport_type="MMA",
        age_group="adults",
        training_date=datetime(2026, 6, 1, 18, 0, 0),
        coach_id=1,
        is_cancelled=False,
    )
    session.add(training)
    session.flush()

    session.add(
        GlobalFreeze(
            title="Каникулы",
            start_date=datetime(2026, 6, 1, 0, 0, 0),
            end_date=datetime(2026, 6, 10, 23, 59, 59),
            is_active=True,
            created_by=1,
            created_at=datetime(2026, 6, 6, 12, 0, 0),
        )
    )
    session.flush()

    attendance = Attendance(
        athlete_id=athlete.id,
        training_id=training.id,
        subscription_id=subscription.id,
        attended=True,
        marked_by=100,
    )
    session.add(attendance)
    session.commit()

    with patch(
        "database.db_utils.close_unmarked_attendance.now_moscow",
        return_value=datetime(2026, 6, 6, 12, 0, 0),
    ):
        locked = lock_attendances_after_calendar_day_end(session)
        session.commit()

    session.refresh(attendance)
    session.refresh(subscription)
    assert locked == 1
    assert attendance.locked_at is not None
    assert subscription.trainings_remaining == 5

    session.close()


@pytest.mark.db
def test_close_unmarked_attendance_skips_rows_in_global_freeze():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    athlete = Athlete(
        full_name="Авто Фриз",
        age_group="adults",
        sport_type="MMA",
        created_by=1,
    )
    session.add(athlete)
    session.flush()

    subscription = Subscription(
        athlete_id=athlete.id,
        discipline_key="mma_group",
        sport_type="MMA",
        subscription_type="monthly",
        start_date=datetime(2026, 5, 1, 0, 0, 0),
        end_date=datetime(2026, 6, 30, 23, 59, 59),
        trainings_total=12,
        trainings_remaining=7,
        is_active=True,
        created_at=datetime(2026, 5, 1, 9, 0, 0),
    )
    session.add(subscription)
    session.flush()

    training = Training(
        sport_type="MMA",
        age_group="adults",
        training_date=datetime(2026, 5, 11, 18, 0, 0),
        coach_id=1,
        is_cancelled=False,
    )
    session.add(training)
    session.flush()

    session.add(
        GlobalFreeze(
            title="Майская пауза",
            start_date=datetime(2026, 5, 10, 0, 0, 0),
            end_date=datetime(2026, 5, 12, 23, 59, 59),
            is_active=True,
            created_by=1,
            created_at=datetime(2026, 5, 10, 8, 0, 0),
        )
    )
    session.commit()

    with patch(
        "database.db_utils.close_unmarked_attendance.now_moscow",
        return_value=datetime(2026, 5, 12, 12, 0, 0),
    ):
        inserted = close_unmarked_attendance_after_grace(session, lookback_days=10)
        session.commit()

    assert inserted == 0
    assert session.query(Attendance).count() == 0
    session.refresh(subscription)
    assert subscription.trainings_remaining == 7

    session.close()


@pytest.mark.db
def test_close_unmarked_attendance_outside_global_freeze_deducts_once():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    athlete = Athlete(
        full_name="Контроль Без Фриза",
        age_group="adults",
        sport_type="MMA",
        created_by=1,
    )
    session.add(athlete)
    session.flush()

    subscription = Subscription(
        athlete_id=athlete.id,
        discipline_key="mma_group",
        sport_type="MMA",
        subscription_type="monthly",
        start_date=datetime(2026, 5, 1, 0, 0, 0),
        end_date=datetime(2026, 6, 30, 23, 59, 59),
        trainings_total=12,
        trainings_remaining=7,
        is_active=True,
        created_at=datetime(2026, 5, 1, 9, 0, 0),
    )
    session.add(subscription)
    session.flush()

    training = Training(
        sport_type="MMA",
        age_group="adults",
        training_date=datetime(2026, 5, 11, 18, 0, 0),
        coach_id=1,
        is_cancelled=False,
    )
    session.add(training)
    session.commit()

    with patch(
        "database.db_utils.close_unmarked_attendance.now_moscow",
        return_value=datetime(2026, 5, 12, 12, 0, 0),
    ):
        inserted = close_unmarked_attendance_after_grace(session, lookback_days=10)
        session.commit()

    assert inserted == 1
    attendance = session.query(Attendance).one()
    assert attendance.attended is False
    assert attendance.marked_by is None
    assert attendance.locked_at is not None
    session.refresh(subscription)
    assert subscription.trainings_remaining == 6

    session.close()
