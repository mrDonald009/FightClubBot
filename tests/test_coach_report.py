"""Статистика тренера за месяц: агрегаты из coach_report."""
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
    SubscriptionTariff,
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
    assert rep.payment_count_monthly == 0
    assert rep.payment_count_single == 0
    assert rep.payment_count_individual == 0
    assert rep.estimated_revenue_if_current_env_rub == 0


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
    assert rep.payment_count_monthly == 0
    assert rep.payment_count_single == 0
    assert rep.payment_count_individual == 0
    assert rep.estimated_revenue_if_current_env_rub == 0


def test_report_estimated_revenue_from_starts_when_tariff_in_db():
    s, coach = _coach_mma_session()
    s.add(
        SubscriptionTariff(
            sport_type_name="MMA",
            tariff_kind="subscription_monthly",
            amount_rubles=12000,
            is_active=True,
        )
    )
    s.commit()
    y, m = 2026, 1

    a = Athlete(
        full_name="Стартянварь",
        sport_type="MMA",
        age_group="adults",
        created_by=coach.id,
        created_at=datetime(2025, 12, 1),
    )
    s.add(a)
    s.flush()
    s.add(
        Subscription(
            athlete_id=a.id,
            discipline_key="mma_jan",
            sport_type="MMA",
            subscription_type="monthly",
            is_active=True,
            start_date=datetime(2026, 1, 10, 19, 0, 0),
            end_date=datetime(2027, 1, 10, 19, 0, 0),
            created_at=datetime(2025, 12, 15),
        )
    )
    s.commit()

    rep = build_coach_period_report(s, coach, y, m)
    s.close()

    assert rep.subscription_starts_in_period == 1
    assert rep.revenue_rubles == 0
    assert rep.payment_records_in_period == 0
    assert rep.payment_count_monthly == 0
    assert rep.payment_count_single == 0
    assert rep.payment_count_individual == 0
    assert rep.estimated_revenue_if_current_env_rub == 12000


def test_report_estimated_uses_db_tariff():
    s, coach = _coach_mma_session()
    s.add(
        SubscriptionTariff(
            sport_type_name="MMA",
            tariff_kind="subscription_monthly",
            amount_rubles=8800,
            is_active=True,
        )
    )
    y, m = 2026, 2

    a = Athlete(
        full_name="ТолькоБД",
        sport_type="MMA",
        age_group="adults",
        created_by=coach.id,
        created_at=datetime(2026, 1, 1),
    )
    s.add(a)
    s.flush()
    s.add(
        Subscription(
            athlete_id=a.id,
            discipline_key="mma_feb",
            sport_type="MMA",
            subscription_type="monthly",
            is_active=True,
            start_date=datetime(2026, 2, 5, 19, 0, 0),
            end_date=datetime(2027, 2, 5, 19, 0, 0),
            created_at=datetime(2026, 1, 20),
        )
    )
    s.commit()

    rep = build_coach_period_report(s, coach, y, m)
    s.close()

    assert rep.subscription_starts_in_period == 1
    assert rep.payment_records_in_period == 0
    assert rep.payment_count_monthly == 0
    assert rep.payment_count_single == 0
    assert rep.payment_count_individual == 0
    assert rep.estimated_revenue_if_current_env_rub == 8800


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
            subscription_type="monthly",
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
    assert rep.payment_count_monthly == 1
    assert rep.payment_count_single == 0
    assert rep.payment_count_individual == 0
    assert rep.estimated_revenue_if_current_env_rub == 0


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
    assert rep.payment_count_monthly == 0
    assert rep.payment_count_single == 0
    assert rep.payment_count_individual == 0
    assert rep.estimated_revenue_if_current_env_rub == 0


def test_report_deduplicates_payments_per_subscription_in_period():
    """Активация = одна оплата: дубли строк оплаты одного абонемента не раздувают отчёт."""
    s, coach = _coach_mma_session()
    y, m = 2026, 5

    a = Athlete(
        full_name="Индивидуал",
        sport_type="MMA",
        age_group="adults",
        created_by=coach.id,
        created_at=datetime(2026, 4, 1),
    )
    s.add(a)
    s.flush()
    sub = Subscription(
        athlete_id=a.id,
        discipline_key="mma_ind_may",
        sport_type="MMA",
        subscription_type="individual",
        is_active=True,
        start_date=datetime(2026, 5, 11, 14, 0, 0),
        end_date=datetime(2026, 5, 11, 15, 30, 0),
        created_at=datetime(2026, 5, 11, 14, 0, 0),
    )
    s.add(sub)
    s.flush()

    # Исторический дубль одной и той же оплаты в пределах месяца.
    s.add(
        SubscriptionPayment(
            subscription_id=sub.id,
            amount_rubles=3000,
            paid_at=datetime(2026, 5, 11, 14, 0, 0),
            payment_kind="individual_training",
        )
    )
    s.add(
        SubscriptionPayment(
            subscription_id=sub.id,
            amount_rubles=3000,
            paid_at=datetime(2026, 5, 11, 14, 0, 1),
            payment_kind="individual_training",
        )
    )
    s.commit()

    rep = build_coach_period_report(s, coach, y, m)
    s.close()

    assert rep.revenue_rubles == 3000
    assert rep.payment_records_in_period == 1
    assert rep.payment_count_monthly == 0
    assert rep.payment_count_single == 0
    assert rep.payment_count_individual == 1


