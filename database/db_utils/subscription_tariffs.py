"""Тарифы subscription_tariffs: поиск активной суммы по виду спорта и типу продукта."""
from __future__ import annotations

from typing import Optional, Tuple

from sqlalchemy.orm import Session as OrmSession

from database.models import SubscriptionTariff

TARIFF_KIND_SUBSCRIPTION_MONTHLY = "subscription_monthly"
TARIFF_KIND_SUBSCRIPTION_SINGLE = "subscription_single"
TARIFF_KIND_INDIVIDUAL_TRAINING = "individual_training"


def tariff_kind_for_subscription_type(subscription_type: Optional[str]) -> Optional[str]:
    if subscription_type == "monthly":
        return TARIFF_KIND_SUBSCRIPTION_MONTHLY
    if subscription_type == "single":
        return TARIFF_KIND_SUBSCRIPTION_SINGLE
    if subscription_type == "individual":
        return TARIFF_KIND_INDIVIDUAL_TRAINING
    return None


def find_active_tariff_amount_rubles(
    session: OrmSession,
    *,
    sport_type_name: str,
    tariff_kind: str,
) -> Optional[int]:
    """
    Активная сумма из subscription_tariffs.

    Учитываются только строки с тем же видом спорта, что у абонемента (без учёта регистра и пробелов
    по краям). Общего тарифа «на все виды» нет — строку нужно завести для каждого нужного вида.

    При нескольких совпадениях берётся строка с наибольшим id.
    """
    sport_key = (sport_type_name or "").strip().lower()
    if not sport_key:
        return None

    rows = (
        session.query(SubscriptionTariff)
        .filter(
            SubscriptionTariff.is_active.is_(True),
            SubscriptionTariff.tariff_kind == tariff_kind,
        )
        .order_by(SubscriptionTariff.id.desc())
        .all()
    )
    for r in rows:
        name = (r.sport_type_name or "").strip()
        if not name:
            continue
        if name.lower() != sport_key:
            continue
        amount = r.amount_rubles
        if amount is None or amount <= 0:
            return None
        return int(amount)
    return None


def tariff_preview_for_sport(
    session: OrmSession,
    sport_type_name: Optional[str],
) -> Tuple[Optional[int], Optional[int], Optional[int]]:
    """Активные суммы из БД: (месячный абонемент, разовый, индивидуальная тренировка)."""
    sport = (sport_type_name or "").strip()
    if not sport:
        return None, None, None
    m = find_active_tariff_amount_rubles(
        session, sport_type_name=sport, tariff_kind=TARIFF_KIND_SUBSCRIPTION_MONTHLY
    )
    s = find_active_tariff_amount_rubles(
        session, sport_type_name=sport, tariff_kind=TARIFF_KIND_SUBSCRIPTION_SINGLE
    )
    ind = find_active_tariff_amount_rubles(
        session, sport_type_name=sport, tariff_kind=TARIFF_KIND_INDIVIDUAL_TRAINING
    )
    return m, s, ind


def tariff_preview_monthly_single_for_sport(
    session: OrmSession,
    sport_type_name: Optional[str],
) -> Tuple[Optional[int], Optional[int]]:
    """Обратная совместимость: только (месячный, разовый)."""
    m, s, _ = tariff_preview_for_sport(session, sport_type_name)
    return m, s
