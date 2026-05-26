from datetime import datetime
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db_utils.close_unmarked_attendance import close_unmarked_attendance_after_grace
from database.models import Athlete, Attendance, Base, Subscription, Training


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
        end_date=datetime(2026, 5, 31, 23, 59, 59),
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
        return_value=datetime(2026, 5, 11, 22, 0, 0),
    ):
        inserted = close_unmarked_attendance_after_grace(session, lookback_days=10)

    assert inserted == 1
    attendance_rows = session.query(Attendance).all()
    assert len(attendance_rows) == 1
    assert attendance_rows[0].athlete_id == athlete.id
    assert attendance_rows[0].training_id == training.id
    assert attendance_rows[0].subscription_id == sub_group.id

    session.close()
