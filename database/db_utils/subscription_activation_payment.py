"""Автозапись оплаты при активации абонемента (фиксированная сумма по типу из env)."""
from __future__ import annotations

import os
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session as OrmSession

from database.models import Subscription, SubscriptionPayment


def activation_price_rubles(subscription_type: Optional[str]) -> Optional[int]:
    """
    Сумма в рублях для типа абонемента из окружения.
    Если переменная не задана, пустая или не число — None (платёж не создаём).
    """
    if subscription_type == "monthly":
        raw = os.getenv("SUBSCRIPTION_PRICE_MONTHLY_RUB", "").strip()
    elif subscription_type == "single":
        raw = os.getenv("SUBSCRIPTION_PRICE_SINGLE_RUB", "").strip()
    else:
        return None
    if not raw:
        return None
    try:
        n = int(raw)
    except ValueError:
        return None
    if n <= 0:
        return None
    return n


def record_payment_on_subscription_activation(
    session: OrmSession,
    subscription: Subscription,
    paid_at: datetime,
    *,
    recorded_by_telegram_id: Optional[int] = None,
) -> None:
    """
    Одна строка в subscription_payments при активации, если для типа задана цена в env.
    paid_at — как правило дата/время старта абонемента (первая тренировка).
    """
    amount = activation_price_rubles(subscription.subscription_type)
    if amount is None:
        return
    session.add(
        SubscriptionPayment(
            subscription_id=subscription.id,
            amount_rubles=amount,
            paid_at=paid_at,
            note="Активация абонемента",
            recorded_by_telegram_id=recorded_by_telegram_id,
        )
    )
