"""Валидации полей ФИО/телефон/дата рождения в сценарии."""

from datetime import datetime
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


def test_birth_date_rejects_invalid_format():
    update = _update_with_message("31.13.20ab")
    state = _run(ch.add_athlete_birth_date(update, _ctx({})))
    assert state == ch.ATHLETE_BIRTH_DATE
    assert "Неверный формат даты" in update.message.calls[-1]["text"]


def test_birth_date_rejects_future_date():
    future = datetime.now().replace(year=datetime.now().year + 1).strftime("%d.%m.%Y")
    update = _update_with_message(future)
    state = _run(ch.add_athlete_birth_date(update, _ctx({})))
    assert state == ch.ATHLETE_BIRTH_DATE
    assert "не может быть в будущем" in update.message.calls[-1]["text"]


def test_full_name_menu_interrupt_calls_cancel(monkeypatch):
    cancel = AsyncMock(return_value=ch.ConversationHandler.END)
    monkeypatch.setattr(ch, "cancel_athlete_creation", cancel)
    state = _run(ch.add_athlete_full_name(_update_with_message(ch.MENU_BUTTONS[0]), _ctx({})))
    assert state == ch.ConversationHandler.END
    cancel.assert_awaited_once()


def test_full_name_rejects_phone_like_input():
    update = _update_with_message("925-123-45-67")
    state = _run(ch.add_athlete_full_name(update, _ctx({})))
    assert state == ch.ATHLETE_FULL_NAME
    assert "номер телефона" in update.message.calls[-1]["text"]


def test_full_name_rejects_latin():
    update = _update_with_message("Ivanov Ivan")
    state = _run(ch.add_athlete_full_name(update, _ctx({})))
    assert state == ch.ATHLETE_FULL_NAME
    assert "латиница" in update.message.calls[-1]["text"].lower()


def test_full_name_accepts_valid_cyrillic():
    update = _update_with_message("Иванов Иван Петрович")
    context = _ctx({})
    state = _run(ch.add_athlete_full_name(update, context))
    assert state == ch.ATHLETE_PHONE
    assert context.user_data["full_name"] == "Иванов Иван Петрович"


def test_phone_rejects_name_like_without_proper_format():
    update = _update_with_message("Иванов Иван")
    state = _run(ch.add_athlete_phone(update, _ctx({"full_name": "Иванов Иван"})))
    assert state == ch.ATHLETE_PHONE
    assert "ФИО" in update.message.calls[-1]["text"]


def test_phone_rejects_wrong_format(monkeypatch):
    monkeypatch.setattr(ch, "Session", lambda: (_ for _ in ()).throw(AssertionError("DB should not open")))
    update = _update_with_message("12345")
    state = _run(ch.add_athlete_phone(update, _ctx({"full_name": "Иванов Иван"})))
    assert state == ch.ATHLETE_PHONE
    assert "формат" in update.message.calls[-1]["text"].lower()


def test_phone_rejects_duplicate_phone(monkeypatch):
    class _FakeQuery:
        def __init__(self, result):
            self._result = result

        def filter_by(self, **_kwargs):
            return self

        def first(self):
            return self._result

    class _FakeSession:
        def query(self, _model):
            return _FakeQuery(SimpleNamespace(full_name="Уже Существует"))

        def close(self):
            return None

    monkeypatch.setattr(ch, "Session", lambda: _FakeSession())
    update = _update_with_message("925-123-45-67")
    state = _run(ch.add_athlete_phone(update, _ctx({"full_name": "Новый Спортсмен"})))
    assert state == ch.ATHLETE_PHONE
    assert "уже существует" in update.message.calls[-1]["text"]


def test_birth_date_invalid_month():
    update = _update_with_message("15.13.2010")
    state = _run(ch.add_athlete_birth_date(update, _ctx({})))
    assert state == ch.ATHLETE_BIRTH_DATE
    assert "месяц" in update.message.calls[-1]["text"].lower()


def test_birth_date_impossible_calendar_day():
    update = _update_with_message("31.02.2010")
    state = _run(ch.add_athlete_birth_date(update, _ctx({})))
    assert state == ch.ATHLETE_BIRTH_DATE
    assert "не существует" in update.message.calls[-1]["text"].lower()


def test_birth_date_year_before_1900():
    update = _update_with_message("01.01.1899")
    state = _run(ch.add_athlete_birth_date(update, _ctx({})))
    assert state == ch.ATHLETE_BIRTH_DATE
    assert "ранний" in update.message.calls[-1]["text"].lower()


def test_birth_date_accepts_slash_normalized():
    update = _update_with_message("15/06/2010")
    context = _ctx({})
    state = _run(ch.add_athlete_birth_date(update, context))
    assert state == ch.ATHLETE_MEDICAL
    assert context.user_data["birth_date"].year == 2010
