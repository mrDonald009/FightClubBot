"""Тесты единого upsert пути для visit_history."""
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db_utils.visit_history import upsert_visit_history_for_training
from database.models import Athlete, Attendance, Base, Coach, SportType, Subscription, Training


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    return SessionLocal(), engine


def _seed_entities(session):
    st = SportType(name="MMA", display_name="MMA")
    session.add(st)
    session.flush()

    coach = Coach(telegram_id=901001, first_name="C", sport_type_id=st.id, sport_type="MMA")
    session.add(coach)
    session.flush()

    athlete = Athlete(
        full_name="Тест Спортсмен",
        sport_type="MMA",
        age_group="adults",
        created_by=coach.id,
    )
    session.add(athlete)
    session.flush()

    sub = Subscription(
        athlete_id=athlete.id,
        discipline_key="mma_group",
        responsible_coach_id=coach.id,
        sport_type_id=st.id,
        sport_type="MMA",
        subscription_type="single",
        start_date=datetime(2026, 5, 1, 19, 0, 0),
        end_date=datetime(2026, 5, 1, 21, 0, 0),
        trainings_total=1,
        trainings_remaining=1,
        is_active=True,
        created_at=datetime(2026, 5, 1, 10, 0, 0),
    )
    session.add(sub)
    session.flush()

    tr = Training(
        sport_type="MMA",
        age_group="adults",
        training_date=datetime(2026, 5, 1, 19, 0, 0),
        is_cancelled=False,
        coach_id=coach.id,
    )
    session.add(tr)
    session.flush()
    return athlete, sub, tr


def test_upsert_visit_history_creates_row_from_attendance():
    session, engine = _session()
    athlete, sub, tr = _seed_entities(session)
    att = Attendance(
        athlete_id=athlete.id,
        training_id=tr.id,
        subscription_id=sub.id,
        attended=True,
        created_at=datetime(2026, 5, 1, 20, 0, 0),
    )
    session.add(att)
    session.flush()

    row, changed = upsert_visit_history_for_training(
        session,
        athlete_id=athlete.id,
        training_id=tr.id,
        attendance=att,
        source="attendance_mark",
    )
    session.flush()

    assert changed is True
    assert row.status_code == "present"
    assert row.attendance_id == att.id
    assert row.subscription_id == sub.id
    session.close()
    engine.dispose()


def test_upsert_visit_history_updates_existing_row():
    session, engine = _session()
    athlete, sub, tr = _seed_entities(session)
    att = Attendance(
        athlete_id=athlete.id,
        training_id=tr.id,
        subscription_id=sub.id,
        attended=False,
        created_at=datetime(2026, 5, 1, 20, 0, 0),
    )
    session.add(att)
    session.flush()

    upsert_visit_history_for_training(
        session,
        athlete_id=athlete.id,
        training_id=tr.id,
        attendance=att,
        status_code="absent",
        status_label="❌ Не был",
        source="derived",
    )
    session.flush()

    att.attended = True
    row, changed = upsert_visit_history_for_training(
        session,
        athlete_id=athlete.id,
        training_id=tr.id,
        attendance=att,
        status_code="present",
        status_label="✅ Был",
        source="attendance_mark",
    )
    session.flush()

    assert changed is True
    assert row.status_code == "present"
    assert row.status_label == "✅ Был"
    assert row.source == "attendance_mark"
    session.close()
    engine.dispose()
