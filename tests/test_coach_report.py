"""Сводка тренера за месяц: агрегаты из coach_report."""
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import joinedload, sessionmaker

from database.db_utils.coach_report import build_coach_period_report
from database.models import (
    Athlete,
    Attendance,
    Base,
    Coach,
    SportType,
    Subscription,
    SubscriptionPayment,
    Training,
)

pytestmark = pytest.mark.db


def _coach_mma_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    st = SportType(name="MMA", display_name="MMA")
    s.add(st)
    s.flush()
    coach = Coach(telegram_id=900202, first_name="C", sport_type_id=st.id)
    s.add(coach)
    s.commit()
    coach = (
        s.query(Coach)
        .options(joinedload(Coach.sport_type_rel))
        .filter_by(id=coach.id)
        .one()
    )
    return s, coach


def test_report_roster_attendance_active_and_subscription_starts():
    s, coach = _coach_mma_session()
    y, m = 2025, 6

    a_old = Athlete(
        full_name="Старый",
        sport_type="MMA",
        age_group="adults",
        created_by=coach.id,
        created_at=datetime(2025, 1, 10, 12, 0, 0),
    )
    s.add(a_old)
    s.flush()
    s.add(
        Subscription(
            athlete_id=a_old.id,
            discipline_key="mma_old",
            sport_type="MMA",
            is_active=True,
            start_date=datetime(2025, 1, 1),
            end_date=datetime(2026, 1, 1),
            created_at=datetime(2025, 1, 2),
        )
    )

    a_new = Athlete(
        full_name="Новый",
        sport_type="MMA",
        age_group="adults",
        created_by=coach.id,
        created_at=datetime(2025, 6, 5, 10, 0, 0),
    )
    s.add(a_new)
    s.flush()
    s.add(
        Subscription(
            athlete_id=a_new.id,
            discipline_key="mma_new",
            sport_type="MMA",
            is_active=True,
            start_date=datetime(2025, 6, 5),
            end_date=datetime(2026, 6, 1),
            created_at=datetime(2025, 6, 5, 11, 0, 0),
        )
    )
    s.flush()

    tr = Training(
        sport_type="MMA",
        age_group="adults",
        training_date=datetime(2025, 6, 10, 18, 0, 0),
        coach_id=coach.id,
        is_cancelled=False,
    )
    s.add(tr)
    s.flush()

    sub_old = (
        s.query(Subscription).filter_by(athlete_id=a_old.id).one()
    )
    sub_new = s.query(Subscription).filter_by(athlete_id=a_new.id).one()
    s.add(
        Attendance(
            athlete_id=a_old.id,
            training_id=tr.id,
            subscription_id=sub_old.id,
            attended=True,
            created_at=datetime(2025, 6, 10, 19, 0, 0),
        )
    )
    s.add(
        Attendance(
            athlete_id=a_new.id,
            training_id=tr.id,
            subscription_id=sub_new.id,
            attended=False,
            created_at=datetime(2025, 6, 10, 19, 0, 0),
        )
    )
    s.commit()

    rep = build_coach_period_report(s, coach, y, m)
    s.close()

    assert rep.roster_total == 2
    assert rep.active_at_month_end == 2
    assert rep.new_athletes_in_period == 1
    assert rep.attendance_present == 1
    assert rep.attendance_absent == 1
    assert rep.athletes_present_distinct == 1
    assert rep.athletes_absent_distinct == 1
    assert rep.subscription_starts_in_period == 1
    assert rep.new_subscription_rows_in_period == 1
    assert rep.revenue_rubles == 0
    assert rep.payment_records_in_period == 0


