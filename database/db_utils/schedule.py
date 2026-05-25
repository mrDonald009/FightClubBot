import calendar
import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session as OrmSession

from utils.training_manager import TrainingManager
from utils.time_utils import ACTIVATION_GRACE_AFTER_START, now_moscow, training_end_time

logger = logging.getLogger(__name__)


def _group_training_duration_minutes(session: Optional[OrmSession]) -> int:
    if session is None:
        from utils.time_utils import TRAINING_DURATION_MINUTES

        return TRAINING_DURATION_MINUTES
    from .club_settings import get_group_training_duration_minutes

    return get_group_training_duration_minutes(session)


def _align_start_date_to_schedule(
    start_date: datetime, sport_type: str, age_group: str
) -> datetime:
    """
    Выравнивание start_date по актуальному времени расписания (тот же календарный день).
    Legacy-абонементы могли хранить 18:00 до смены расписания детской группы на 17:00.
    """
    if not start_date:
        return start_date
    schedule = TrainingManager.TRAINING_SCHEDULE.get(sport_type, {}).get(age_group)
    if not schedule:
        return start_date

    day = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
    if day.weekday() not in schedule["days"]:
        return start_date

    hour, minute = TrainingManager.get_hour_minute_for_weekday(schedule, day.weekday())
    aligned = day.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if aligned != start_date.replace(second=0, microsecond=0):
        return aligned
    return start_date


def _calculate_end_date(start_date: datetime, months: int = 1) -> datetime:
    """
    Рассчитать дату окончания абонемента (start_date + months месяцев).
    Например: 13.12.2025 + 1 месяц = 13.01.2026
    """
    year = start_date.year
    month = start_date.month
    day = start_date.day
    
    # Добавляем месяцы
    month += months
    while month > 12:
        month -= 12
        year += 1
    
    # Проверяем, существует ли такой день в целевом месяце (например, 31 января)
    max_day = calendar.monthrange(year, month)[1]
    if day > max_day:
        day = max_day
    
    return datetime(year, month, day, start_date.hour, start_date.minute, start_date.second)


def _find_nearest_training_date(coach_selected_date: datetime, sport_type: str, age_group: str) -> datetime:
    """
    Найти ближайшую дату тренировки согласно расписанию, начиная с даты, указанной тренером.
    
    Дата начала абонемента должна соответствовать дню недели и времени тренировки
    согласно виду спорта и возрастной группе.
    
    Args:
        coach_selected_date: Дата, которую указал тренер при активации абонемента
        sport_type: Вид спорта
        age_group: Возрастная группа (children, adults)
        
    Returns:
        Ближайшая дата тренировки согласно расписанию (с правильным временем)
    """
    schedule = TrainingManager.TRAINING_SCHEDULE.get(sport_type, {}).get(age_group)
    if not schedule:
        # Если расписание не найдено, возвращаем дату как есть (fallback)
        return coach_selected_date
    
    days = schedule['days']
    
    # Нормализуем дату тренера до начала дня
    coach_date_only = coach_selected_date.replace(hour=0, minute=0, second=0, microsecond=0)
    
    # Создаем datetime для тренировки в выбранный день
    hour, minute = TrainingManager.get_hour_minute_for_weekday(schedule, coach_date_only.weekday())
    training_datetime = coach_date_only.replace(hour=hour, minute=minute, second=0, microsecond=0)
    
    # Проверяем, соответствует ли выбранная дата дню тренировки
    if coach_date_only.weekday() in days:
        # Это день тренировки — считаем слот ещё доступным до (начало + запас по .env)
        current_time = now_moscow()
        slot_deadline = training_datetime + ACTIVATION_GRACE_AFTER_START
        if current_time <= slot_deadline:
            return training_datetime
        coach_date_only += timedelta(days=1)
    
    # Ищем ближайший день тренировки (максимум 7 дней вперед)
    for i in range(7):
        check_date = coach_date_only + timedelta(days=i)
        if check_date.weekday() in days:
            # Нашли день тренировки - возвращаем с правильным временем
            hour, minute = TrainingManager.get_hour_minute_for_weekday(schedule, check_date.weekday())
            return check_date.replace(hour=hour, minute=minute, second=0, microsecond=0)
    
    # Если не нашли (не должно произойти), возвращаем исходную дату с временем тренировки
    hour, minute = TrainingManager.get_hour_minute_for_weekday(schedule, coach_date_only.weekday())
    return coach_date_only.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _calculate_12th_training_date(
    start_date: datetime,
    sport_type: str,
    age_group: str,
    session: Optional[OrmSession] = None,
) -> datetime:
    """
    Рассчитать дату окончания абонемента.

    Дата окончания = дата 12-й тренировки + длительность групповой пары
    (club_settings → fallback .env).
    """
    duration_minutes = _group_training_duration_minutes(session)
    aligned_start = _align_start_date_to_schedule(start_date, sport_type, age_group)

    schedule = TrainingManager.TRAINING_SCHEDULE.get(sport_type, {}).get(age_group)
    if not schedule:
        fallback_date = _calculate_end_date(aligned_start, months=1)
        return training_end_time(fallback_date, duration_minutes)

    days = schedule["days"]

    current_date = aligned_start.replace(hour=0, minute=0, second=0, microsecond=0)

    if current_date.weekday() not in days:
        for i in range(7):
            check_date = current_date + timedelta(days=i)
            if check_date.weekday() in days:
                current_date = check_date
                break

    training_count = 0
    max_days_to_search = 60
    days_searched = 0

    while training_count < 12 and days_searched < max_days_to_search:
        if current_date.weekday() in days:
            training_count += 1
            if training_count == 12:
                hour, minute = TrainingManager.get_hour_minute_for_weekday(
                    schedule, current_date.weekday()
                )
                training_datetime = current_date.replace(
                    hour=hour, minute=minute, second=0, microsecond=0
                )
                return training_end_time(training_datetime, duration_minutes)

        current_date += timedelta(days=1)
        days_searched += 1

    logger.warning(
        "Не удалось найти 12 тренировок для %s/%s. "
        "Начало: %s, найдено тренировок: %s, дней проверено: %s",
        sport_type,
        age_group,
        aligned_start,
        training_count,
        days_searched,
    )
    fallback_date = _calculate_end_date(aligned_start, months=1)
    return training_end_time(fallback_date, duration_minutes)
