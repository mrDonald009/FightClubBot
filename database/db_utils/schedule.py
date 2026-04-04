import calendar
import logging
from datetime import datetime, timedelta

from utils.training_manager import TrainingManager
from utils.time_utils import ACTIVATION_GRACE_AFTER_START, now_moscow, training_end_time

logger = logging.getLogger(__name__)


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


def _calculate_12th_training_date(start_date: datetime, sport_type: str, age_group: str) -> datetime:
    """
    Рассчитать дату окончания абонемента.
    
    Дата окончания = Дата начала + 11 тренировочных дней + время начала тренировки + 1,5 часа
    
    Args:
        start_date: Дата начала абонемента (дата первой тренировки)
        sport_type: Вид спорта
        age_group: Возрастная группа (children, adults)
        
    Returns:
        Дата окончания абонемента (дата 12-й тренировки + 1,5 часа)
    """
    schedule = TrainingManager.TRAINING_SCHEDULE.get(sport_type, {}).get(age_group)
    if not schedule:
        # Если расписание не найдено, возвращаем дату через месяц + 1,5 часа (fallback)
        fallback_date = _calculate_end_date(start_date, months=1)
        return training_end_time(fallback_date)
    
    days = schedule['days']
    
    # Начинаем с даты первой тренировки (start_date уже должна быть днем тренировки)
    # Но на всякий случай проверяем и находим ближайший день тренировки, если нужно
    current_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
    
    # Если start_date не является днем тренировки, находим ближайший день тренировки
    if current_date.weekday() not in days:
        # Ищем ближайший день тренировки (максимум 7 дней вперед)
        for i in range(7):
            check_date = current_date + timedelta(days=i)
            if check_date.weekday() in days:
                current_date = check_date
                break
    
    training_count = 0
    max_days_to_search = 60  # Защита от бесконечного цикла (примерно 2 месяца)
    days_searched = 0
    
    # Ищем 12 тренировок по расписанию (1-я тренировка + 11 следующих)
    while training_count < 12 and days_searched < max_days_to_search:
        # Проверяем, это ли день тренировки по расписанию
        if current_date.weekday() in days:
            training_count += 1
            if training_count == 12:
                # Это 12-я тренировка - возвращаем её дату с правильным временем + 1,5 часа
                hour, minute = TrainingManager.get_hour_minute_for_weekday(schedule, current_date.weekday())
                training_datetime = current_date.replace(hour=hour, minute=minute, second=0, microsecond=0)
                return training_end_time(training_datetime)
        
        # Переходим к следующему дню
        current_date += timedelta(days=1)
        days_searched += 1
    
    # Если не нашли 12 тренировок (не должно произойти), возвращаем fallback
    # Логируем проблему для отладки
    logger.warning(
        f"Не удалось найти 12 тренировок для {sport_type}/{age_group}. "
        f"Начало: {start_date}, найдено тренировок: {training_count}, дней проверено: {days_searched}"
    )
    fallback_date = _calculate_end_date(start_date, months=1)
    return training_end_time(fallback_date)
