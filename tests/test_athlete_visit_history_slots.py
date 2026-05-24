"""История посещений спортсмена — те же слоты, что «Отметить посещения»."""
import pytest
from datetime import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, joinedload

from database.models import Athlete, Base, Coach, SportType, Subscription
from services.attendance_training_flow import collect_athlete_visit_history_slots
from utils.time_utils import APP_TZ

pytestmark = pytest.mark.db


def _coach_mma(session):
    st = SportType(name="MMA", display_name="MMA")
    session.add(st)
    session.flush()
    coach = Coach(telegram_id=900201, first_name="C", sport_type_id=st.id)
    session.add(coach)
    session.commit()
    return (
        session.query(Coach)
        .options(joinedload(Coach.sport_type_rel))
        .filter_by(id=coach.id)
        .one()
    )


def test_visit_history_includes_scheduled_group_days_without_training_row(monkeypatch):
    """Групповые дни по расписанию попадают в историю, даже если trainings ещё нет."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    coach = _coach_mma(s)

    athlete = Athlete(
        full_name="Тестов Тест",
        sport_type="MMA",
        age_group="adults",
        created_by=coach.id,
    )
    s.add(athlete)
    s.flush()
    s.add(
        Subscription(
            athlete_id=athlete.id,
            discipline_key="mma_group",
            sport_type="MMA",
            subscription_type="monthly",
            is_active=True,
            start_date=datetime(2026, 4, 1, 0, 0, 0),
            end_date=datetime(2026, 4, 30, 23, 59, 0),
        )
    )
    s.commit()

    fixed_now = datetime(2026, 5, 24, 12, 0, 0)
    monkeypatch.setattr(
        "services.attendance_training_flow.now_moscow",
        lambda: fixed_now,
    )

    rows = collect_athlete_visit_history_slots(
        s,
        athlete,
        coach,
        range_start=datetime(2026, 4, 1, 0, 0, 0),
        range_end=datetime(2026, 4, 30, 23, 59, 0),
    )
    april = [r for r in rows if r[0].training_date.month == 4]
    # MMA adults: Пн/Ср/Пт в апреле 2026 — 13 слотов
    assert len(april) == 13
    assert all(r[0].training_date.hour == 20 for r in april)
