"""UI-хелперы экрана «История посещений»."""
from datetime import datetime, timedelta

from database.models import Subscription, Training
from handlers.card_handlers import (
    _filter_visit_rows_last_month,
    _filter_visit_rows_older_month,
    _format_visit_history_slot_line,
    _group_older_visit_rows_by_month,
    _parse_visits_callback,
    _render_visit_history_month_picker,
    _VISIT_HISTORY_MODE_MONTH,
    _VISIT_HISTORY_MODE_OLDER_MENU,
    _VISIT_HISTORY_MODE_OLDER_MONTH,
    _visit_history_month_label,
    _visits_older_month_callback,
    _yyyymm_to_year_month,
)
from utils.time_utils import now_moscow


def test_parse_visits_callback_modes():
    assert _parse_visits_callback("visits_42") == (42, _VISIT_HISTORY_MODE_MONTH, None)
    assert _parse_visits_callback("visits_42_older") == (
        42,
        _VISIT_HISTORY_MODE_OLDER_MENU,
        None,
    )
    assert _parse_visits_callback("visits_42_older_202605") == (
        42,
        _VISIT_HISTORY_MODE_OLDER_MONTH,
        "202605",
    )


def test_visits_older_month_callback():
    assert _visits_older_month_callback(5, 2026, 5) == "visits_5_older_202605"
    assert _yyyymm_to_year_month("202605") == (2026, 5)


def test_group_older_visit_rows_by_month():
    now = datetime(2026, 5, 22, 12, 0)
    rows = [
        (now - timedelta(days=40), "a\n", False),
        (now - timedelta(days=45), "b\n", False),
        (now - timedelta(days=70), "c\n", False),
    ]
    grouped = _group_older_visit_rows_by_month(rows, now=now)
    assert len(grouped) >= 1
    assert _filter_visit_rows_last_month(
        [(now - timedelta(days=5), "x\n", False)], now=now
    )


def test_filter_visit_rows_older_month():
    now = datetime(2026, 5, 22, 12, 0)
    rows = [
        (datetime(2026, 4, 10, 17, 0), "a\n", False),
        (datetime(2026, 3, 10, 17, 0), "b\n", False),
    ]
    april = _filter_visit_rows_older_month(
        rows, year=2026, month=4, now=now
    )
    assert len(april) == 1
    assert _visit_history_month_label(2026, 4) == "04.2026"


def test_render_visit_history_month_picker():
    text = _render_visit_history_month_picker("Иван", {(2026, 4): []})
    assert "Выберите месяц" in text
    assert "Предшествующие" in text


def test_format_visit_history_slot_line_group():
    training = Training(
        sport_type="MMA",
        training_format=None,
        training_date=datetime(2026, 5, 18, 17, 0),
    )
    line = _format_visit_history_slot_line(
        training, None, subscription=Subscription(subscription_type="monthly")
    )
    assert "Групповая" in line
