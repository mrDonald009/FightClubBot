"""Статистика тренера за календарный месяц (KPI, выручка, посещаемость, абонементы).

Выручка за период — сумма `subscription_payments.amount_rubles` по дате `paid_at`
для абонементов спортсменов из базы тренера (тот же фильтр по виду спорта).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import List, Optional, Tuple

from sqlalchemy import and_, exists, func, or_
from sqlalchemy.orm import Session as OrmSession

from database.db_utils.subscription_activation_payment import (
    resolve_subscription_activation_price_rubles,
)
from database.models import (
    Athlete,
    Attendance,
    Coach,
    Subscription,
    SubscriptionPayment,
    Training,
)


def coach_sport_type_name(coach: Coach) -> Optional[str]:
    if getattr(coach, "sport_type_rel", None) is not None:
        return coach.sport_type_rel.name
    if coach.sport_type:
        return coach.sport_type
    return None


def coach_roster_athlete_ids(session: OrmSession, coach: Coach) -> List[int]:
    """Те же границы списка спортсменов тренера, что в load_athletes_for_list."""
    q = session.query(Athlete.id).filter_by(created_by=coach.id)
    sport_type_name = coach_sport_type_name(coach)
    if sport_type_name:
        has_sub_for_sport = exists().where(
            Subscription.athlete_id == Athlete.id,
            or_(
                Subscription.sport_type == sport_type_name,
                and_(
                    or_(
                        Subscription.sport_type.is_(None),
                        Subscription.sport_type == "",
                    ),
                    Athlete.sport_type == sport_type_name,
                ),
            ),
        )
        q = q.filter(or_(Athlete.sport_type == sport_type_name, has_sub_for_sport))
    rows = q.all()
    return [r[0] for r in rows]


def month_range(year: int, month: int) -> Tuple[datetime, datetime]:
    """Полуинтервал [start, end) для календарного месяца (naive datetime)."""
    start = datetime(year, month, 1, 0, 0, 0, 0)
    if month == 12:
        end = datetime(year + 1, 1, 1, 0, 0, 0, 0)
    else:
        end = datetime(year, month + 1, 1, 0, 0, 0, 0)
    return start, end


def last_instant_of_month(year: int, month: int) -> datetime:
    """Последний момент последнего дня месяца (для проверки «активен на конец периода»)."""
    _, end_excl = month_range(year, month)
    last_day = end_excl - timedelta(days=1)
    return datetime.combine(last_day.date(), time(23, 59, 59, 999999))


@dataclass
class CoachPeriodReport:
    year: int
    month: int
    roster_total: int
    active_at_month_end: int
    new_athletes_in_period: int
    attendance_present: int
    attendance_absent: int
    athletes_present_distinct: int
    athletes_absent_distinct: int
    subscription_starts_in_period: int
    new_subscription_rows_in_period: int
    revenue_rubles: int
    payment_records_in_period: int
    # Справочно: сумма «как если бы» по текущим subscription_tariffs для каждого старта в периоде.
    estimated_revenue_if_current_env_rub: int


def build_coach_period_report(
    session: OrmSession,
    coach: Coach,
    year: int,
    month: int,
) -> CoachPeriodReport:
    period_start, period_end_excl = month_range(year, month)
    as_of = last_instant_of_month(year, month)
    athlete_ids = coach_roster_athlete_ids(session, coach)
    roster_total = len(athlete_ids)

    coach_sport = coach_sport_type_name(coach)

    active_at_month_end = _count_active_athletes_at(
        session, athlete_ids, coach_sport, as_of
    )

    new_athletes_in_period = _count_new_athletes_in_period(
        session, coach, coach_sport, period_start, period_end_excl
    )

    present, absent = _count_attendance_pair(
        session, athlete_ids, coach_sport, period_start, period_end_excl
    )
    u_pres, u_abs = _count_distinct_athletes_attendance_pair(
        session, athlete_ids, coach_sport, period_start, period_end_excl
    )

    sub_starts = _count_subscription_starts(
        session, athlete_ids, coach_sport, period_start, period_end_excl
    )
    sub_rows = _count_new_subscription_rows(
        session, athlete_ids, coach_sport, period_start, period_end_excl
    )
    rev, pay_n = _revenue_in_period(
        session, athlete_ids, coach_sport, period_start, period_end_excl
    )
    est_rev = _estimated_revenue_from_subscription_starts(
        session, athlete_ids, coach_sport, period_start, period_end_excl
    )

    return CoachPeriodReport(
        year=year,
        month=month,
        roster_total=roster_total,
        active_at_month_end=active_at_month_end,
        new_athletes_in_period=new_athletes_in_period,
        attendance_present=present,
        attendance_absent=absent,
        athletes_present_distinct=u_pres,
        athletes_absent_distinct=u_abs,
        subscription_starts_in_period=sub_starts,
        new_subscription_rows_in_period=sub_rows,
        revenue_rubles=rev,
        payment_records_in_period=pay_n,
        estimated_revenue_if_current_env_rub=est_rev,
    )


def _subscription_sport_match_sql(coach_sport: Optional[str]):
    """Условие соответствия абонемента виду спорта тренера (как в списке спортсменов)."""
    if not coach_sport:
        return True
    return or_(
        Subscription.sport_type == coach_sport,
        and_(
            or_(Subscription.sport_type.is_(None), Subscription.sport_type == ""),
            Athlete.sport_type == coach_sport,
        ),
    )


def _count_active_athletes_at(
    session: OrmSession,
    athlete_ids: List[int],
    coach_sport: Optional[str],
    as_of: datetime,
) -> int:
    if not athlete_ids:
        return 0
    q = (
        session.query(func.count(func.distinct(Subscription.athlete_id)))
        .join(Athlete, Athlete.id == Subscription.athlete_id)
        .filter(
            Subscription.athlete_id.in_(athlete_ids),
            Subscription.is_active.is_(True),
            Subscription.start_date.isnot(None),
            Subscription.start_date <= as_of,
            Subscription.end_date >= as_of,
        )
    )
    if coach_sport:
        q = q.filter(_subscription_sport_match_sql(coach_sport))
    return int(q.scalar() or 0)


def _count_new_athletes_in_period(
    session: OrmSession,
    coach: Coach,
    coach_sport: Optional[str],
    period_start: datetime,
    period_end_excl: datetime,
) -> int:
    q = session.query(func.count(Athlete.id)).filter(
        Athlete.created_by == coach.id,
        Athlete.created_at.isnot(None),
        Athlete.created_at >= period_start,
        Athlete.created_at < period_end_excl,
    )
    if coach_sport:
        has_sub_for_sport = exists().where(
            Subscription.athlete_id == Athlete.id,
            or_(
                Subscription.sport_type == coach_sport,
                and_(
                    or_(
                        Subscription.sport_type.is_(None),
                        Subscription.sport_type == "",
                    ),
                    Athlete.sport_type == coach_sport,
                ),
            ),
        )
        q = q.filter(or_(Athlete.sport_type == coach_sport, has_sub_for_sport))
    return int(q.scalar() or 0)


def _count_attendance_pair(
    session: OrmSession,
    athlete_ids: List[int],
    coach_sport: Optional[str],
    period_start: datetime,
    period_end_excl: datetime,
) -> Tuple[int, int]:
    if not athlete_ids:
        return 0, 0

    def one(attended: bool) -> int:
        q = (
            session.query(func.count(Attendance.id))
            .join(Training, Training.id == Attendance.training_id)
            .filter(
                Attendance.athlete_id.in_(athlete_ids),
                Attendance.attended.is_(attended),
                Training.training_date.isnot(None),
                Training.training_date >= period_start,
                Training.training_date < period_end_excl,
                Training.is_cancelled.is_(False),
            )
        )
        if coach_sport:
            q = q.filter(Training.sport_type == coach_sport)
        return int(q.scalar() or 0)

    return one(True), one(False)


def _count_distinct_athletes_attendance_pair(
    session: OrmSession,
    athlete_ids: List[int],
    coach_sport: Optional[str],
    period_start: datetime,
    period_end_excl: datetime,
) -> Tuple[int, int]:
    """Сколько разных спортсменов имели хотя бы одну отметку «был» / «не был» за период."""

    def one(attended: bool) -> int:
        if not athlete_ids:
            return 0
        q = (
            session.query(func.count(func.distinct(Attendance.athlete_id)))
            .join(Training, Training.id == Attendance.training_id)
            .filter(
                Attendance.athlete_id.in_(athlete_ids),
                Attendance.attended.is_(attended),
                Training.training_date.isnot(None),
                Training.training_date >= period_start,
                Training.training_date < period_end_excl,
                Training.is_cancelled.is_(False),
            )
        )
        if coach_sport:
            q = q.filter(Training.sport_type == coach_sport)
        return int(q.scalar() or 0)

    return one(True), one(False)


def _count_subscription_starts(
    session: OrmSession,
    athlete_ids: List[int],
    coach_sport: Optional[str],
    period_start: datetime,
    period_end_excl: datetime,
) -> int:
    if not athlete_ids:
        return 0
    q = (
        session.query(func.count(Subscription.id))
        .join(Athlete, Athlete.id == Subscription.athlete_id)
        .filter(
            Subscription.athlete_id.in_(athlete_ids),
            Subscription.start_date.isnot(None),
            Subscription.start_date >= period_start,
            Subscription.start_date < period_end_excl,
        )
    )
    if coach_sport:
        q = q.filter(_subscription_sport_match_sql(coach_sport))
    return int(q.scalar() or 0)


def _count_new_subscription_rows(
    session: OrmSession,
    athlete_ids: List[int],
    coach_sport: Optional[str],
    period_start: datetime,
    period_end_excl: datetime,
) -> int:
    if not athlete_ids:
        return 0
    q = (
        session.query(func.count(Subscription.id))
        .join(Athlete, Athlete.id == Subscription.athlete_id)
        .filter(
            Subscription.athlete_id.in_(athlete_ids),
            Subscription.created_at.isnot(None),
            Subscription.created_at >= period_start,
            Subscription.created_at < period_end_excl,
        )
    )
    if coach_sport:
        q = q.filter(_subscription_sport_match_sql(coach_sport))
    return int(q.scalar() or 0)


def _revenue_in_period(
    session: OrmSession,
    athlete_ids: List[int],
    coach_sport: Optional[str],
    period_start: datetime,
    period_end_excl: datetime,
) -> Tuple[int, int]:
    """Сумма оплат (₽) и число платёжных записей за период по полю paid_at."""
    if not athlete_ids:
        return 0, 0

    total = (
        session.query(func.coalesce(func.sum(SubscriptionPayment.amount_rubles), 0))
        .select_from(SubscriptionPayment)
        .join(Subscription, Subscription.id == SubscriptionPayment.subscription_id)
        .join(Athlete, Athlete.id == Subscription.athlete_id)
        .filter(
            Subscription.athlete_id.in_(athlete_ids),
            SubscriptionPayment.paid_at >= period_start,
            SubscriptionPayment.paid_at < period_end_excl,
        )
    )
    if coach_sport:
        total = total.filter(_subscription_sport_match_sql(coach_sport))
    nq = (
        session.query(func.count(SubscriptionPayment.id))
        .select_from(SubscriptionPayment)
        .join(Subscription, Subscription.id == SubscriptionPayment.subscription_id)
        .join(Athlete, Athlete.id == Subscription.athlete_id)
        .filter(
            Subscription.athlete_id.in_(athlete_ids),
            SubscriptionPayment.paid_at >= period_start,
            SubscriptionPayment.paid_at < period_end_excl,
        )
    )
    if coach_sport:
        nq = nq.filter(_subscription_sport_match_sql(coach_sport))
    return int(total.scalar() or 0), int(nq.scalar() or 0)


def _estimated_revenue_from_subscription_starts(
    session: OrmSession,
    athlete_ids: List[int],
    coach_sport: Optional[str],
    period_start: datetime,
    period_end_excl: datetime,
) -> int:
    """
    Сумма по стартам абонемента в периоде при текущих тарифах (subscription_tariffs, затем env —
    как resolve_subscription_activation_price_rubles при активации). Не заменяет факт из subscription_payments.
    """
    if not athlete_ids:
        return 0
    q = (
        session.query(Subscription)
        .join(Athlete, Athlete.id == Subscription.athlete_id)
        .filter(
            Subscription.athlete_id.in_(athlete_ids),
            Subscription.start_date.isnot(None),
            Subscription.start_date >= period_start,
            Subscription.start_date < period_end_excl,
        )
    )
    if coach_sport:
        q = q.filter(_subscription_sport_match_sql(coach_sport))
    total = 0
    for sub in q:
        p = resolve_subscription_activation_price_rubles(session, sub)
        if p is not None:
            total += p
    return total
