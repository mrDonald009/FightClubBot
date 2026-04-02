"""Проверки сценария добавления спортсмена (включая ветки с массовой заморозкой).

Матрица сценариев (текущий функционал):
- Старт: пользователь не в БД; роль не coach/admin; тренер без вида спорта (ветка else);
  тренер с видом спорта → ФИО.
- ФИО: прерывание кнопкой меню; ввод телефона вместо ФИО; латиница/невалид; успех → телефон.
- Телефон: меню; «как ФИО»; неверный формат; дубликат телефона; успех → дата рождения.
- Дата рождения: меню; формат; месяц/день вне диапазона; несуществующая дата; будущее; год < 1900;
  нормализация / и -; успех → медицина.
- Медицина: меню; «нет» / произвольный текст → возрастная группа.
- Возраст: меню; неверная кнопка; Детская/Взрослая → абонемент.
- Абонемент: меню; неверная кнопка; календарь / нет расписания.
- Календарь: навигация (сессия потеряна, битый callback, успех); выбор дня (те же + нет schedule);
  ignore; legacy select_training_date_; массовая заморозка / confirm / cancel (уже в тестах ниже).
- Финализация: месячный/разовый, ошибка БД (опционально).
"""
import asyncio
import builtins
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

sys.path.append(str(Path(__file__).resolve().parents[1]))

import handlers.coach_handlers as ch


def _run(coro):
    return asyncio.run(coro)


class _FakeSentMessage:
    async def delete(self):
        return None


class _FakeMessage:
    def __init__(self, text=None):
        self.text = text
        self.calls = []
        self.deleted = False

    async def reply_text(self, text, **kwargs):
        self.calls.append({"text": text, **kwargs})
        return _FakeSentMessage()

    async def delete(self):
        self.deleted = True


class _FakeCallbackQuery:
    def __init__(self, data=""):
        self.data = data
        self.message = _FakeMessage()
        self.answers = 0
        self.edits = []
        self.edit_reply_markup_calls = []

    async def answer(self, *args, **kwargs):
        self.answers += 1

    async def edit_message_text(self, text, **kwargs):
        self.edits.append({"text": text, **kwargs})

    async def edit_message_reply_markup(self, **kwargs):
        self.edit_reply_markup_calls.append(kwargs)


def _ctx(user_data=None):
    return SimpleNamespace(user_data=user_data or {})


def _update_with_message(text, user_id=777):
    msg = _FakeMessage(text=text)
    return SimpleNamespace(message=msg, effective_user=SimpleNamespace(id=user_id))


def _update_with_query(data, user_id=777):
    q = _FakeCallbackQuery(data=data)
    return SimpleNamespace(callback_query=q, effective_user=SimpleNamespace(id=user_id))


def test_medical_step_goes_to_age_group():
    update = _update_with_message("нет")
    context = _ctx({})

    state = _run(ch.add_athlete_medical(update, context))

    assert state == ch.ATHLETE_AGE_GROUP
    assert context.user_data["medical_info"] == "Нет противопоказаний"
    assert update.message.calls
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
    monkeypatch.setattr(ch, "create_add_athlete_training_calendar", lambda *_args, **_kwargs: sentinel_kb)

    state = _run(ch.add_athlete_subscription(update, context))

    assert state == ch.ATHLETE_TRAINING_DATE
    assert context.user_data["subscription_type"] == "single"
    assert update.message.calls[-1]["reply_markup"] is sentinel_kb
    assert "Выберите <b>первую дату тренировки</b>" in update.message.calls[-1]["text"]


def test_subscription_invalid_value_stays_on_same_step():
    update = _update_with_message("Годовой")
    context = _ctx({})

    state = _run(ch.add_athlete_subscription(update, context))

    assert state == ch.ATHLETE_SUBSCRIPTION
    assert "Выберите тип абонемента кнопкой" in update.message.calls[-1]["text"]


