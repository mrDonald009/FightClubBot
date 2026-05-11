"""Слоты индивидуальных тренировок: дедупликация по времени без разделения дети/взрослые."""
from datetime import datetime
from types import SimpleNamespace

from database.db_utils.training_slots import dedupe_individual_trainings_by_slot


def test_dedupe_individual_trainings_keeps_one_per_slot():
    t1 = SimpleNamespace(
        id=10,
        coach_id=1,
        sport_type="MMA",
        training_date=datetime(2026, 5, 11, 14, 0, 0),
        training_format="individual",
    )
    t2 = SimpleNamespace(
        id=11,
        coach_id=1,
        sport_type="MMA",
        training_date=datetime(2026, 5, 11, 14, 0, 0),
        training_format="individual",
    )
    g1 = SimpleNamespace(
        id=3,
        coach_id=1,
        sport_type="MMA",
        training_date=datetime(2026, 5, 11, 18, 0, 0),
        training_format=None,
    )
    out = dedupe_individual_trainings_by_slot([t2, g1, t1])
    assert [x.id for x in out] == [10, 3]


def test_active_subscription_for_training_individual_allows_mixed_age_vs_training():
    from utils.subscription_resolve import active_subscription_for_training

    sub = SimpleNamespace(id=1, sport_type="MMA", is_active=True)
    athlete = SimpleNamespace(age_group="children", subscriptions=[sub], sport_type="MMA")
    training = SimpleNamespace(sport_type="MMA", age_group="adults", training_format="individual")
    assert active_subscription_for_training(athlete, training) is sub


def test_active_subscription_for_training_group_still_matches_age():
    from utils.subscription_resolve import active_subscription_for_training

    sub = SimpleNamespace(id=1, sport_type="MMA", is_active=True)
    athlete = SimpleNamespace(age_group="children", subscriptions=[sub], sport_type="MMA")
    training = SimpleNamespace(sport_type="MMA", age_group="adults", training_format=None)
    assert active_subscription_for_training(athlete, training) is None
