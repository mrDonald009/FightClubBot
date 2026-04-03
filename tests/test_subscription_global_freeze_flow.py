"""Тесты домена абонементов: массовая заморозка, деактивация, пересчёт и аудит."""
from datetime import datetime
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db_utils import (
    apply_global_freeze,
    calculate_actual_trainings_remaining,
    deactivate_global_freeze_and_migrate,
    list_active_global_freezes_overlapping_range,
    migrate_existing_subscription,
)
from database.models import (
    Athlete,
    Attendance,
    Base,
    Coach,
    GlobalFreeze,
    GlobalFreezeApplication,
    SportType,
    Subscription,
    Training,
)
from services.subscription_audit_service import run_subscription_audit


def _session_with_active_global_freeze():
    """Базовая in-memory БД: sport/coach/athlete + 1 активная глобальная заморозка."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    s = SessionLocal()
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
def session_with_monthly_subscription():
    s, a, _ = _session_with_active_global_freeze()
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
def session_migrate_start_inside_global_freeze():
    """Старт абонемента в первый день GF — для проверки чистки авто-списаний."""
    s, a, c = _session_with_active_global_freeze()
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


# --- Блок 1: корректность остатка и миграции monthly -----------------------------------------


def test_remaining_ignores_auto_usage_during_active_global_freeze(session_with_monthly_subscription):
    s = session_with_monthly_subscription
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
    with patch("database.db_utils.now_moscow", return_value=datetime(2026, 3, 25, 20, 0, 0)):
        rem = calculate_actual_trainings_remaining(s, sub)
    assert rem == 12


def test_remaining_counts_usage_outside_global_freeze(session_with_monthly_subscription):
    s = session_with_monthly_subscription
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
    with patch("database.db_utils.now_moscow", return_value=datetime(2026, 3, 22, 20, 0, 0)):
        rem = calculate_actual_trainings_remaining(s, sub)
    assert rem == 11


def test_monthly_migration_removes_auto_attendance_inside_freeze_window(
    session_migrate_start_inside_global_freeze,
):
    s, sub, a, c = session_migrate_start_inside_global_freeze
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

    with patch("database.db_utils.now_moscow", return_value=datetime(2026, 3, 25, 10, 0, 0)):
        migrate_existing_subscription(s, sub.id)

    assert s.query(Attendance).filter_by(subscription_id=sub.id).count() == 0


# --- Блок 2: создание/пересечения глобальных заморозок ---------------------------------------


def test_overlapping_range_helper_matches_apply_overlap_logic():
    s, _, _ = _session_with_active_global_freeze()
    gf = s.query(GlobalFreeze).one()
    ov = list_active_global_freezes_overlapping_range(s, datetime(2026, 3, 25), datetime(2026, 4, 1))
    assert len(ov) == 1
    assert ov[0].id == gf.id
    s.close()


def test_apply_global_freeze_rejects_overlapping_ranges():
    s, _, _ = _session_with_active_global_freeze()
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
    s, _, _ = _session_with_active_global_freeze()
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


def test_subscription_audit_reports_overlapping_global_freezes():
    s, _, _ = _session_with_active_global_freeze()
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


# --- Блок 3: деактивация GF и цикл re-apply ---------------------------------------------------


def test_deactivate_global_freeze_is_idempotent_and_sets_inactive():
    s, _, _ = _session_with_active_global_freeze()
    gf = s.query(GlobalFreeze).one()
    r1 = deactivate_global_freeze_and_migrate(s, gf.id)
    assert r1["success"] is True
    assert r1.get("already_inactive") is False
    assert r1["migrated"] == 0
    assert r1["checked"] == 0
    assert r1["synced"] == 0
    s.refresh(gf)
    assert gf.is_active is False

    r2 = deactivate_global_freeze_and_migrate(s, gf.id)
    assert r2["success"] is True
    assert r2.get("already_inactive") is True
    assert r2["checked"] == 0
    assert r2["synced"] == 0
    s.close()


def _session_with_monthly_sub_and_without_any_gf():
    """Чистая БД: спортсмен + активный monthly, без записей global_freezes."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    s = SessionLocal()
    st = SportType(name="Тайский Бокс", display_name="Тайский Бокс")
    s.add(st)
    s.flush()
    c = Coach(telegram_id=900002, username="t2", first_name="T2", sport_type_id=st.id)
    s.add(c)
    s.flush()
    a = Athlete(
        full_name="GF Cycle Tester",
        sport_type="Тайский Бокс",
        age_group="children",
        created_by=c.id,
    )
    s.add(a)
    s.flush()
    sub = Subscription(
        athlete_id=a.id,
        sport_type="Тайский Бокс",
        subscription_type="monthly",
        start_date=datetime(2026, 3, 21, 12, 30),
        end_date=datetime(2026, 4, 30, 19, 30),
        trainings_total=12,
        trainings_remaining=10,
        is_active=True,
    )
    s.add(sub)
    s.commit()
    return s, sub