def test_finalize_detects_global_freeze_and_requests_confirm(monkeypatch):
    class _FakeQuery:
        def filter(self, *args, **kwargs):
            return self

        def order_by(self, *args, **kwargs):
            return self

        def first(self):
            return SimpleNamespace(
                start_date=datetime(2026, 4, 1, 0, 0),
                end_date=datetime(2026, 4, 10, 23, 59),
            )

    class _FakeSession:
        def query(self, _model):
            return _FakeQuery()

        def close(self):
            return None

    monkeypatch.setattr(ch, "Session", lambda: _FakeSession())
    monkeypatch.setattr("database.db_utils._find_nearest_training_date", lambda *_a, **_k: datetime(2026, 4, 3, 20, 0))
    monkeypatch.setattr("database.db_utils.is_training_in_global_freeze", lambda *_a, **_k: True)
    monkeypatch.setattr(
        ch,
        "_find_next_non_frozen_training_date",
        lambda *_a, **_k: datetime(2026, 4, 12, 20, 0),
    )

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
    assert update.callback_query.edits
    assert "попадает в период массовой заморозки" in update.callback_query.edits[-1]["text"]


def test_shift_confirm_without_pending_returns_error():
    update = _update_with_query("addath_shift_confirm")
    context = _ctx({})

    state = _run(ch.handle_add_athlete_shift_confirm(update, context))

    assert state == ch.ATHLETE_TRAINING_DATE
    assert update.callback_query.edits
    assert "Данные сессии утеряны" in update.callback_query.edits[-1]["text"]


def test_shift_confirm_with_pending_calls_finalize(monkeypatch):
    calls = {}

    async def _fake_finalize(query, context, coach_selected_date, *, skip_freeze_confirm=False):
        calls["date"] = coach_selected_date
        calls["skip"] = skip_freeze_confirm
        return ch.ConversationHandler.END

    monkeypatch.setattr(ch, "_finalize_add_athlete_from_selected_date", _fake_finalize)

    update = _update_with_query("addath_shift_confirm")
    context = _ctx({"pending_shifted_start_date": "2026-04-12T20:00:00"})

    state = _run(ch.handle_add_athlete_shift_confirm(update, context))

    assert state == ch.ConversationHandler.END
    assert calls["skip"] is True
    assert calls["date"] == datetime(2026, 4, 12, 20, 0)
    assert "pending_shifted_start_date" not in context.user_data


def test_shift_confirm_uses_callback_payload_without_user_data(monkeypatch):
    """Как в проде при пустом user_data: дата только в callback_data кнопки."""
    calls = {}

    async def _fake_finalize(query, context, coach_selected_date, *, skip_freeze_confirm=False):
        calls["date"] = coach_selected_date
        calls["skip"] = skip_freeze_confirm
        return ch.ConversationHandler.END

    monkeypatch.setattr(ch, "_finalize_add_athlete_from_selected_date", _fake_finalize)

    update = _update_with_query("addath_shift_confirm_202604121430")
    context = _ctx({})

    state = _run(ch.handle_add_athlete_shift_confirm(update, context))

    assert state == ch.ConversationHandler.END
    assert calls["date"] == datetime(2026, 4, 12, 14, 30)
    assert calls["skip"] is True


def test_shift_cancel_returns_to_calendar(monkeypatch):
    sentinel_kb = object()
    monkeypatch.setattr(ch, "create_add_athlete_training_calendar", lambda *_a, **_k: sentinel_kb)

    update = _update_with_query("addath_shift_cancel")
    context = _ctx({"pending_shifted_start_date": "2026-04-12T20:00:00", "sport_type": "Тайский Бокс", "age_group": "adults"})

    state = _run(ch.handle_add_athlete_shift_cancel(update, context))

    assert state == ch.ATHLETE_TRAINING_DATE
    assert "pending_shifted_start_date" not in context.user_data
    assert update.callback_query.edits
    assert update.callback_query.edits[-1]["reply_markup"] is sentinel_kb
    assert "Выберите <b>первую дату тренировки</b>" in update.callback_query.edits[-1]["text"]


def test_birth_date_rejects_invalid_format():
    update = _update_with_message("31.13.20ab")
    context = _ctx({})

    state = _run(ch.add_athlete_birth_date(update, context))

    assert state == ch.ATHLETE_BIRTH_DATE
    assert "Неверный формат даты" in update.message.calls[-1]["text"]


