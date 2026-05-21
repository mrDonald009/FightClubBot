"""UI-хелперы экрана «История посещений»."""
from datetime import datetime, timedelta

from database.models import Attendance, Subscription, Training
from handlers.card_handlers import (
    _format_visit_history_slot_line,
    _parse_visits_callback,
    _render_visit_history_message,
    _visit_history_entries_for_display,
    _visit_history_entries_for_stats,
    _visit_history_period_stats,
    _visits_filter_callback,
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


def test_visit_training_kind_ru_single():
    training = Training(
        sport_type="MMA",
        age_group="children",
        training_format=None,
        training_date=datetime(2026, 5, 18, 17, 0),
    )
    sub = Subscription(subscription_type="single")
    assert _visit_training_kind_ru(training, subscription=sub) == "Разовая"


def test_visit_training_kind_ru_individual_slot():
    training = Training(
        sport_type="MMA",
        training_format="individual",
        training_date=datetime(2026, 5, 20, 9, 30),
    )
    assert _visit_training_kind_ru(training) == "Индивидуальная"


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


def test_format_visit_history_slot_line_present():
    training = Training(
        sport_type="MMA",
        training_format="individual",
        training_date=datetime(2026, 5, 20, 9, 30),
    )
    att = Attendance(attended=True)
    line = _format_visit_history_slot_line(training, att)
    assert line == "✅ 09:30 · MMA | Индивидуальная\n"


def test_parse_visits_callback_defaults_and_filtered():
    assert _parse_visits_callback("visits_42") == (42, 120, "all")
    assert _parse_visits_callback("visits_42_30_grp") == (42, 30, "grp")
    assert _parse_visits_callback("visits_42_999_bad") == (42, 120, "all")


def test_visits_filter_callback_format():
    assert _visits_filter_callback(5, 30, "ind") == "visits_5_30_ind"


def test_visit_history_entries_filter_by_kind_and_period():
    now = datetime(2026, 5, 22, 12, 0)
    entries = [
        (now - timedelta(days=2), "a\n", True, "Групповая"),
        (now - timedelta(days=2), "b\n", False, "Индивидуальная"),
        (now - timedelta(days=40), "c\n", False, "Групповая"),
    ]
    stats = _visit_history_entries_for_stats(entries, "grp")
    assert len(stats) == 2
    display = _visit_history_entries_for_display(
        entries, filter_days=7, kind_code="ind", now=now
    )
    assert len(display) == 1
    assert display[0][1] == "b\n"


def test_visit_history_period_stats_filters_by_days():
    now = datetime(2026, 5, 22, 12, 0)
    old = now - timedelta(days=40)
    recent = now - timedelta(days=3)
    entries = [
        (old, "line\n", False),
        (recent, "line\n", True),
        (recent, "line2\n", False),
    ]
    stats = dict(
        (label, (present, absent, total))
        for label, present, absent, total in _visit_history_period_stats(entries, now=now)
    )
    assert stats["7 дней"] == (1, 1, 2)
    assert stats["30 дней"] == (1, 1, 2)
    assert stats["120 дней"] == (1, 2, 3)


def test_render_visit_history_message_grouped_by_day():
    now = datetime(2026, 5, 22, 12, 0)
    raw = [
        (datetime(2026, 5, 18, 17, 0), "❌ 17:00 · MMA | Групповая\n", False, "Групповая"),
        (datetime(2026, 5, 18, 18, 30), "❌ 18:30 · MMA | Групповая\n", False, "Групповая"),
        (datetime(2026, 5, 20, 9, 30), "✅ 09:30 · MMA | Индивидуальная\n", True, "Индивидуальная"),
        (datetime(2026, 5, 20, 17, 0), "❌ 17:00 · MMA | Групповая\n", False, "Групповая"),
    ]
    entries = [(dt, line, present) for dt, line, present, _k in raw]
    text = _render_visit_history_message(
        "Иван Петров", entries, stats_entries=entries, now=now
    )
    assert "• 7 дней: ✅ 1 · ❌ 3 · всего 4" in text
    assert "• 120 дней: ✅ 1 · ❌ 3 · всего 4" in text
    assert "▸ 18.05.2026" in text
    assert "▸ 20.05.2026" in text
    assert text.index("▸ 18.05.2026") < text.index("▸ 20.05.2026")


def test_render_visit_history_truncation_note():
    now = now_moscow()
    all_entries = [
        (now - timedelta(days=i), f"line{i}\n", False, "Групповая") for i in range(30)
    ]
    display = [(e[0], e[1], e[2]) for e in all_entries[-5:]]
    stats = [(e[0], e[1], e[2]) for e in all_entries]
    text = _render_visit_history_message(
        "Иван",
        display,
        stats_entries=stats,
        now=now,
        total_matching=30,
        filter_days=30,
        kind_code="grp",
    )
    assert "Показаны последние 5 из 30" in text
    assert "Фильтр:" in text


def test_render_visit_history_filtered_empty():
    now = datetime(2026, 5, 22, 12, 0)
    text = _render_visit_history_message(
        "Иван",
        [],
        stats_entries=[],
        now=now,
        filter_days=7,
        kind_code="sgl",
    )
    assert "По выбранному фильтру записей нет" in text
