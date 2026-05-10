import os
from datetime import datetime, timedelta
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

# После окончания пары без строки в attendances в UI считаем «Не был», а не «Не отмечено».
ATTENDANCE_UNMARKED_TO_ABSENT_AFTER_TRAINING_END = timedelta(hours=24)

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


def training_end_time(training_start: datetime) -> datetime:
    """Время окончания тренировки по времени ее начала."""
    return training_start + TRAINING_DURATION


def individual_training_end_time(training_start: datetime) -> datetime:
    """Конец индивидуальной тренировки (по ТЗ — 1,5 ч; совпадает с TRAINING_DURATION)."""
    return training_end_time(training_start)
