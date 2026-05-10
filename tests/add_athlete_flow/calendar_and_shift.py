"""Календарные и shift-ветки сценария добавления спортсмена."""

from datetime import datetime
from unittest.mock import AsyncMock

import pytest

import handlers.coach_handlers as ch
from tests.helpers import (
    ctx as _ctx,
    run_async as _run,
    update_with_query as _update_with_query,
)

pytestmark = pytest.mark.flow


def test_finalize_detects_global_freeze_and_requests_confirm(monkeypatch):
    class _FakeQuery:
        def filter(self, *args, **kwargs):
            return self

        def order_by(self, *args, **kwargs):
            return self

        def first(self):
            return type("GF", (), {"start_date": datetime(2026, 4, 1), "end_date": datetime(2026, 4, 10, 23, 59)})()

    class _FakeSession:
        def query(self, _model):
            return _FakeQuery()

        def close(self):
            return None

    monkeypatch.setattr(ch, "Session", lambda: _FakeSession())
    monkeypatch.setattr("database.db_utils._find_nearest_training_date", lambda *_a, **_k: datetime(2026, 4, 3, 20, 0))
    monkeypatch.setattr("database.db_utils.is_training_in_global_freeze", lambda *_a, **_k: True)
    monkeypatch.setattr("database.db_utils.find_next_non_frozen_training_date", lambda *_a, **_k: datetime(2026, 4, 12, 20, 0))
    update = _update_with_query("addath_date_2026_4_3")
    context = _ctx(
        {
            "sport_type": "Тайский Бокс",
            "age_group": "adults",
            "subscription_type": "single",
            "subscription_type_ru": "Разовый",
            "full_name": "Тест",
            "phone": "+7-900-000-00-00",
            "medical_info": "Нет противопоказаний",
            "coach_id": 1,
        }
    )
    state = _run(ch._finalize_add_athlete_from_selected_date(update.callback_query, context, datetime(2026, 4, 3)))
    assert state == ch.ATHLETE_TRAINING_DATE
    assert context.user_data["pending_shifted_start_date"] == "2026-04-12T20:00:00"


def test_shift_confirm_without_pending_returns_error():
    update = _update_with_query("addath_shift_confirm")
    state = _run(ch.handle_add_athlete_shift_confirm(update, _ctx({})))
    assert state == ch.ATHLETE_TRAINING_DATE
    assert "Данные сессии утеряны" in update.callback_query.edits[-1]["text"]


def test_shift_confirm_with_pending_calls_finalize(monkeypatch):
    calls = {}

    async def _fake_finalize(query, context, coach_selected_date, *, skip_freeze_confirm=False):
        calls["date"] = coach_selected_date
        calls["skip"] = skip_freeze_confirm
        return ch.ConversationHandler.END

    monkeypatch.setattr(ch, "_finalize_add_athlete_from_selected_date", _fake_finalize)
    context = _ctx({"pending_shifted_start_date": "2026-04-12T20:00:00"})
    state = _run(ch.handle_add_athlete_shift_confirm(_update_with_query("addath_shift_confirm"), context))
    assert state == ch.ConversationHandler.END
    assert calls["skip"] is True


def test_shift_confirm_uses_callback_payload_without_user_data(monkeypatch):
    calls = {}

    async def _fake_finalize(query, context, coach_selected_date, *, skip_freeze_confirm=False):
        calls["date"] = coach_selected_date
        calls["skip"] = skip_freeze_confirm
        return ch.ConversationHandler.END

    monkeypatch.setattr(ch, "_finalize_add_athlete_from_selected_date", _fake_finalize)
    state = _run(ch.handle_add_athlete_shift_confirm(_update_with_query("addath_shift_confirm_202604121430"), _ctx({})))
    assert state == ch.ConversationHandler.END
    assert calls["date"] == datetime(2026, 4, 12, 14, 30)
    assert calls["skip"] is True


def test_shift_confirm_rejects_callback_pending_mismatch(monkeypatch):
    finalize = AsyncMock(return_value=ch.ConversationHandler.END)
    monkeypatch.setattr(ch, "_finalize_add_athlete_from_selected_date", finalize)
    update = _update_with_query("addath_shift_confirm_202604121400")
    state = _run(ch.handle_add_athlete_shift_confirm(update, _ctx({"pending_shifted_start_date": "2026-04-12T20:00:00"})))
    assert state == ch.ATHLETE_TRAINING_DATE
    finalize.assert_not_called()


def test_shift_confirm_callback_matches_pending_calls_finalize(monkeypatch):
    calls = {}

    async def _fake_finalize(query, context, coach_selected_date, *, skip_freeze_confirm=False):
        calls["date"] = coach_selected_date
        return ch.ConversationHandler.END

    monkeypatch.setattr(ch, "_finalize_add_athlete_from_selected_date", _fake_finalize)
    state = _run(
        ch.handle_add_athlete_shift_confirm(
            _update_with_query("addath_shift_confirm_202604122000"),
            _ctx({"pending_shifted_start_date": "2026-04-12T20:00:00"}),
        )
    )
    assert state == ch.ConversationHandler.END
    assert calls["date"] == datetime(2026, 4, 12, 20, 0)


