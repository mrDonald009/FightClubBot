"""Возрастные группы спортсменов и отображение в UI."""
from __future__ import annotations

from typing import Optional, Tuple

AGE_GROUP_CHILDREN = "children"
AGE_GROUP_MIDDLE = "middle"
AGE_GROUP_ADULTS = "adults"

AGE_GROUP_CODES: Tuple[str, ...] = (
    AGE_GROUP_CHILDREN,
    AGE_GROUP_MIDDLE,
    AGE_GROUP_ADULTS,
)

BUTTON_LABELS: Tuple[str, ...] = ("Детская", "Средняя", "Взрослая")
LABEL_TO_CODE = dict(zip(BUTTON_LABELS, AGE_GROUP_CODES))
CODE_TO_LABEL = {code: label for label, code in LABEL_TO_CODE.items()}

SHORT_LABELS = {
    AGE_GROUP_CHILDREN: "Дети",
    AGE_GROUP_MIDDLE: "Средняя",
    AGE_GROUP_ADULTS: "Взрослые",
}


def is_valid_age_group(code: Optional[str]) -> bool:
    return (code or "").strip() in AGE_GROUP_CODES


def parse_age_group_button(text: Optional[str]) -> Optional[str]:
    """Текст кнопки «Детская» / «Средняя» / «Взрослая» → код в БД."""
    return LABEL_TO_CODE.get((text or "").strip())


def format_age_group_label(code: Optional[str], *, short: bool = False) -> str:
    if not code:
        return "Не указана"
    key = (code or "").strip()
    if short:
        return SHORT_LABELS.get(key, key)
    return CODE_TO_LABEL.get(key, key)


def normalize_age_group(value: Optional[str]) -> Optional[str]:
    """Привести legacy/русские значения к коду children | middle | adults."""
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    lower = raw.lower()
    if lower in AGE_GROUP_CODES:
        return lower
    legacy = {
        "детская": AGE_GROUP_CHILDREN,
        "child": AGE_GROUP_CHILDREN,
        "kids": AGE_GROUP_CHILDREN,
        "средняя": AGE_GROUP_MIDDLE,
        "middle": AGE_GROUP_MIDDLE,
        "взрослая": AGE_GROUP_ADULTS,
        "adult": AGE_GROUP_ADULTS,
        "adults": AGE_GROUP_ADULTS,
    }
    return legacy.get(lower, raw if is_valid_age_group(raw) else raw)
