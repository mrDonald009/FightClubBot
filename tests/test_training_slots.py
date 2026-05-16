"""Слоты индивидуальных тренировок: дедупликация по времени без разделения дети/взрослые."""
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

from database.db_utils.training_slots import (
    dedupe_individual_trainings_by_slot,
    individual_slot_conflicts,
    iter_allowed_individual_starts,
    scheduled_group_training_intervals,
)


def _empty_session():
    """Сессия без тренировок и без занятых individual-подписок."""
    session = MagicMock()
    q = session.query.return_value
    q.filter.return_value.order_by.return_value.all.return_value = []
    q.filter.return_value.count.return_value = 0
    return session


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


def test_active_subscription_for_training_prefers_individual_for_individual_slot():
    from utils.subscription_resolve import active_subscription_for_training

    slot_dt = datetime(2026, 5, 11, 14, 0, 0)
    group_sub = SimpleNamespace(
        id=1,
        sport_type="MMA",
        is_active=True,
        subscription_type="monthly",
        start_date=datetime(2026, 5, 1, 0, 0, 0),
    )
    individual_sub = SimpleNamespace(
        id=2,
        sport_type="MMA",
        is_active=True,
        subscription_type="individual",
        start_date=slot_dt,
    )
    athlete = SimpleNamespace(
        age_group="adults",
        subscriptions=[group_sub, individual_sub],
        sport_type="MMA",
    )
    training = SimpleNamespace(
        sport_type="MMA",
        age_group="adults",
        training_format="individual",
        training_date=slot_dt,
    )
    assert active_subscription_for_training(athlete, training) is individual_sub


def test_active_subscription_for_training_prefers_group_for_group_slot():
    from utils.subscription_resolve import active_subscription_for_training

    group_sub = SimpleNamespace(
        id=10,
        sport_type="MMA",
        is_active=True,
        subscription_type="monthly",
    )
    individual_sub = SimpleNamespace(
        id=2,
        sport_type="MMA",
        is_active=True,
        subscription_type="individual",
        start_date=datetime(2026, 5, 11, 14, 0, 0),
    )
    athlete = SimpleNamespace(
        age_group="adults",
        subscriptions=[individual_sub, group_sub],
        sport_type="MMA",
    )
    training = SimpleNamespace(
        sport_type="MMA",
        age_group="adults",
        training_format=None,
        training_date=datetime(2026, 5, 11, 20, 0, 0),
    )
    assert active_subscription_for_training(athlete, training) is group_sub


def test_scheduled_group_intervals_tuesday_thai_only():
    """Вторник: тайский (дети 18:00, взрослые 20:00), MMA в этот день нет."""
    day = date(2026, 4, 7)
    assert day.weekday() == 1
    starts = sorted(t[0] for t in scheduled_group_training_intervals(day))
    assert starts == [
        datetime(2026, 4, 7, 18, 0),
        datetime(2026, 4, 7, 20, 0),
    ]


def test_scheduled_group_intervals_monday_mma():
    """Понедельник: MMA дети 18:00, взрослые 20:00; тайского в этот день нет."""
    day = date(2026, 4, 6)
    assert day.weekday() == 0
    starts = sorted(t[0] for t in scheduled_group_training_intervals(day))
    assert starts == [
        datetime(2026, 4, 6, 18, 0),
        datetime(2026, 4, 6, 20, 0),
    ]


def test_iter_allowed_individual_blocks_group_times_on_tuesday():
    day = date(2026, 4, 7)
    session = _empty_session()
    starts = iter_allowed_individual_starts(
        session,
        coach_id=1,
        sport_type="MMA",
        day=day,
        now_cutoff=datetime(2026, 4, 7, 0, 0),
    )
    labels = {s.strftime("%H:%M") for s in starts}
    assert "09:00" in labels
    assert "18:00" not in labels
    assert "20:00" not in labels
    assert "17:30" not in labels
    assert "19:30" not in labels


def test_iter_allowed_individual_last_start_is_22_00():
    day = date(2026, 4, 6)
    session = _empty_session()
    starts = iter_allowed_individual_starts(
        session,
        coach_id=1,
        sport_type="MMA",
        day=day,
        now_cutoff=datetime(2026, 4, 6, 0, 0),
    )
    assert starts
    assert max(starts).strftime("%H:%M") == "22:00"
    assert "22:30" not in {s.strftime("%H:%M") for s in starts}


def test_individual_slot_conflicts_with_schedule_without_db_rows():
    session = _empty_session()
    assert individual_slot_conflicts(
        session, 1, "MMA", datetime(2026, 4, 7, 18, 0)
    )
    assert not individual_slot_conflicts(
        session, 1, "MMA", datetime(2026, 4, 7, 10, 0)
    )
