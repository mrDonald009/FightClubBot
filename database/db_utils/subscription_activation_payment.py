"""Автозапись оплаты при активации абонемента (сумма только из subscription_tariffs)."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session as OrmSession

from database.db_utils.subscription_tariffs import (
    find_active_tariff_amount_rubles,
    tariff_kind_for_subscription_type,
)
from database.models import Athlete, Subscription, SubscriptionPayment


def _effective_subscription_sport_type(session: OrmSession, subscription: Subscription) -> str:
    st = (subscription.sport_type or "").strip()
    if st:
        return st
    if subscription.athlete_id:
        row = session.query(Athlete.sport_type).filter_by(id=subscription.athlete_id).first()
        if row and row[0]:
            return (row[0] or "").strip()
    return ""


def resolve_subscription_activation_price_rubles(
    session: OrmSession,
    subscription: Subscription,
) -> Optional[int]:
    """Сумма при активации — активная строка subscription_tariffs по виду спорта и типу абонемента."""
    kind = tariff_kind_for_subscription_type(subscription.subscription_type)
    if not kind:
        return None
    sport = _effective_subscription_sport_type(session, subscription)
    return find_active_tariff_amount_rubles(
        session, sport_type_name=sport, tariff_kind=kind
    )


def record_payment_on_subscription_activation(
    session: OrmSession,
    subscription: Subscription,
    paid_at: datetime,
    *,
    recorded_by_telegram_id: Optional[int] = None,
) -> None:
    """
    Одна строка в subscription_payments при активации, если для вида спорта и типа есть тариф в БД.
    paid_at — дата/время активации (первая тренировка).
    """
    amount = resolve_subscription_activation_price_rubles(session, subscription)
    if amount is None:
        return
    kind = tariff_kind_for_subscription_type(subscription.subscription_type)
    session.add(
        SubscriptionPayment(
            subscription_id=subscription.id,
            amount_rubles=amount,
            paid_at=paid_at,
            payment_kind=kind,
            note="Активация абонемента",
            recorded_by_telegram_id=recorded_by_telegram_id,
        )
    )
