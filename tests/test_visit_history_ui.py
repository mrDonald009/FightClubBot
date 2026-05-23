"""UI-хелперы экрана «История посещений»."""
from datetime import datetime, timedelta

from database.models import Subscription, Training
from handlers.card_handlers import (
    _format_visit_history_compact_list,
    _format_visit_history_slot_line,
    _parse_visits_athlete_id,
    _render_visit_history_message,
    _visit_training_kind_ru,
)
from utils.time_utils import now_moscow


def test_visit_training_kind_ru_group_monthly():
    training = Training(
        sport_type="MMA",
        age_group="children",
        training_format=None,
        training_date=datetime(2026, 5, 18, 17, 0),
    )
    sub = Subscription(subscription_type="monthly")
    assert _visit_training_kind_ru(training, subscription=sub) == "Групповая"


def test_format_visit_history_slot_line_group():
    training = Training(
        sport_type="MMA",
        age_group="children",
        training_format=None,
        training_date=datetime(2026, 5, 18, 17, 0),
    )
    line = _format_visit_history_slot_line(
        training, None, subscription=Subscription(subscription_type="monthly")
    )
    assert line == "❌ 17:00 · MMA | Групповая\n"


def test_parse_visits_athlete_id():
    assert _parse_visits_athlete_id("visits_42") == 42
    assert _parse_visits_athlete_id("visits_42_30_grp") == 42


def test_format_visit_history_compact_list_no_indent():
    entries = [
        (datetime(2026, 5, 20, 17, 0), "❌ 17:00 · MMA | Групповая\n", False),
        (datetime(2026, 5, 20, 9, 30), "✅ 09:30 · MMA | Индивидуальная\n", True),
        (datetime(2026, 5, 18, 17, 0), "❌ 17:00 · MMA | Групповая\n", False),
    ]
    text = _format_visit_history_compact_list(entries)
    assert "20.05  ✅ 09:30" in text
    assert "20.05  ❌ 17:00" in text
    assert text.index("18.05") < text.index("20.05  ✅")
    assert "      " not in text


def test_render_visit_history_message_minimal():
    entries = [
        (datetime(2026, 5, 20, 9, 30), "✅ 09:30 · MMA | Индивидуальная\n", True),
    ]
    text = _render_visit_history_message("Иван Петров", entries, total_matching=1)
    assert "История посещений" in text
    assert "Тренировки:" in text
    assert "7 дн" not in text
    assert "Групп" not in text


def test_render_visit_history_truncation_note():
    now = now_moscow()
    all_entries = [
        (now - timedelta(days=i), f"line{i}\n", False) for i in range(30)
    ]
    display = all_entries[-5:]
    text = _render_visit_history_message("Иван", display, total_matching=30)
    assert "Показаны последние 5 из 30" in text


def test_render_visit_history_empty():
    text = _render_visit_history_message("Иван", [], total_matching=0)
    assert "Нет записей посещений" in text
