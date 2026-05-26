"""Выбор абонемента при нескольких активных направлениях у одного спортсмена."""
from typing import List, Optional

from database.db_utils.training_slots import TRAINING_FORMAT_INDIVIDUAL
from database.models import Athlete, Subscription, Training


def _subscription_sport(sub: Subscription, athlete: Athlete) -> str:
    return (sub.sport_type or athlete.sport_type or "").strip()


def active_subscriptions_all(athlete: Athlete) -> List[Subscription]:
    return [s for s in athlete.subscriptions if s.is_active]


def first_active_subscription(athlete: Athlete) -> Optional[Subscription]:
    """Стабильный «первый» активный абонемент (минимальный id)."""
    act = active_subscriptions_all(athlete)
    if not act:
        return None
    return min(act, key=lambda s: s.id)


def active_subscription_for_sport(
    athlete: Athlete, sport_type: Optional[str]
) -> Optional[Subscription]:
    """Активный абонемент по виду спорта (строка как в subscription.sport_type)."""
    if not sport_type or not str(sport_type).strip():
        return first_active_subscription(athlete)
    st = str(sport_type).strip()
    matches = [
        s
        for s in active_subscriptions_all(athlete)
        if _subscription_sport(s, athlete) == st
    ]
    if not matches:
        return None
    return min(matches, key=lambda s: s.id)


def active_subscription_for_training(
    athlete: Athlete, training: Optional[Training]
) -> Optional[Subscription]:
    """Абонемент, соответствующий формату и времени слота.

    Правила:
    - индивидуальная тренировка -> приоритет subscription_type == individual
      (по возможности с тем же start_date);
    - групповая тренировка -> subscription_type != individual;
    - вид спорта должен совпадать;
    - для групповых дополнительно проверяется age_group спортсмена.
    """
    if not training:
        return None
    is_individual = (
        (getattr(training, "training_format", None) or "").strip().lower()
        == TRAINING_FORMAT_INDIVIDUAL
    )
    if not is_individual:
        if athlete.age_group and training.age_group and athlete.age_group != training.age_group:
            return None
    sport = (training.sport_type or "").strip()
    if not sport:
        return first_active_subscription(athlete)

    candidates = [
        s for s in active_subscriptions_all(athlete)
        if _subscription_sport(s, athlete) == sport
    ]
    if not candidates:
        return None

    if is_individual:
        individual_subs = [
            s
            for s in candidates
            if (getattr(s, "subscription_type", None) or "").strip().lower() == "individual"
        ]
        if not individual_subs:
            # Legacy fallback: у старых записей тип мог быть пустым.
            legacy_untyped = [
                s
                for s in candidates
                if not (getattr(s, "subscription_type", None) or "").strip()
            ]
            if legacy_untyped:
                individual_subs = legacy_untyped
            elif len(candidates) == 1:
                return candidates[0]
        if not individual_subs:
            return None
        # Для индивидуального слота сначала ищем совпадение по минуте старта,
        # затем мягкий fallback на любой individual по спорту.
        training_dt = getattr(training, "training_date", None)
        if training_dt is not None:
            slot_key = training_dt.strftime("%Y-%m-%d %H:%M")
            exact = [
                s
                for s in individual_subs
                if getattr(s, "start_date", None) is not None
                and s.start_date.strftime("%Y-%m-%d %H:%M") == slot_key
            ]
            if exact:
                return min(exact, key=lambda s: s.id)
        return min(individual_subs, key=lambda s: s.id)

    group_subs = [
        s
        for s in candidates
        if (getattr(s, "subscription_type", None) or "").strip().lower() != "individual"
    ]
    if not group_subs:
        return None
    return min(group_subs, key=lambda s: s.id)


def subscription_for_coach_sport(
    athlete: Athlete, coach_sport_type: Optional[str]
) -> Optional[Subscription]:
    """Для UI тренера: абонемент по его виду спорта или любой активный, если вид не задан."""
    if coach_sport_type:
        return active_subscription_for_sport(athlete, coach_sport_type)
    return first_active_subscription(athlete)