def test_shift_cancel_returns_to_calendar(monkeypatch):
    sentinel_kb = object()
    monkeypatch.setattr(ch, "create_add_athlete_training_calendar", lambda *_a, **_k: sentinel_kb)
    update = _update_with_query("addath_shift_cancel")
    context = _ctx({"pending_shifted_start_date": "2026-04-12T20:00:00", "sport_type": "Тайский Бокс", "age_group": "adults"})
    state = _run(ch.handle_add_athlete_shift_cancel(update, context))
    assert state == ch.ATHLETE_TRAINING_DATE
    assert "pending_shifted_start_date" not in context.user_data


def test_calendar_nav_session_expired():
    update = _update_with_query("addath_cal_2026_5")
    state = _run(ch.handle_add_athlete_calendar_nav(update, _ctx({})))
    assert state == ch.ConversationHandler.END


def test_calendar_nav_malformed_callback():
    update = _update_with_query("addath_cal_2026")
    context = _ctx({"subscription_type": "single", "sport_type": "Тайский Бокс", "age_group": "adults"})
    state = _run(ch.handle_add_athlete_calendar_nav(update, context))
    assert state == ch.ATHLETE_TRAINING_DATE


def test_calendar_nav_updates_markup(monkeypatch):
    kb = object()
    monkeypatch.setattr(ch, "create_add_athlete_training_calendar", lambda *_a, **_k: kb)
    update = _update_with_query("addath_cal_2026_5")
    context = _ctx({"subscription_type": "single", "sport_type": "Тайский Бокс", "age_group": "adults"})
    state = _run(ch.handle_add_athlete_calendar_nav(update, context))
    assert state == ch.ATHLETE_TRAINING_DATE
    assert update.callback_query.edit_reply_markup_calls[-1]["reply_markup"] is kb


def test_calendar_date_pick_malformed_parts():
    update = _update_with_query("addath_date_2026_4")
    context = _ctx({"subscription_type": "single", "sport_type": "Тайский Бокс", "age_group": "adults"})
    state = _run(ch.handle_add_athlete_calendar_date_pick(update, context))
    assert state == ch.ATHLETE_TRAINING_DATE


def test_calendar_date_pick_session_expired():
    update = _update_with_query("addath_date_2026_4_10")
    state = _run(ch.handle_add_athlete_calendar_date_pick(update, _ctx({})))
    assert state == ch.ConversationHandler.END


def test_calendar_date_pick_no_schedule_ends():
    update = _update_with_query("addath_date_2026_4_10")
    context = _ctx({"subscription_type": "single", "sport_type": "НетТакогоСпорта", "age_group": "adults"})
    state = _run(ch.handle_add_athlete_calendar_date_pick(update, context))
    assert state == ch.ConversationHandler.END


def test_calendar_ignore_stays_on_training_date():
    state = _run(ch.handle_add_athlete_calendar_ignore(_update_with_query("addath_ignore"), _ctx({"subscription_type": "single"})))
    assert state == ch.ATHLETE_TRAINING_DATE


def test_training_date_selection_no_subscription():
    state = _run(ch.handle_training_date_selection(_update_with_query("select_training_date_2026-04-12-20-0"), _ctx({})))
    assert state == ch.ConversationHandler.END


def test_training_date_selection_bad_payload():
    update = _update_with_query("select_training_date_broken")
    state = _run(ch.handle_training_date_selection(update, _ctx({"subscription_type": "single"})))
    assert state == ch.ConversationHandler.END
    assert "Ошибка" in update.callback_query.edits[-1]["text"]


def test_training_date_selection_delegates_to_finalize(monkeypatch):
    calls = []

    async def _fake_finalize(query, context, dt, *, skip_freeze_confirm=False):
        calls.append((dt, skip_freeze_confirm))
        return ch.ConversationHandler.END

    monkeypatch.setattr(ch, "_finalize_add_athlete_from_selected_date", _fake_finalize)
    state = _run(
        ch.handle_training_date_selection(
            _update_with_query("select_training_date_2026-04-12-20-30"),
            _ctx({"subscription_type": "single"}),
        )
    )
    assert state == ch.ConversationHandler.END
    assert calls[0][0] == datetime(2026, 4, 12, 20, 30)


def test_shift_cancel_when_calendar_unavailable(monkeypatch):
    monkeypatch.setattr(ch, "create_add_athlete_training_calendar", lambda *_a, **_k: None)
    update = _update_with_query("addath_shift_cancel")
    state = _run(ch.handle_add_athlete_shift_cancel(update, _ctx({"sport_type": "X", "age_group": "adults"})))
    assert state == ch.ConversationHandler.END
