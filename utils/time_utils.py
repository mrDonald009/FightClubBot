import os
from datetime import datetime, timedelta
from typing import Optional
try:
    from zoneinfo import ZoneInfo  # py3.9+
except Exception:  # pragma: no cover
    from backports.zoneinfo import ZoneInfo  # py3.8 fallback

# Для запуска отдельных скриптов без инициализации core.config
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

DEFAULT_TIMEZONE = "Europe/Moscow"
DEFAULT_TRAINING_DURATION_MINUTES = 90
DEFAULT_INDIVIDUAL_TRAINING_DURATION_MINUTES = 60
DEFAULT_ACTIVATION_GRACE_AFTER_START_MINUTES = 30

_timezone_name = os.getenv("APP_TIMEZONE", DEFAULT_TIMEZONE)
try:
    APP_TZ = ZoneInfo(_timezone_name)
except Exception:
    APP_TZ = ZoneInfo(DEFAULT_TIMEZONE)

try:
    _duration_raw = int(os.getenv("TRAINING_DURATION_MINUTES", str(DEFAULT_TRAINING_DURATION_MINUTES)))
    TRAINING_DURATION_MINUTES = _duration_raw if _duration_raw > 0 else DEFAULT_TRAINING_DURATION_MINUTES
except Exception:
    TRAINING_DURATION_MINUTES = DEFAULT_TRAINING_DURATION_MINUTES

TRAINING_DURATION = timedelta(minutes=TRAINING_DURATION_MINUTES)

try:
    _ind_dur_raw = int(
        os.getenv(
            "INDIVIDUAL_TRAINING_DURATION_MINUTES",
            str(DEFAULT_INDIVIDUAL_TRAINING_DURATION_MINUTES),
        )
    )
    INDIVIDUAL_TRAINING_DURATION_MINUTES = (
        _ind_dur_raw if _ind_dur_raw > 0 else DEFAULT_INDIVIDUAL_TRAINING_DURATION_MINUTES
    )
except Exception:
    INDIVIDUAL_TRAINING_DURATION_MINUTES = DEFAULT_INDIVIDUAL_TRAINING_DURATION_MINUTES

INDIVIDUAL_TRAINING_DURATION = timedelta(minutes=INDIVIDUAL_TRAINING_DURATION_MINUTES)

# Порог после окончания пары для трактовки отсутствия без явной записи в attendance.
# 0 — сразу после конца слота (как только now > end); раньше было 24 ч.
ATTENDANCE_UNMARKED_TO_ABSENT_AFTER_TRAINING_END = timedelta(0)

try:
    _grace_raw = int(
        os.getenv(
            "ACTIVATION_GRACE_AFTER_START_MINUTES",
            str(DEFAULT_ACTIVATION_GRACE_AFTER_START_MINUTES),
        )
    )
    ACTIVATION_GRACE_AFTER_START_MINUTES = (
        _grace_raw if _grace_raw >= 0 else DEFAULT_ACTIVATION_GRACE_AFTER_START_MINUTES
    )
except Exception:
    ACTIVATION_GRACE_AFTER_START_MINUTES = DEFAULT_ACTIVATION_GRACE_AFTER_START_MINUTES

ACTIVATION_GRACE_AFTER_START = timedelta(minutes=ACTIVATION_GRACE_AFTER_START_MINUTES)


def now_moscow() -> datetime:
    """
    Текущее время приложения (по APP_TIMEZONE) в naive-формате.
    В проекте datetime хранятся как naive, поэтому возвращаем naive datetime.
    """
    return datetime.now(APP_TZ).replace(tzinfo=None)


def today_moscow():
    """Текущая дата по Москве."""
    return now_moscow().date()


def training_end_time(training_start: datetime, duration_minutes: int = None) -> datetime:
    """Время окончания групповой тренировки."""
    if duration_minutes is None:
        delta = TRAINING_DURATION
    else:
        delta = timedelta(minutes=duration_minutes)
    return training_start + delta


def individual_training_end_time(
    training_start: datetime, duration_minutes: int = None
) -> datetime:
    """Конец индивидуальной тренировки."""
    if duration_minutes is None:
        delta = INDIVIDUAL_TRAINING_DURATION
    else:
        delta = timedelta(minutes=duration_minutes)
    return training_start + delta


def training_slot_end_time(
    training_start: datetime, training_format: Optional[str] = None
) -> datetime:
    """Конец слота по формату: individual — 1 ч, иначе групповая длительность."""
    fmt = (training_format or "").strip().lower()
    if fmt == "individual":
        return individual_training_end_time(training_start)
    return training_end_time(training_start)
