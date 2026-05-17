"""Вид спорта тренера: единая логика для handlers, services и отчётов."""
from typing import Any, Optional

from database.models import Coach


def sport_type_label_from_user(user: Any) -> Optional[str]:
    """Имя вида спорта из sport_type_rel / sport_type (без проверки класса)."""
    if user is None:
        return None
    rel = getattr(user, "sport_type_rel", None)
    if rel is not None:
        name = getattr(rel, "name", None)
        if name:
            return name
    legacy = getattr(user, "sport_type", None)
    if legacy:
        return legacy
    return None


def coach_sport_type_name(user: Any) -> Optional[str]:
    """
    Имя вида спорта для тренера (ORM Coach): связь sport_types приоритетнее legacy sport_type.
    Для не-тренера (админ, спортсмен и т.д.) — None.
    """
    if not isinstance(user, Coach):
        return None
    return sport_type_label_from_user(user)
