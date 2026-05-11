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
    """Абонемент, соответствующий слоту тренировки (вид спорта; для групповых ещё возрастная группа)."""
    if not training:
        return None
    is_individual = (
        (getattr(training, "training_format", None) or "").strip().lower()
        == TRAINING_FORMAT_INDIVIDUAL
    )
    if not is_individual:
        if athlete.age_group and training.age_group and athlete.age_group != training.age_group:
            return None
    return active_subscription_for_sport(athlete, training.sport_type)


def subscription_for_coach_sport(
    athlete: Athlete, coach_sport_type: Optional[str]
) -> Optional[Subscription]:
    """Для UI тренера: абонемент по его виду спорта или любой активный, если вид не задан."""
    if coach_sport_type:
        return active_subscription_for_sport(athlete, coach_sport_type)
    return first_active_subscription(athlete)
