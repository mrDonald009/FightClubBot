"""Старт сценария добавления и проверки доступа."""

import builtins
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import handlers.coach_handlers as ch
from tests.helpers import (
    ctx as _ctx,
    run_async as _run,
    update_with_message as _update_with_message,
)

pytestmark = pytest.mark.flow


def test_cancel_athlete_creation_clears_and_shows_menu(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(ch, "get_coach_main_menu", lambda: sentinel)
    update = _update_with_message("x")
    context = _ctx({"full_name": "X"})
    state = _run(ch.cancel_athlete_creation(update, context))
    assert state == ch.ConversationHandler.END
    assert context.user_data == {}
    assert update.message.calls[-1]["reply_markup"] is sentinel


def test_subscription_menu_interrupt(monkeypatch):
    cancel = AsyncMock(return_value=ch.ConversationHandler.END)
    monkeypatch.setattr(ch, "cancel_athlete_creation", cancel)
    state = _run(ch.add_athlete_subscription(_update_with_message(ch.MENU_BUTTONS[0]), _ctx({})))
    assert state == ch.ConversationHandler.END


def test_add_athlete_start_user_not_found(monkeypatch):
    class _S:
        def close(self):
            return None

    monkeypatch.setattr(ch, "Session", lambda: _S())
    monkeypatch.setattr(ch, "get_user_by_telegram_id", lambda *_a, **_k: None)
    update = _update_with_message("")
    state = _run(ch.add_athlete_start(update, _ctx({"old": 1})))
    assert state == ch.ConversationHandler.END


def test_add_athlete_start_wrong_role(monkeypatch):
    class _S:
        def close(self):
            return None

    monkeypatch.setattr(ch, "Session", lambda: _S())
    monkeypatch.setattr(ch, "get_user_by_telegram_id", lambda *_a, **_k: SimpleNamespace())
    monkeypatch.setattr(ch, "get_user_role", lambda *_a, **_k: "assistant")
    update = _update_with_message("")
    state = _run(ch.add_athlete_start(update, _ctx({})))
    assert state == ch.ConversationHandler.END


def test_add_athlete_start_admin_denied(monkeypatch):
    class _S:
        def close(self):
            return None

    monkeypatch.setattr(ch, "Session", lambda: _S())
    monkeypatch.setattr(ch, "get_user_by_telegram_id", lambda *_a, **_k: SimpleNamespace(telegram_id=4242))
    monkeypatch.setattr(ch, "get_user_role", lambda *_a, **_k: "admin")
    update = _update_with_message("")
    state = _run(ch.add_athlete_start(update, _ctx({})))
    assert state == ch.ConversationHandler.END


def test_add_athlete_start_coach_with_sport(monkeypatch):
    coach = SimpleNamespace(id=99, sport_type_rel=SimpleNamespace(name="Тайский Бокс"), sport_type=None)

    class _S:
        def close(self):
            return None

    monkeypatch.setattr(ch, "Session", lambda: _S())
    monkeypatch.setattr(ch, "get_user_by_telegram_id", lambda *_a, **_k: coach)
    monkeypatch.setattr(ch, "get_user_role", lambda *_a, **_k: "coach")
    real_isinstance = builtins.isinstance
    monkeypatch.setattr(
        builtins,
        "isinstance",
        lambda obj, cls: True if obj is coach and cls is ch.Coach else real_isinstance(obj, cls),
    )
    context = _ctx({})
    state = _run(ch.add_athlete_start(_update_with_message(""), context))
    assert state == ch.ATHLETE_FULL_NAME
    assert context.user_data["coach_id"] == 99


def test_add_athlete_start_coach_without_sport_in_profile(monkeypatch):
    coach = SimpleNamespace(id=5, sport_type_rel=None, sport_type=None)

    class _S:
        def close(self):
            return None

    monkeypatch.setattr(ch, "Session", lambda: _S())
    monkeypatch.setattr(ch, "get_user_by_telegram_id", lambda *_a, **_k: coach)
    monkeypatch.setattr(ch, "get_user_role", lambda *_a, **_k: "coach")
    real_isinstance = builtins.isinstance
    monkeypatch.setattr(
        builtins,
        "isinstance",
        lambda obj, cls: True if obj is coach and cls is ch.Coach else real_isinstance(obj, cls),
    )
    state = _run(ch.add_athlete_start(_update_with_message(""), _ctx({})))
    assert state == ch.ConversationHandler.END
