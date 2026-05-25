"""Настройки клуба в БД (длительность тренировок и др.)."""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session as OrmSession

from database.models import ClubSetting
from utils.time_utils import (
    INDIVIDUAL_TRAINING_DURATION_MINUTES,
    TRAINING_DURATION_MINUTES,
)

KEY_GROUP_TRAINING_DURATION_MINUTES = "group_training_duration_minutes"
KEY_INDIVIDUAL_TRAINING_DURATION_MINUTES = "individual_training_duration_minutes"


def _read_positive_int_setting(session: OrmSession, key: str) -> Optional[int]:
    row = session.query(ClubSetting).filter_by(key=key).first()
    if not row or not str(row.value).strip().isdigit():
        return None
    value = int(str(row.value).strip())
    return value if value > 0 else None


def get_group_training_duration_minutes(session: OrmSession) -> int:
    """Длительность групповой пары (мин); приоритет — club_settings, иначе .env."""
    return (
        _read_positive_int_setting(session, KEY_GROUP_TRAINING_DURATION_MINUTES)
        or TRAINING_DURATION_MINUTES
    )


def get_individual_training_duration_minutes(session: OrmSession) -> int:
    """Длительность индивидуальной тренировки (мин); приоритет — club_settings."""
    return (
        _read_positive_int_setting(session, KEY_INDIVIDUAL_TRAINING_DURATION_MINUTES)
        or INDIVIDUAL_TRAINING_DURATION_MINUTES
    )