def test_birth_date_rejects_future_date():
    future = datetime.now().replace(year=datetime.now().year + 1).strftime("%d.%m.%Y")
    update = _update_with_message(future)
    context = _ctx({})

    state = _run(ch.add_athlete_birth_date(update, context))

    assert state == ch.ATHLETE_BIRTH_DATE
    assert "не может быть в будущем" in update.message.calls[-1]["text"]


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
            dup = SimpleNamespace(full_name="Уже Существует")
            return _FakeQuery(dup)

        def close(self):
            return None

    monkeypatch.setattr(ch, "Session", lambda: _FakeSession())

    update = _update_with_message("925-123-45-67")
    context = _ctx({"full_name": "Новый Спортсмен"})

    state = _run(ch.add_athlete_phone(update, context))

    assert state == ch.ATHLETE_PHONE
    assert "уже существует" in update.message.calls[-1]["text"]


def test_subscription_step_without_schedule_finishes_with_error(monkeypatch):
    update = _update_with_message("Месячный")
    context = _ctx({"sport_type": "Тайский Бокс", "age_group": "adults"})

    monkeypatch.setattr(ch, "create_add_athlete_training_calendar", lambda *_args, **_kwargs: None)

    state = _run(ch.add_athlete_subscription(update, context))

    assert state == ch.ConversationHandler.END
    assert "Нет доступных дат тренировок" in update.message.calls[-1]["text"]


def test_finalize_monthly_uses_12th_training_end(monkeypatch):
    calls = {"end_monthly": None}

    monkeypatch.setattr("database.db_utils._find_nearest_training_date", lambda *_a, **_k: datetime(2026, 4, 12, 20, 0))
    monkeypatch.setattr("database.db_utils.is_training_in_global_freeze", lambda *_a, **_k: False)
    monkeypatch.setattr("database.db_utils._calculate_12th_training_date", lambda *_a, **_k: datetime(2026, 5, 31, 21, 30))

    class _FakeSession:
        def __init__(self):
            self.training_obj = None

        def query(self, model):
            class _Q:
                def filter_by(self, **_kwargs):
                    return self

                def first(self):
                    return None if model.__name__ == "Training" else None
            return _Q()

        def add(self, obj):
            self.training_obj = obj

        def flush(self):
            return None

        def commit(self):
            return None

        def close(self):
            return None

    monkeypatch.setattr(ch, "Session", lambda: _FakeSession())
    monkeypatch.setattr(ch, "create_athlete", lambda **_kwargs: SimpleNamespace(id=1, full_name="Тест", phone="+7-1", birth_date=None, sport_type="Тайский Бокс", age_group="adults", medical_info="нет", created_by=1))

    def _fake_create_subscription(**_kwargs):
        sub = SimpleNamespace(id=1, start_date=None, end_date=None, is_active=False, trainings_remaining=12)
        calls["sub"] = sub
        return sub

    monkeypatch.setattr("database.db_utils.create_subscription", _fake_create_subscription)
    monkeypatch.setattr("database.db_utils._create_and_deduct_scheduled_trainings", lambda *_a, **_k: None)
    monkeypatch.setattr("database.db_utils.sync_subscription_trainings_remaining", lambda *_a, **_k: None)

    update = _update_with_query("addath_date_2026_4_12")
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

    state = _run(ch._finalize_add_athlete_from_selected_date(update.callback_query, context, datetime(2026, 4, 12)))

    assert state == ch.ConversationHandler.END
    assert calls["sub"].end_date == datetime(2026, 5, 31, 21, 30)


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
    monkeypatch.setattr(ch, "create_athlete", lambda **_kwargs: SimpleNamespace(id=1, full_name="Тест", phone="+7-1", birth_date=None, sport_type="Тайский Бокс", age_group="adults", medical_info="нет", created_by=1))

    sub_holder = {}

    def _fake_create_subscription(**_kwargs):
        sub = SimpleNamespace(id=1, start_date=None, end_date=None, is_active=False, trainings_remaining=1)
        sub_holder["sub"] = sub
        return sub

    monkeypatch.setattr("database.db_utils.create_subscription", _fake_create_subscription)
    monkeypatch.setattr("database.db_utils.sync_subscription_trainings_remaining", lambda *_a, **_k: None)

    update = _update_with_query("addath_date_2026_4_12")
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

    state = _run(ch._finalize_add_athlete_from_selected_date(update.callback_query, context, datetime(2026, 4, 12)))

    assert state == ch.ConversationHandler.END
    assert sub_holder["sub"].end_date == datetime(2026, 4, 12, 21, 30)


