"""Регрессия: списания не должны жить внутри активной массовой заморозки (кроме ручных отметок)."""
from datetime import datetime
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db_utils import (
    apply_global_freeze,
    calculate_actual_trainings_remaining,
    deactivate_global_freeze_and_migrate,
    migrate_existing_subscription,
    update_global_freeze_title,
)
from database.models import (
    Athlete,
    Attendance,
    Base,
    Coach,
    GlobalFreeze,
    SportType,
    Subscription,
    Training,
)
from services.subscription_audit_service import run_subscription_audit


def _base_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine)
    s = S()
    st = SportType(name="Тайский Бокс", display_name="Тайский Бокс")
    s.add(st)
    s.flush()
    c = Coach(telegram_id=900001, username="t", first_name="T", sport_type_id=st.id)
    s.add(c)
    s.flush()
    a = Athlete(
        full_name="Tester",
        sport_type="Тайский Бокс",
        age_group="children",
        created_by=c.id,
    )
    s.add(a)
    s.flush()
    gf = GlobalFreeze(
        title="test_gf",
        start_date=datetime(2026, 3, 24),
        end_date=datetime(2026, 4, 5, 23, 59, 59),
        is_active=True,
    )
    s.add(gf)
    s.commit()
    return s, a, c


@pytest.fixture
def memory_session():
    s, a, c = _base_session()
    sub = Subscription(
        athlete_id=a.id,
        sport_type="Тайский Бокс",
        subscription_type="monthly",
        start_date=datetime(2026, 3, 21, 12, 30),
        end_date=datetime(2026, 4, 30, 19, 30),
        trainings_total=12,
        trainings_remaining=10,
        is_active=True,
        frozen_training_days_total=1,
    )
    s.add(sub)
    s.commit()
    yield s
    s.close()


@pytest.fixture
def session_migrate_start_in_freeze():
    """Старт в первый день заморозки — в миграции обрабатывается один тренировочный слот."""
    s, a, c = _base_session()
    sub = Subscription(
        athlete_id=a.id,
        sport_type="Тайский Бокс",
        subscription_type="monthly",
        start_date=datetime(2026, 3, 24, 18, 0),
        end_date=datetime(2026, 5, 15, 19, 30),
        trainings_total=12,
        trainings_remaining=11,
        is_active=True,
        frozen_training_days_total=1,
    )
    s.add(sub)
    s.commit()
    yield s, sub, a, c
    s.close()


def test_calculate_remaining_ignores_auto_usage_during_global_freeze(memory_session):
    s = memory_session
    sub = s.query(Subscription).one()
    tr = Training(
        sport_type="Тайский Бокс",
        age_group="children",
        training_date=datetime(2026, 3, 24, 18, 0, 0),
        is_cancelled=False,
        coach_id=None,
    )
    s.add(tr)
    s.flush()
    a = s.query(Athlete).one()
    s.add(
        Attendance(
            athlete_id=a.id,
            training_id=tr.id,
            subscription_id=sub.id,
            attended=False,
            marked_by=None,
        )
    )
    s.commit()
    fake_now = datetime(2026, 3, 25, 20, 0, 0)
    with patch("database.db_utils.now_moscow", return_value=fake_now):
        rem = calculate_actual_trainings_remaining(s, sub)
    assert rem == 12


def test_calculate_remaining_counts_usage_outside_global_freeze(memory_session):
    s = memory_session
    sub = s.query(Subscription).one()
    tr = Training(
        sport_type="Тайский Бокс",
        age_group="children",
        training_date=datetime(2026, 3, 21, 12, 30, 0),
        is_cancelled=False,
        coach_id=None,
    )
    s.add(tr)
    s.flush()
    a = s.query(Athlete).one()
    s.add(
        Attendance(
            athlete_id=a.id,
            training_id=tr.id,
            subscription_id=sub.id,
            attended=False,
            marked_by=None,
        )
    )
    s.commit()
    fake_now = datetime(2026, 3, 22, 20, 0, 0)
    with patch("database.db_utils.now_moscow", return_value=fake_now):
        rem = calculate_actual_trainings_remaining(s, sub)
    assert rem == 11


def test_migrate_deletes_spurious_auto_attendance_in_freeze_window(session_migrate_start_in_freeze):
    s, sub, a, c = session_migrate_start_in_freeze
    tr = Training(
        sport_type="Тайский Бокс",
        age_group="children",
        training_date=datetime(2026, 3, 24, 18, 0, 0),
        is_cancelled=False,
        coach_id=c.id,
    )
    s.add(tr)
    s.flush()
    s.add(
        Attendance(
            athlete_id=a.id,
            training_id=tr.id,
            subscription_id=sub.id,
            attended=False,
            marked_by=None,
        )
    )
    s.commit()
    assert s.query(Attendance).filter_by(subscription_id=sub.id).count() == 1

    fake_now = datetime(2026, 3, 25, 10, 0, 0)
    with patch("database.db_utils.now_moscow", return_value=fake_now):
        migrate_existing_subscription(s, sub.id)

    assert s.query(Attendance).filter_by(subscription_id=sub.id).count() == 0


def test_apply_global_freeze_rejects_overlapping_ranges():
    s, _, _ = _base_session()
    result = apply_global_freeze(
        session=s,
        start_date=datetime(2026, 3, 25),
        end_date=datetime(2026, 4, 6),
        title="overlap",
        created_by=1,
    )
    assert result["success"] is False
    assert "пересека" in result["message"].lower()
    assert s.query(GlobalFreeze).count() == 1
    s.close()


def test_apply_global_freeze_allows_non_overlapping_ranges():
    s, _, _ = _base_session()
    result = apply_global_freeze(
        session=s,
        start_date=datetime(2026, 4, 6),
        end_date=datetime(2026, 4, 10),
        title="non-overlap",
        created_by=1,
    )
    assert result["success"] is True
    assert s.query(GlobalFreeze).count() == 2
    s.close()


def test_audit_reports_overlapping_global_freezes():
    s, _, _ = _base_session()
    s.add(
        GlobalFreeze(
            title="second_overlap",
            start_date=datetime(2026, 3, 25),
            end_date=datetime(2026, 4, 6, 23, 59, 59),
            is_active=True,
        )
    )
    s.commit()
    report = run_subscription_audit(s)
    codes = {issue["code"] for issue in report["issues"]}
    assert "overlapping_global_freezes" in codes
    s.close()


def test_deactivate_global_freeze_sets_inactive_and_idempotent():
    s, _, _ = _base_session()
    gf = s.query(GlobalFreeze).one()
    r1 = deactivate_global_freeze_and_migrate(s, gf.id)
    assert r1["success"] is True
    assert r1.get("already_inactive") is False
    assert r1["migrated"] == 0
    s.refresh(gf)
    assert gf.is_active is False

    r2 = deactivate_global_freeze_and_migrate(s, gf.id)
    assert r2["success"] is True
    assert r2.get("already_inactive") is True
    s.close()


def test_update_global_freeze_title_active_only():
    s, _, _ = _base_session()
    gf = s.query(GlobalFreeze).one()
    r = update_global_freeze_title(s, gf.id, "Новое имя")
    assert r["success"] is True
    assert r["title"] == "Новое имя"
    s.refresh(gf)
    assert gf.title == "Новое имя"
    s.close()


def test_update_global_freeze_title_rejects_inactive():
    s, _, _ = _base_session()
    gf = s.query(GlobalFreeze).one()
    gf.is_active = False
    s.commit()
    r = update_global_freeze_title(s, gf.id, "X")
    assert r["success"] is False
    s.close()
