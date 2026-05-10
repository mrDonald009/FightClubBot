"""Базовые шаги сценария добавления спортсмена."""

import pytest

import handlers.coach_handlers as ch
from tests.helpers import (
    ctx as _ctx,
    run_async as _run,
    update_with_message as _update_with_message,
)

pytestmark = pytest.mark.flow


def test_medical_step_goes_to_age_group():
    update = _update_with_message("нет")
    context = _ctx({})
    state = _run(ch.add_athlete_medical(update, context))
    assert state == ch.ATHLETE_AGE_GROUP
    assert context.user_data["medical_info"] == "Нет противопоказаний"
    assert "Выберите возрастную группу" in update.message.calls[-1]["text"]


def test_age_group_step_goes_to_subscription():
    update = _update_with_message("Детская")
    context = _ctx({})
    state = _run(ch.add_athlete_age_group(update, context))
    assert state == ch.ATHLETE_SUBSCRIPTION
    assert context.user_data["age_group"] == "children"
    assert "Выберите тип абонемента" in update.message.calls[-1]["text"]


def test_subscription_step_shows_calendar(monkeypatch):
    update = _update_with_message("Разовый")
    context = _ctx({"sport_type": "Тайский Бокс", "age_group": "adults"})
    sentinel_kb = object()
    monkeypatch.setattr(ch, "create_add_athlete_training_calendar", lambda *_a, **_k: sentinel_kb)
    state = _run(ch.add_athlete_subscription(update, context))
    assert state == ch.ATHLETE_TRAINING_DATE
    assert context.user_data["subscription_type"] == "single"
    assert update.message.calls[-1]["reply_markup"] is sentinel_kb


def test_subscription_invalid_value_stays_on_same_step():
    update = _update_with_message("Годовой")
    context = _ctx({})
    state = _run(ch.add_athlete_subscription(update, context))
    assert state == ch.ATHLETE_SUBSCRIPTION
    assert "Выберите тип абонемента кнопкой" in update.message.calls[-1]["text"]


def test_subscription_step_without_schedule_finishes_with_error(monkeypatch):
    update = _update_with_message("Месячный")
    context = _ctx({"sport_type": "Тайский Бокс", "age_group": "adults"})
    monkeypatch.setattr(ch, "create_add_athlete_training_calendar", lambda *_a, **_k: None)
    state = _run(ch.add_athlete_subscription(update, context))
    assert state == ch.ConversationHandler.END
    assert "Нет доступных дат тренировок" in update.message.calls[-1]["text"]


def test_medical_custom_text_saved():
    update = _update_with_message("Аллергия на орехи")
    context = _ctx({})
    state = _run(ch.add_athlete_medical(update, context))
    assert state == ch.ATHLETE_AGE_GROUP
    assert context.user_data["medical_info"] == "Аллергия на орехи"


def test_age_group_invalid_stays():
    update = _update_with_message("Подростковая")
    context = _ctx({})
    state = _run(ch.add_athlete_age_group(update, context))
    assert state == ch.ATHLETE_AGE_GROUP
    assert "кнопкой" in update.message.calls[-1]["text"].lower()


def test_age_group_adults():
    update = _update_with_message("Взрослая")
    context = _ctx({})
    state = _run(ch.add_athlete_age_group(update, context))
    assert state == ch.ATHLETE_SUBSCRIPTION
    assert context.user_data["age_group"] == "adults"