def test_cancel_athlete_creation_clears_and_shows_menu(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(ch, "get_coach_main_menu", lambda: sentinel)

    update = _update_with_message("x")
    context = _ctx({"full_name": "X"})

    state = _run(ch.cancel_athlete_creation(update, context))

    assert state == ch.ConversationHandler.END
    assert context.user_data == {}
    assert update.message.calls[-1]["reply_markup"] is sentinel
    assert "отменено" in update.message.calls[-1]["text"]


def test_full_name_menu_interrupt_calls_cancel(monkeypatch):
    cancel = AsyncMock(return_value=ch.ConversationHandler.END)
    monkeypatch.setattr(ch, "cancel_athlete_creation", cancel)

    update = _update_with_message(ch.MENU_BUTTONS[0])
    context = _ctx({})

    state = _run(ch.add_athlete_full_name(update, context))

    assert state == ch.ConversationHandler.END
    cancel.assert_awaited_once()


def test_full_name_rejects_phone_like_input():
    update = _update_with_message("925-123-45-67")
    context = _ctx({})

    state = _run(ch.add_athlete_full_name(update, context))

    assert state == ch.ATHLETE_FULL_NAME
    assert "номер телефона" in update.message.calls[-1]["text"]


def test_full_name_rejects_latin():
    update = _update_with_message("Ivanov Ivan")
    context = _ctx({})

    state = _run(ch.add_athlete_full_name(update, context))

    assert state == ch.ATHLETE_FULL_NAME
    assert "латиница" in update.message.calls[-1]["text"].lower()


def test_full_name_accepts_valid_cyrillic():
    update = _update_with_message("Иванов Иван Петрович")
    context = _ctx({})

    state = _run(ch.add_athlete_full_name(update, context))

    assert state == ch.ATHLETE_PHONE
    assert context.user_data["full_name"] == "Иванов Иван Петрович"
    assert "телефона" in update.message.calls[-1]["text"].lower()


def test_phone_rejects_name_like_without_proper_format():
    update = _update_with_message("Иванов Иван")
    context = _ctx({"full_name": "Иванов Иван"})

    state = _run(ch.add_athlete_phone(update, context))

    assert state == ch.ATHLETE_PHONE
    assert "ФИО" in update.message.calls[-1]["text"]


def test_phone_rejects_wrong_format(monkeypatch):
    monkeypatch.setattr(ch, "Session", lambda: (_ for _ in ()).throw(AssertionError("DB should not open")))

    update = _update_with_message("12345")
    context = _ctx({"full_name": "Иванов Иван"})

    state = _run(ch.add_athlete_phone(update, context))

    assert state == ch.ATHLETE_PHONE
    assert "формат" in update.message.calls[-1]["text"].lower()


def test_birth_date_invalid_month():
    update = _update_with_message("15.13.2010")
    context = _ctx({})

    state = _run(ch.add_athlete_birth_date(update, context))

    assert state == ch.ATHLETE_BIRTH_DATE
    assert "месяц" in update.message.calls[-1]["text"].lower()


def test_birth_date_impossible_calendar_day():
    update = _update_with_message("31.02.2010")
    context = _ctx({})

    state = _run(ch.add_athlete_birth_date(update, context))

    assert state == ch.ATHLETE_BIRTH_DATE
    assert "не существует" in update.message.calls[-1]["text"].lower()


def test_birth_date_year_before_1900():
    update = _update_with_message("01.01.1899")
    context = _ctx({})

    state = _run(ch.add_athlete_birth_date(update, context))

    assert state == ch.ATHLETE_BIRTH_DATE
    assert "ранний" in update.message.calls[-1]["text"].lower()


def test_birth_date_accepts_slash_normalized():
    update = _update_with_message("15/06/2010")
    context = _ctx({})

    state = _run(ch.add_athlete_birth_date(update, context))

    assert state == ch.ATHLETE_MEDICAL
    assert context.user_data["birth_date"].year == 2010
    assert context.user_data["birth_date"].month == 6


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


def test_subscription_menu_interrupt(monkeypatch):
    cancel = AsyncMock(return_value=ch.ConversationHandler.END)
    monkeypatch.setattr(ch, "cancel_athlete_creation", cancel)

    update = _update_with_message(ch.MENU_BUTTONS[0])
    context = _ctx({})

    state = _run(ch.add_athlete_subscription(update, context))

    assert state == ch.ConversationHandler.END
    cancel.assert_awaited_once()


def test_calendar_nav_session_expired():
    update = _update_with_query("addath_cal_2026_5")
    context = _ctx({})

    state = _run(ch.handle_add_athlete_calendar_nav(update, context))

    assert state == ch.ConversationHandler.END
    assert "завершена" in update.callback_query.edits[-1]["text"]


def test_calendar_nav_malformed_callback():
    update = _update_with_query("addath_cal_2026")
    context = _ctx({"subscription_type": "single", "sport_type": "Тайский Бокс", "age_group": "adults"})

    state = _run(ch.handle_add_athlete_calendar_nav(update, context))

    assert state == ch.ATHLETE_TRAINING_DATE
    assert update.callback_query.answers >= 1


def test_calendar_nav_updates_markup(monkeypatch):
    kb = object()
    monkeypatch.setattr(ch, "create_add_athlete_training_calendar", lambda *_a, **_k: kb)

    update = _update_with_query("addath_cal_2026_5")
    context = _ctx({"subscription_type": "single", "sport_type": "Тайский Бокс", "age_group": "adults"})

    state = _run(ch.handle_add_athlete_calendar_nav(update, context))

    assert state == ch.ATHLETE_TRAINING_DATE
    assert update.callback_query.edit_reply_markup_calls
    assert update.callback_query.edit_reply_markup_calls[-1]["reply_markup"] is kb


def test_calendar_date_pick_session_expired():
    update = _update_with_query("addath_date_2026_4_10")
    context = _ctx({})

    state = _run(ch.handle_add_athlete_calendar_date_pick(update, context))

    assert state == ch.ConversationHandler.END


def test_calendar_date_pick_malformed_parts():
    update = _update_with_query("addath_date_2026_4")
    context = _ctx({"subscription_type": "single", "sport_type": "Тайский Бокс", "age_group": "adults"})

    state = _run(ch.handle_add_athlete_calendar_date_pick(update, context))

    assert state == ch.ATHLETE_TRAINING_DATE


def test_calendar_date_pick_no_schedule_ends():
    update = _update_with_query("addath_date_2026_4_10")
    context = _ctx(
        {
            "subscription_type": "single",
            "sport_type": "НетТакогоСпорта",
            "age_group": "adults",
        }
    )

    state = _run(ch.handle_add_athlete_calendar_date_pick(update, context))

    assert state == ch.ConversationHandler.END
    assert "Расписание" in update.callback_query.edits[-1]["text"]


def test_calendar_ignore_stays_on_training_date():
    update = _update_with_query("addath_ignore")
    context = _ctx({"subscription_type": "single"})

    state = _run(ch.handle_add_athlete_calendar_ignore(update, context))

    assert state == ch.ATHLETE_TRAINING_DATE


def test_training_date_selection_no_subscription():
    update = _update_with_query("select_training_date_2026-04-12-20-0")
    context = _ctx({})

    state = _run(ch.handle_training_date_selection(update, context))

    assert state == ch.ConversationHandler.END


def test_training_date_selection_bad_payload():
    update = _update_with_query("select_training_date_broken")
    context = _ctx({"subscription_type": "single"})

    state = _run(ch.handle_training_date_selection(update, context))

    assert state == ch.ConversationHandler.END
    assert "Ошибка" in update.callback_query.edits[-1]["text"]


def test_training_date_selection_delegates_to_finalize(monkeypatch):
    calls = []

    async def _fake_finalize(query, context, dt, *, skip_freeze_confirm=False):
        calls.append((dt, skip_freeze_confirm))
        return ch.ConversationHandler.END

    monkeypatch.setattr(ch, "_finalize_add_athlete_from_selected_date", _fake_finalize)

    update = _update_with_query("select_training_date_2026-04-12-20-30")
    context = _ctx({"subscription_type": "single"})

    state = _run(ch.handle_training_date_selection(update, context))

    assert state == ch.ConversationHandler.END
    assert calls[0][0] == datetime(2026, 4, 12, 20, 30)


def test_shift_cancel_when_calendar_unavailable(monkeypatch):
    monkeypatch.setattr(ch, "create_add_athlete_training_calendar", lambda *_a, **_k: None)

    update = _update_with_query("addath_shift_cancel")
    context = _ctx({"sport_type": "X", "age_group": "adults"})

    state = _run(ch.handle_add_athlete_shift_cancel(update, context))

    assert state == ch.ConversationHandler.END
    assert "недоступен" in update.callback_query.edits[-1]["text"].lower()


def test_add_athlete_start_user_not_found(monkeypatch):
    class _S:
        def close(self):
            return None

    monkeypatch.setattr(ch, "Session", lambda: _S())
    monkeypatch.setattr(ch, "get_user_by_telegram_id", lambda *_a, **_k: None)

    update = _update_with_message("")
    context = _ctx({"old": 1})

    state = _run(ch.add_athlete_start(update, context))

    assert state == ch.ConversationHandler.END
    assert "не найден" in update.message.calls[-1]["text"].lower()


def test_add_athlete_start_wrong_role(monkeypatch):
    u = SimpleNamespace()

    class _S:
        def close(self):
            return None

    monkeypatch.setattr(ch, "Session", lambda: _S())
    monkeypatch.setattr(ch, "get_user_by_telegram_id", lambda *_a, **_k: u)
    monkeypatch.setattr(ch, "get_user_role", lambda *_a, **_k: "assistant")

    update = _update_with_message("")
    context = _ctx({})

    state = _run(ch.add_athlete_start(update, context))

    assert state == ch.ConversationHandler.END
    assert "нет прав" in update.message.calls[-1]["text"].lower()


def test_add_athlete_start_admin_gets_no_sport_message(monkeypatch):
    admin = SimpleNamespace()

    class _S:
        def close(self):
            return None

    monkeypatch.setattr(ch, "Session", lambda: _S())
    monkeypatch.setattr(ch, "get_user_by_telegram_id", lambda *_a, **_k: admin)
    monkeypatch.setattr(ch, "get_user_role", lambda *_a, **_k: "admin")

    real_isinstance = builtins.isinstance

    def _isinstance(obj, cls):
        if obj is admin and cls is ch.Coach:
            return False
        return real_isinstance(obj, cls)

    monkeypatch.setattr(builtins, "isinstance", _isinstance)

    update = _update_with_message("")
    context = _ctx({})

    state = _run(ch.add_athlete_start(update, context))

    assert state == ch.ConversationHandler.END
    assert "специализац" in update.message.calls[-1]["text"].lower()


def test_add_athlete_start_coach_with_sport(monkeypatch):
    coach = SimpleNamespace(
        id=99,
        sport_type_rel=SimpleNamespace(name="Тайский Бокс"),
        sport_type=None,
    )

    class _S:
        def close(self):
            return None

    monkeypatch.setattr(ch, "Session", lambda: _S())
    monkeypatch.setattr(ch, "get_user_by_telegram_id", lambda *_a, **_k: coach)
    monkeypatch.setattr(ch, "get_user_role", lambda *_a, **_k: "coach")

    real_isinstance = builtins.isinstance

    def _isinstance(obj, cls):
        if obj is coach and cls is ch.Coach:
            return True
        return real_isinstance(obj, cls)

    monkeypatch.setattr(builtins, "isinstance", _isinstance)

    update = _update_with_message("")
    context = _ctx({})

    state = _run(ch.add_athlete_start(update, context))

    assert state == ch.ATHLETE_FULL_NAME
    assert context.user_data["sport_type"] == "Тайский Бокс"
    assert context.user_data["coach_id"] == 99
    assert "ФИО" in update.message.calls[-1]["text"]


def test_finalize_on_create_athlete_error_shows_message(monkeypatch):
    monkeypatch.setattr("database.db_utils._find_nearest_training_date", lambda *_a, **_k: datetime(2026, 4, 12, 20, 0))
    monkeypatch.setattr("database.db_utils.is_training_in_global_freeze", lambda *_a, **_k: False)

    class _FakeSession:
        def query(self, model):
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

    update = _update_with_query("addath_date_2026_4_12")
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

    state = _run(ch._finalize_add_athlete_from_selected_date(update.callback_query, context, datetime(2026, 4, 12)))

    assert state == ch.ConversationHandler.END
    assert "Ошибка при добавлении" in update.callback_query.edits[-1]["text"]
