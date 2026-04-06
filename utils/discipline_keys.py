"""Ключи направлений абонемента: (вид спорта × формат group|individual)."""

from __future__ import annotations

import re
from typing import Optional

# Стабильные ключи для продуктовых направлений
THAI_BOXING_GROUP = "thai_boxing_group"
THAI_BOXING_INDIVIDUAL = "thai_boxing_individual"
MMA_GROUP = "mma_group"
MMA_INDIVIDUAL = "mma_individual"


def slug_from_sport_name(sport_type: str) -> str:
    """Латинский slug из названия вида спорта (fallback для неизвестных)."""
    if not sport_type:
        return "unknown"
    s = sport_type.strip().lower()
    s = re.sub(r"[^a-z0-9а-яё]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    if not s:
        return "unknown"
    # укорачиваем кириллический slug для индекса
    return s[:48]


def discipline_key_for(sport_type: str, *, format: str = "group") -> str:
    """
    Ключ направления для подписки.

    format: 'group' | 'individual'
    """
    fmt = (format or "group").strip().lower()
    if fmt not in ("group", "individual"):
        fmt = "group"

    st = (sport_type or "").strip()
    if st == "Тайский Бокс":
        return THAI_BOXING_INDIVIDUAL if fmt == "individual" else THAI_BOXING_GROUP
    if st == "MMA":
        return MMA_INDIVIDUAL if fmt == "individual" else MMA_GROUP

    base = slug_from_sport_name(st)
    return f"{base}_{fmt}"


def default_group_key_for_subscription_sport(sport_type: str) -> str:
    """Для миграции и потока «только группа» без явного format."""
    return discipline_key_for(sport_type, format="group")


def format_training_format_ru(discipline_key: Optional[str]) -> str:
    """Человекочитаемый формат занятий по ключу направления (групповые / индивидуальные)."""
    if not discipline_key or not str(discipline_key).strip():
        return "—"
    key = str(discipline_key).strip().lower()
    if key.endswith("_individual") or "_individual" in key:
        return "Индивидуальные"
    if key.endswith("_group"):
        return "Групповые"
    if "individual" in key:
        return "Индивидуальные"
    return "—"