def test_global_freeze_cycle_deactivate_then_reapply_recalculates_monthly_consistently():
    """
    Цикл: apply GF -> deactivate GF (migration monthly) -> apply GF again same dates.
    Проверяем согласованность продления и отсутствие overlap среди активных GF.
    """
    s, sub = _session_with_monthly_sub_and_without_any_gf()
    freeze_start = datetime(2026, 3, 24)
    freeze_end = datetime(2026, 3, 30)
    title = "cycle_test_gf"

    r_apply1 = apply_global_freeze(
        session=s,
        start_date=freeze_start,
        end_date=freeze_end,
        title=title,
        created_by=1,
    )
    assert r_apply1["success"] is True
    assert r_apply1["updated_subscriptions"] >= 1
    gf_id_1 = r_apply1["global_freeze_id"]
    s.refresh(sub)
    end_after_apply1 = sub.end_date

    apps = (
        s.query(GlobalFreezeApplication)
        .filter(GlobalFreezeApplication.global_freeze_id == gf_id_1)
        .all()
    )
    assert len(apps) >= 1
    assert any(a.training_days_added > 0 for a in apps)

    with patch("database.db_utils.now_moscow", return_value=datetime(2026, 3, 25, 10, 0, 0)):
        r_deact = deactivate_global_freeze_and_migrate(s, gf_id_1)

    assert r_deact["success"] is True
    assert r_deact.get("already_inactive") is False
    assert r_deact["migrated"] >= 1
    assert r_deact["checked"] >= 1
    assert s.query(GlobalFreeze).filter_by(id=gf_id_1).one().is_active is False

    s.refresh(sub)
    end_after_deactivate = sub.end_date
    assert end_after_deactivate <= end_after_apply1

    r_apply2 = apply_global_freeze(
        session=s,
        start_date=freeze_start,
        end_date=freeze_end,
        title=title + "_again",
        created_by=1,
    )
    assert r_apply2["success"] is True
    assert r_apply2["global_freeze_id"] != gf_id_1

    s.refresh(sub)
    end_after_apply2 = sub.end_date
    assert end_after_apply2 >= end_after_deactivate

    active_count = s.query(GlobalFreeze).filter(GlobalFreeze.is_active == True).count()
    assert active_count == 1

    report = run_subscription_audit(s)
    codes = {issue["code"] for issue in report["issues"]}
    assert "overlapping_global_freezes" not in codes
    s.close()


def test_deactivate_global_freeze_checks_non_monthly_subscriptions_too():
    """После деактивации выполняется контрольная синхронизация для всех затронутых типов."""
    s, _ = _session_with_monthly_sub_and_without_any_gf()
    coach_id = s.query(Coach).one().id
    athlete = Athlete(
        full_name="Single Sub Athlete",
        sport_type="Тайский Бокс",
        age_group="children",
        created_by=coach_id,
    )
    s.add(athlete)
    s.flush()
    single = Subscription(
        athlete_id=athlete.id,
        sport_type="Тайский Бокс",
        subscription_type="single",
        start_date=datetime(2026, 3, 24, 18, 0),
        end_date=datetime(2026, 3, 24, 19, 30),
        trainings_total=1,
        trainings_remaining=1,
        is_active=True,
    )
    s.add(single)
    s.commit()

    r_apply = apply_global_freeze(
        session=s,
        start_date=datetime(2026, 3, 24),
        end_date=datetime(2026, 3, 30),
        title="mix_types",
        created_by=1,
    )
    assert r_apply["success"] is True
    r_deact = deactivate_global_freeze_and_migrate(s, r_apply["global_freeze_id"])
    assert r_deact["success"] is True
    # Затронуты monthly + single: проверка должна учитывать оба.
    assert r_deact["checked"] >= 2
    s.close()