def test_report_excludes_other_sport_training():
    s, coach = _coach_mma_session()
    y, m = 2025, 7

    a = Athlete(
        full_name="Боец",
        sport_type="MMA",
        age_group="adults",
        created_by=coach.id,
        created_at=datetime(2025, 6, 1),
    )
    s.add(a)
    s.flush()
    s.add(
        Subscription(
            athlete_id=a.id,
            discipline_key="mma_one",
            sport_type="MMA",
            is_active=True,
            start_date=datetime(2025, 6, 1),
            end_date=datetime(2026, 6, 1),
            created_at=datetime(2025, 6, 1),
        )
    )
    s.flush()
    tr_thai = Training(
        sport_type="Тайский Бокс",
        age_group="adults",
        training_date=datetime(2025, 7, 3, 18, 0, 0),
        coach_id=coach.id,
        is_cancelled=False,
    )
    s.add(tr_thai)
    s.flush()
    sub = s.query(Subscription).filter_by(athlete_id=a.id).one()
    s.add(
        Attendance(
            athlete_id=a.id,
            training_id=tr_thai.id,
            subscription_id=sub.id,
            attended=True,
            created_at=datetime(2025, 7, 3, 19, 0, 0),
        )
    )
    s.commit()

    rep = build_coach_period_report(s, coach, y, m)
    s.close()

    assert rep.roster_total == 1
    assert rep.attendance_present == 0
    assert rep.athletes_present_distinct == 0
    assert rep.athletes_absent_distinct == 0
    assert rep.revenue_rubles == 0
    assert rep.payment_records_in_period == 0


def test_report_revenue_by_paid_at():
    s, coach = _coach_mma_session()
    y, m = 2025, 9

    a = Athlete(
        full_name="Платник",
        sport_type="MMA",
        age_group="adults",
        created_by=coach.id,
        created_at=datetime(2025, 8, 1),
    )
    s.add(a)
    s.flush()
    s.add(
        Subscription(
            athlete_id=a.id,
            discipline_key="mma_pay",
            sport_type="MMA",
            is_active=True,
            start_date=datetime(2025, 8, 1),
            end_date=datetime(2026, 9, 1),
            created_at=datetime(2025, 8, 1),
        )
    )
    s.flush()
    sub = s.query(Subscription).filter_by(athlete_id=a.id).one()
    s.add(
        SubscriptionPayment(
            subscription_id=sub.id,
            amount_rubles=5000,
            paid_at=datetime(2025, 9, 15, 12, 0, 0),
            note="абонемент",
        )
    )
    s.add(
        SubscriptionPayment(
            subscription_id=sub.id,
            amount_rubles=1500,
            paid_at=datetime(2025, 10, 1, 0, 0, 0),
        )
    )
    s.commit()

    rep = build_coach_period_report(s, coach, y, m)
    s.close()

    assert rep.revenue_rubles == 5000
    assert rep.payment_records_in_period == 1


def test_report_excludes_cancelled_training_attendance():
    s, coach = _coach_mma_session()
    y, m = 2025, 8

    a = Athlete(
        full_name="Боец",
        sport_type="MMA",
        age_group="adults",
        created_by=coach.id,
        created_at=datetime(2025, 7, 1),
    )
    s.add(a)
    s.flush()
    s.add(
        Subscription(
            athlete_id=a.id,
            discipline_key="mma_c",
            sport_type="MMA",
            is_active=True,
            start_date=datetime(2025, 7, 1),
            end_date=datetime(2026, 8, 1),
            created_at=datetime(2025, 7, 1),
        )
    )
    s.flush()
    tr = Training(
        sport_type="MMA",
        age_group="adults",
        training_date=datetime(2025, 8, 12, 18, 0, 0),
        coach_id=coach.id,
        is_cancelled=True,
    )
    s.add(tr)
    s.flush()
    sub = s.query(Subscription).filter_by(athlete_id=a.id).one()
    s.add(
        Attendance(
            athlete_id=a.id,
            training_id=tr.id,
            subscription_id=sub.id,
            attended=True,
            created_at=datetime(2025, 8, 12, 19, 0, 0),
        )
    )
    s.commit()

    rep = build_coach_period_report(s, coach, y, m)
    s.close()

    assert rep.attendance_present == 0
    assert rep.athletes_present_distinct == 0
    assert rep.revenue_rubles == 0
    assert rep.payment_records_in_period == 0
