"""Тесты utils.coach_sport."""
from database.models import Coach, SportType
from utils.coach_sport import coach_sport_type_name


def test_coach_sport_type_name_from_relation():
    st = SportType(name="MMA", display_name="MMA")
    coach = Coach(telegram_id=1, sport_type_id=1, sport_type="Legacy")
    coach.sport_type_rel = st
    assert coach_sport_type_name(coach) == "MMA"


def test_coach_sport_type_name_legacy_string():
    coach = Coach(telegram_id=1, sport_type_id=1, sport_type="Тайский Бокс")
    assert coach_sport_type_name(coach) == "Тайский Бокс"


def test_coach_sport_type_name_non_coach():
    assert coach_sport_type_name(object()) is None