def test_report_all_fields_snapshot_values():
    """Полный снимок: проверяем все поля отчёта в одном контролируемом сценарии."""
    s, coach = _coach_mma_session()
    y, m = 2026, 5

    s.add_all(
        [
            SubscriptionTariff(
                sport_type_name="MMA",
                tariff_kind="subscription_monthly",
                amount_rubles=6500,
                is_active=True,
            ),
            SubscriptionTariff(
                sport_type_name="MMA",
                tariff_kind="subscription_single",
                amount_rubles=550,
                is_active=True,
            ),
            SubscriptionTariff(
                sport_type_name="MMA",
                tariff_kind="individual_training",
                amount_rubles=3000,
                is_active=True,
            ),
        ]
    )

    a1 = Athlete(
        full_name="Старший Спортсмен",
        sport_type="MMA",
        age_group="adults",
        created_by=coach.id,
        created_at=datetime(2026, 4, 20, 10, 0, 0),
    )
    a2 = Athlete(
        full_name="Новый Спортсмен",
        sport_type="MMA",
        age_group="adults",
        created_by=coach.id,
        created_at=datetime(2026, 5, 5, 10, 0, 0),
    )
    s.add_all([a1, a2])
    s.flush()

    sub_monthly = Subscription(
        athlete_id=a1.id,
        discipline_key="mma_monthly_main",
        sport_type="MMA",
        subscription_type="monthly",
        is_active=True,
        start_date=datetime(2026, 4, 1, 0, 0, 0),
        end_date=datetime(2026, 12, 31, 23, 59, 59),
        created_at=datetime(2026, 4, 1, 10, 0, 0),
    )
    sub_individual = Subscription(
        athlete_id=a2.id,
        discipline_key="mma_individual_may",
        sport_type="MMA",
        subscription_type="individual",
        is_active=True,
        start_date=datetime(2026, 5, 11, 14, 0, 0),
        end_date=datetime(2026, 5, 11, 15, 30, 0),
        created_at=datetime(2026, 5, 5, 11, 0, 0),
    )
    sub_single = Subscription(
        athlete_id=a1.id,
        discipline_key="mma_single_may",
        sport_type="MMA",
        subscription_type="single",
        is_active=True,
        start_date=datetime(2026, 5, 20, 20, 0, 0),
        end_date=datetime(2026, 5, 20, 21, 30, 0),
        created_at=datetime(2026, 5, 20, 10, 0, 0),
    )
    s.add_all([sub_monthly, sub_individual, sub_single])
    s.flush()

    tr1 = Training(
        sport_type="MMA",
        age_group="adults",
        training_date=datetime(2026, 5, 10, 18, 0, 0),
        coach_id=coach.id,
        is_cancelled=False,
    )
    tr2 = Training(
        sport_type="MMA",
        age_group="adults",
        training_date=datetime(2026, 5, 12, 18, 0, 0),
        coach_id=coach.id,
        is_cancelled=False,
    )
    tr3 = Training(
        sport_type="MMA",
        age_group="adults",
        training_date=datetime(2026, 5, 20, 20, 0, 0),
        coach_id=coach.id,
        is_cancelled=False,
    )
    s.add_all([tr1, tr2, tr3])
    s.flush()

    s.add_all(
        [
            Attendance(
                athlete_id=a1.id,
                training_id=tr1.id,
                subscription_id=sub_monthly.id,
                attended=True,
                created_at=datetime(2026, 5, 10, 19, 0, 0),
            ),
            Attendance(
                athlete_id=a1.id,
                training_id=tr2.id,
                subscription_id=sub_monthly.id,
                attended=False,
                created_at=datetime(2026, 5, 12, 19, 0, 0),
            ),
            Attendance(
                athlete_id=a2.id,
                training_id=tr2.id,
                subscription_id=sub_individual.id,
                attended=True,
                created_at=datetime(2026, 5, 12, 19, 10, 0),
            ),
            Attendance(
                athlete_id=a1.id,
                training_id=tr3.id,
                subscription_id=sub_single.id,
                attended=True,
                created_at=datetime(2026, 5, 20, 21, 0, 0),
            ),
        ]
    )

    s.add_all(
        [
            SubscriptionPayment(
                subscription_id=sub_monthly.id,
                amount_rubles=6500,
                paid_at=datetime(2026, 5, 3, 12, 0, 0),
                payment_kind="subscription_monthly",
            ),
            SubscriptionPayment(
                subscription_id=sub_individual.id,
                amount_rubles=3000,
                paid_at=datetime(2026, 5, 11, 14, 0, 0),
                payment_kind="individual_training",
            ),
            SubscriptionPayment(
                subscription_id=sub_single.id,
                amount_rubles=550,
                paid_at=datetime(2026, 5, 20, 20, 0, 0),
                payment_kind="subscription_single",
            ),
        ]
    )
    s.commit()

    rep = build_coach_period_report(s, coach, y, m)
    s.close()

    assert rep.year == 2026
    assert rep.month == 5
    assert rep.roster_total == 2
    assert rep.active_at_month_end == 1
    assert rep.new_athletes_in_period == 1
    assert rep.attendance_present == 3
    assert rep.attendance_absent == 1
    assert rep.athletes_present_distinct == 2
    assert rep.athletes_absent_distinct == 1
    assert rep.subscription_starts_in_period == 2
    assert rep.new_subscription_rows_in_period == 2
    assert rep.revenue_rubles == 10050
    assert rep.payment_records_in_period == 3
    assert rep.payment_count_monthly == 1
    assert rep.payment_count_single == 1
    assert rep.payment_count_individual == 1
    assert rep.estimated_revenue_if_current_env_rub == 3550
