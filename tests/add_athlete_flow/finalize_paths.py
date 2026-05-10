"""Финализация сценария добавления спортсмена."""

from datetime import datetime
from types import SimpleNamespace

import pytest

import handlers.coach_handlers as ch
from tests.helpers import (
    ctx as _ctx,
    run_async as _run,
    update_with_query as _update_with_query,
)

pytestmark = pytest.mark.flow


def test_finalize_monthly_uses_12th_training_end(monkeypatch):
    monkeypatch.setattr("database.db_utils._find_nearest_training_date", lambda *_a, **_k: datetime(2026, 4, 12, 20, 0))
    monkeypatch.setattr("database.db_utils.is_training_in_global_freeze", lambda *_a, **_k: False)
    monkeypatch.setattr("database.db_utils._calculate_12th_training_date", lambda *_a, **_k: datetime(2026, 5, 31, 21, 30))

    class _FakeSession:
        def query(self, model):
            class _Q:
                def filter_by(self, **_kwargs):
                    return self

                def first(self):
                    return None if model.__name__ == "Training" else None

            return _Q()

        def add(self, _obj):
            return None

        def flush(self):
            return None

        def commit(self):
            return None

        def close(self):
            return None

    monkeypatch.setattr(ch, "Session", lambda: _FakeSession())
    monkeypatch.setattr(ch, "create_athlete", lambda **_k: SimpleNamespace(id=1))
    holder = {}

    def _fake_create_subscription(**_kwargs):
        sub = SimpleNamespace(id=1, end_date=None, is_active=False, trainings_remaining=12)
        holder["sub"] = sub
        return sub

    monkeypatch.setattr("database.db_utils.create_subscription", _fake_create_subscription)
    monkeypatch.setattr("database.db_utils._create_and_deduct_scheduled_trainings", lambda *_a, **_k: None)
    monkeypatch.setattr("database.db_utils.sync_subscription_trainings_remaining", lambda *_a, **_k: None)
    context = _ctx(
        {
            "sport_type": "Тайский Бокс",
            "age_group": "adults",
            "subscription_type": "monthly",
            "subscription_type_ru": "Месячный",
            "full_name": "Тест",
            "phone": "+7-1",
            "medical_info": "нет",
            "coach_id": 1,
        }
    )
    state = _run(
        ch._finalize_add_athlete_from_selected_date(
            _update_with_query("addath_date_2026_4_12").callback_query,
            context,
            datetime(2026, 4, 12),
        )
    )
    assert state == ch.ConversationHandler.END
    assert holder["sub"].end_date == datetime(2026, 5, 31, 21, 30)


def test_finalize_single_uses_training_end_time(monkeypatch):
    monkeypatch.setattr("database.db_utils._find_nearest_training_date", lambda *_a, **_k: datetime(2026, 4, 12, 20, 0))
    monkeypatch.setattr("database.db_utils.is_training_in_global_freeze", lambda *_a, **_k: False)
    monkeypatch.setattr("database.db_utils.training_end_time", lambda dt: dt.replace(hour=21, minute=30))

    class _FakeSession:
        def query(self, model):
            class _Q:
                def filter_by(self, **_kwargs):
                    return self

                def first(self):
                    return None if model.__name__ == "Training" else None

            return _Q()

        def add(self, _obj):
            return None

        def flush(self):
            return None

        def commit(self):
            return None

        def close(self):
            return None

    monkeypatch.setattr(ch, "Session", lambda: _FakeSession())
    monkeypatch.setattr(ch, "create_athlete", lambda **_k: SimpleNamespace(id=1))
    holder = {}

    def _fake_create_subscription(**_kwargs):
        sub = SimpleNamespace(id=1, end_date=None, is_active=False, trainings_remaining=1)
        holder["sub"] = sub
        return sub

    monkeypatch.setattr("database.db_utils.create_subscription", _fake_create_subscription)
    monkeypatch.setattr("database.db_utils.sync_subscription_trainings_remaining", lambda *_a, **_k: None)
    context = _ctx(
        {
            "sport_type": "Тайский Бокс",
            "age_group": "adults",
            "subscription_type": "single",
            "subscription_type_ru": "Разовый",
            "full_name": "Тест",
            "phone": "+7-1",
            "medical_info": "нет",
            "coach_id": 1,
        }
    )
    state = _run(
        ch._finalize_add_athlete_from_selected_date(
            _update_with_query("addath_date_2026_4_12").callback_query,
            context,
            datetime(2026, 4, 12),
        )
    )
    assert state == ch.ConversationHandler.END
    assert holder["sub"].end_date == datetime(2026, 4, 12, 21, 30)


def test_finalize_on_create_athlete_error_shows_message(monkeypatch):
    monkeypatch.setattr("database.db_utils._find_nearest_training_date", lambda *_a, **_k: datetime(2026, 4, 12, 20, 0))
    monkeypatch.setattr("database.db_utils.is_training_in_global_freeze", lambda *_a, **_k: False)

    class _FakeSession:
        def query(self, _model):
            class _Q:
                def filter_by(self, **_kwargs):
                    return self

                def first(self):
                    return None

            return _Q()

        def add(self, _obj):
            return None

        def flush(self):
            return None

        def commit(self):
            return None

        def close(self):
            return None

    monkeypatch.setattr(ch, "Session", lambda: _FakeSession())
    monkeypatch.setattr(ch, "create_athlete", lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("DB fail")))
    context = _ctx(
        {
            "sport_type": "Тайский Бокс",
            "age_group": "adults",
            "subscription_type": "single",
            "subscription_type_ru": "Разовый",
            "full_name": "Тест",
            "phone": "+7-1",
            "medical_info": "нет",
            "coach_id": 1,
        }
    )
    update = _update_with_query("addath_date_2026_4_12")
    state = _run(ch._finalize_add_athlete_from_selected_date(update.callback_query, context, datetime(2026, 4, 12)))
    assert state == ch.ConversationHandler.END
