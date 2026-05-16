"""Возрастные группы: коды, кнопки, расписание."""
from datetime import date

from utils.age_groups import (
    AGE_GROUP_CODES,
    format_age_group_label,
    normalize_age_group,
    parse_age_group_button,
)
def test_parse_middle_button():
    assert parse_age_group_button("Средняя") == "middle"
    assert parse_age_group_button("Детская") == "children"


def test_format_middle_label():
    assert format_age_group_label("middle") == "Средняя"
    assert format_age_group_label("middle", short=True) == "Средняя"


def test_normalize_russian_middle():
    assert normalize_age_group("Средняя") == "middle"


def test_training_schedule_has_middle_for_mma_and_thai():
    from utils.training_manager import TrainingManager

    for sport in ("MMA", "Тайский Бокс"):
        sched = TrainingManager.TRAINING_SCHEDULE[sport]
        assert "middle" in sched
        assert set(sched["middle"]["days"]) == set(sched["children"]["days"])


def test_middle_tuesday_slot_time():
    from utils.training_manager import TrainingManager

    day = date(2026, 4, 7)
    assert day.weekday() == 1
    sched = TrainingManager.TRAINING_SCHEDULE["Тайский Бокс"]["middle"]
    assert day.weekday() in sched["days"]
    assert TrainingManager.get_time_str_for_weekday(sched, day.weekday()) == "18:30"


def test_thai_saturday_children_start():
    from utils.training_manager import TrainingManager

    sched = TrainingManager.TRAINING_SCHEDULE["Тайский Бокс"]["children"]
    assert TrainingManager.get_time_str_for_weekday(sched, 5) == "11:00"


def test_all_codes_order():
    assert AGE_GROUP_CODES == ("children", "middle", "adults")
