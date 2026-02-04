from sqlalchemy.orm import Session
from database.models import Coach, Admin, Assistant, Athlete, Subscription, Training, Attendance, RestorationRequest, SportType
from datetime import datetime, timedelta
import json
import calendar
from utils.subscription_checker import SubscriptionChecker
from utils.training_manager import TrainingManager
from typing import Optional, Union
from sqlalchemy import func, or_, and_


def get_user_role(user: Union[Coach, Admin, Assistant, Athlete]) -> str:
    """Определить роль пользователя"""
    if isinstance(user, Coach):
        return "coach"
    elif isinstance(user, Admin):
        return "admin"
    elif isinstance(user, Assistant):
        return "assistant"
    elif isinstance(user, Athlete):
        return "athlete"
    return "unknown"


def get_user_by_telegram_id(session: Session, telegram_id: int) -> Optional[Union[Coach, Admin, Assistant, Athlete]]:
    """Получить пользователя по telegram_id (проверяет все таблицы: coaches, admins, assistants, athletes)"""
    # Проверяем тренеров
    coach = session.query(Coach).filter_by(telegram_id=telegram_id).first()
    if coach:
        return coach
    
    # Проверяем админов
    admin = session.query(Admin).filter_by(telegram_id=telegram_id).first()
    if admin:
        return admin
    
    # Проверяем ассистентов
    assistant = session.query(Assistant).filter_by(telegram_id=telegram_id).first()
    if assistant:
        return assistant
    
    # Проверяем спортсменов
    athlete = session.query(Athlete).filter_by(telegram_id=telegram_id).first()
    if athlete:
        return athlete
    
    return None


def get_athletes_by_coach(session: Session, coach_id: int):
    """Получить список спортсменов, созданных конкретным тренером."""
    return (
        session.query(Athlete)
        .filter(Athlete.created_by == coach_id)
        .order_by(Athlete.full_name.asc())
        .all()
    )


def get_coach_by_sport_type(session: Session, sport_type: str) -> Optional[Coach]:
    """Получить тренера по виду спорта"""
    # Сначала пытаемся найти через связь с sport_types
    sport_type_obj = session.query(SportType).filter_by(name=sport_type).first()
    if sport_type_obj:
        coach = session.query(Coach).filter(
            Coach.sport_type_id == sport_type_obj.id,
            Coach.is_active == True
        ).first()
        if coach:
            return coach
    
    # Fallback: ищем по строке sport_type (для обратной совместимости)
    return session.query(Coach).filter(
        Coach.sport_type == sport_type,
        Coach.is_active == True
    ).first()


def create_user(session: Session, telegram_id: int, username: str, first_name: str, role: str = "athlete",
                sport_type: str = None) -> Union[Coach, Admin, Assistant]:
    """Создать нового пользователя в соответствующей таблице"""
    if role == "coach":
        # Получаем sport_type_id из таблицы sport_types
        sport_type_id = None
        if sport_type:
            sport_type_obj = session.query(SportType).filter_by(name=sport_type).first()
            if sport_type_obj:
                sport_type_id = sport_type_obj.id
            else:
                # Если вида спорта нет, создаем его
                sport_type_obj = SportType(name=sport_type, display_name=sport_type)
                session.add(sport_type_obj)
                session.flush()
                sport_type_id = sport_type_obj.id
        
        if not sport_type_id:
            raise ValueError(f"Не указан вид спорта для тренера или вид спорта '{sport_type}' не найден")
        
        coach = Coach(
            telegram_id=telegram_id,
            username=username,
            first_name=first_name,
            sport_type_id=sport_type_id,
            sport_type=sport_type  # Для обратной совместимости
        )
        session.add(coach)
        session.commit()
        return coach
    
    elif role == "admin":
        admin = Admin(
            telegram_id=telegram_id,
            username=username,
            first_name=first_name
        )
        session.add(admin)
        session.commit()
        return admin
    
    elif role == "assistant":
        assistant = Assistant(
            telegram_id=telegram_id,
            username=username,
            first_name=first_name
        )
        session.add(assistant)
        session.commit()
        return assistant
    
    else:
        # Для спортсменов не создаем запись в отдельной таблице, только в athletes
        raise ValueError(f"Для создания спортсмена используйте create_athlete")


def create_athlete(
    session: Session,
    telegram_id: int,
    full_name: str,
    phone: str,
    medical_info: str,
    sport_type: str,
    age_group: str,
    created_by: int,
    birth_date: datetime = None,
    height: int = None,
    weight: int = None
):
    """Создать спортсмена"""
    athlete = Athlete(
        telegram_id=telegram_id,
        full_name=full_name,
        phone=phone,
        birth_date=birth_date,
        height=height,
        weight=weight,
        medical_info=medical_info,
        sport_type=sport_type,
        age_group=age_group,
        created_by=created_by  # ID тренера из таблицы coaches
    )
    session.add(athlete)
    session.commit()
    return athlete


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
    from utils.training_manager import TrainingManager
    
    schedule = TrainingManager.TRAINING_SCHEDULE.get(sport_type, {}).get(age_group)
    if not schedule:
        # Если расписание не найдено, возвращаем дату как есть (fallback)
        return coach_selected_date
    
    days = schedule['days']
    time_str = schedule['time']
    hour, minute = map(int, time_str.split(':'))
    
    # Нормализуем дату тренера до начала дня
    coach_date_only = coach_selected_date.replace(hour=0, minute=0, second=0, microsecond=0)
    
    # Создаем datetime для тренировки в выбранный день
    training_datetime = coach_date_only.replace(hour=hour, minute=minute, second=0, microsecond=0)
    
    # Проверяем, соответствует ли выбранная дата дню тренировки
    if coach_date_only.weekday() in days:
        # Это день тренировки - проверяем, не прошло ли время
        current_time = datetime.utcnow()
        if training_datetime >= current_time:
            # Время тренировки еще не прошло - используем эту дату
            return training_datetime
        else:
            # Время уже прошло - начинаем поиск со следующего дня
            coach_date_only += timedelta(days=1)
    
    # Ищем ближайший день тренировки (максимум 7 дней вперед)
    for i in range(7):
        check_date = coach_date_only + timedelta(days=i)
        if check_date.weekday() in days:
            # Нашли день тренировки - возвращаем с правильным временем
            return check_date.replace(hour=hour, minute=minute, second=0, microsecond=0)
    
    # Если не нашли (не должно произойти), возвращаем исходную дату с временем тренировки
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
    from utils.training_manager import TrainingManager
    
    schedule = TrainingManager.TRAINING_SCHEDULE.get(sport_type, {}).get(age_group)
    if not schedule:
        # Если расписание не найдено, возвращаем дату через месяц + 1,5 часа (fallback)
        fallback_date = _calculate_end_date(start_date, months=1)
        return fallback_date + timedelta(hours=1.5)
    
    days = schedule['days']
    time_str = schedule['time']
    hour, minute = map(int, time_str.split(':'))
    
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
                training_datetime = current_date.replace(hour=hour, minute=minute, second=0, microsecond=0)
                return training_datetime + timedelta(hours=1.5)
        
        # Переходим к следующему дню
        current_date += timedelta(days=1)
        days_searched += 1
    
    # Если не нашли 12 тренировок (не должно произойти), возвращаем fallback
    # Логируем проблему для отладки
    import logging
    logger = logging.getLogger(__name__)
    logger.warning(
        f"Не удалось найти 12 тренировок для {sport_type}/{age_group}. "
        f"Начало: {start_date}, найдено тренировок: {training_count}, дней проверено: {days_searched}"
    )
    fallback_date = _calculate_end_date(start_date, months=1)
    return fallback_date + timedelta(hours=1.5)


def create_subscription(session: Session, athlete_id: int, subscription_type: str = None, sport_type: str = None):
    """
    Создать абонемент для спортсмена.
    
    Если subscription_type не указан, абонемент создается без типа (тип будет определен при активации).
    Если subscription_type указан, присваивает количество тренировок:
    - месячный (monthly): 12 тренировок
    - разовый (single): 1 тренировка
    
    Args:
        session: Сессия базы данных
        athlete_id: ID спортсмена
        subscription_type: Тип абонемента (monthly, single) или None (будет определен при активации)
        sport_type: Вид спорта для абонемента (если None, берется из спортсмена)
    """
    athlete = session.query(Athlete).filter_by(id=athlete_id).first()
    if not athlete:
        raise ValueError(f"Спортсмен с id={athlete_id} не найден")
    
    # Если sport_type не указан, берем из спортсмена
    if not sport_type:
        sport_type = athlete.sport_type
    
    # При создании абонемент неактивен, даты начала и окончания не устанавливаются
    # Они будут установлены при активации
    created_at = datetime.utcnow()
    
    # Если тип не указан, создаем абонемент без типа
    if subscription_type is None:
        trainings_total = None
        trainings_remaining = None
    elif subscription_type == "monthly":
        # Месячный абонемент - 12 тренировок
        trainings_total = 12
        trainings_remaining = 12
    elif subscription_type == "single":
        # Разовый абонемент - 1 тренировка
        trainings_total = 1
        trainings_remaining = 1
    else:
        raise ValueError(f"Неизвестный тип абонемента: {subscription_type}")

    subscription = Subscription(
        athlete_id=athlete_id,
        sport_type=sport_type,
        subscription_type=subscription_type,  # Может быть None
        start_date=None,  # Будет установлена при активации
        end_date=None,     # Будет установлена при активации
        trainings_total=trainings_total,  # Может быть None
        trainings_remaining=trainings_remaining,  # Может быть None
        is_active=False,   # По умолчанию неактивен
        created_at=created_at
    )
    session.add(subscription)
    session.flush()  # Получаем ID абонемента
    
    # Тренировки не создаются при создании абонемента
    # Они будут созданы при активации абонемента
    
    session.commit()
    return subscription


def _create_and_deduct_scheduled_trainings(
    session: Session, 
    subscription: Subscription, 
    athlete: Athlete,
    start_date: datetime,
    end_date: datetime
):
    """
    Создать тренировки по расписанию для абонемента и автоматически списать первую.
    """
    schedule = TrainingManager.TRAINING_SCHEDULE.get(athlete.sport_type, {}).get(athlete.age_group)
    if not schedule:
        return
    
    days = schedule['days']
    time_str = schedule['time']
    hour, minute = map(int, time_str.split(':'))
    
    # Получаем тренера спортсмена
    coach_id = athlete.created_by
    
    # Проходим по всем дням от start_date до end_date
    current_day = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
    end_day = end_date.replace(hour=23, minute=59, second=59, microsecond=999)
    
    while current_day <= end_day:
        # Проверяем, это ли день тренировки по расписанию
        if current_day.weekday() in days:
            # Создаем datetime с правильным временем
            training_datetime = current_day.replace(hour=hour, minute=minute, second=0, microsecond=0)
            
            # Пропускаем будущие тренировки (после end_date)
            if training_datetime > end_date:
                break
            
            # Проверяем, есть ли уже такая тренировка
            existing_training = session.query(Training).filter_by(
                sport_type=athlete.sport_type,
                age_group=athlete.age_group,
                training_date=training_datetime,
                is_cancelled=False
            ).first()
            
            if not existing_training:
                # Создаем новую тренировку
                training = Training(
                    sport_type=athlete.sport_type,
                    age_group=athlete.age_group,
                    training_date=training_datetime,
                    is_cancelled=False,
                    coach_id=coach_id
                )
                session.add(training)
                session.flush()
            else:
                training = existing_training
            
            # Важно: НЕ списываем тренировку при активации.
            # Списание делается по факту (авто-списание в день тренировки или отметка посещения).
        
        current_day += timedelta(days=1)


def auto_deduct_daily_trainings(session: Session):
    """
    Автоматически списать тренировки для всех активных абонементов в тренировочные дни.
    Вызывается ежедневно для списания тренировок по расписанию.
    """
    from utils.subscription_checker import SubscriptionChecker
    
    now = datetime.utcnow()
    today_date = now.date()
    today_date = now.date()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = now.replace(hour=23, minute=59, second=59, microsecond=999)
    
    # Получаем все активные абонементы (исключая замороженные)
    active_subscriptions = session.query(Subscription).filter(
        Subscription.is_active == True,
        Subscription.is_frozen == False,  # Пропускаем замороженные абонементы
        Subscription.end_date >= today_start
    ).all()
    
    deducted_count = 0
    
    for subscription in active_subscriptions:
        athlete = session.query(Athlete).filter_by(id=subscription.athlete_id).first()
        if not athlete or not athlete.sport_type or not athlete.age_group:
            continue
        
        # Проверяем статус абонемента
        status = SubscriptionChecker.get_subscription_status(subscription)
        if status != "active":
            continue
        
        schedule = TrainingManager.TRAINING_SCHEDULE.get(athlete.sport_type, {}).get(athlete.age_group)
        if not schedule:
            continue
        
        days = schedule['days']
        time_str = schedule['time']
        hour, minute = map(int, time_str.split(':'))
        
        # Проверяем, сегодня ли тренировочный день
        if now.weekday() not in days:
            continue
        
        # Создаем datetime для сегодняшней тренировки
        training_datetime = today_start.replace(hour=hour, minute=minute, second=0, microsecond=0)
        
        # Время окончания тренировки = время начала + 1.5 часа
        training_end_datetime = training_datetime + timedelta(hours=1.5)
        
        # Важно: списание происходит только после завершения тренировки (через 1.5 часа после начала)
        # Если тренировка еще не завершилась, пропускаем
        if now < training_end_datetime:
            continue
        
        # Проверяем, что тренировка в пределах действия абонемента (по датам)
        if not subscription.start_date or not subscription.end_date:
            continue
        if training_datetime.date() < subscription.start_date.date() or training_datetime.date() > subscription.end_date.date():
            continue
        
        # Проверяем, есть ли уже запись о посещении на эту тренировку
        existing_training = session.query(Training).filter_by(
            sport_type=athlete.sport_type,
            age_group=athlete.age_group,
            training_date=training_datetime,
            is_cancelled=False
        ).first()
        
        if not existing_training:
            # Создаем тренировку
            coach_id = athlete.created_by
            existing_training = Training(
                sport_type=athlete.sport_type,
                age_group=athlete.age_group,
                training_date=training_datetime,
                is_cancelled=False,
                coach_id=coach_id
            )
            session.add(existing_training)
            session.flush()
        
        # Проверяем, не списана ли уже тренировка
        existing_attendance = session.query(Attendance).filter_by(
            athlete_id=athlete.id,
            training_id=existing_training.id,
            subscription_id=subscription.id
        ).first()
        
        if not existing_attendance and subscription.trainings_remaining > 0:
            # Создаем запись о посещении с attended=False (неиспользовано по умолчанию)
            attendance = Attendance(
                athlete_id=athlete.id,
                training_id=existing_training.id,
                subscription_id=subscription.id,
                attended=False,  # По умолчанию неиспользовано
                marked_by=None,  # Автоматическое списание
                created_at=datetime.utcnow()
            )
            session.add(attendance)
            
            # Списываем тренировку
            subscription.trainings_remaining -= 1
            deducted_count += 1
    
    if deducted_count > 0:
        session.commit()
    
    return deducted_count


def migrate_existing_subscription(session: Session, subscription_id: int):
    """
    Применить новую логику к существующему абонементу.
    - Пересчитывает дату окончания (если нужно)
    - Создает тренировки по расписанию
    - Списывает уже прошедшие тренировки как "неиспользовано"
    """
    subscription = session.query(Subscription).filter_by(id=subscription_id).first()
    if not subscription:
        return {"success": False, "message": "Абонемент не найден"}
    
    athlete = session.query(Athlete).filter_by(id=subscription.athlete_id).first()
    if not athlete or not athlete.sport_type or not athlete.age_group:
        return {"success": False, "message": "Данные спортсмена неполные"}
    
    if subscription.subscription_type != "monthly":
        return {"success": False, "message": "Функция применяется только к месячным абонементам"}
    
    changes = []
    
    # 1. Пересчитываем дату окончания (дата 12-й тренировки + 1,5 часа)
    # НЕ перезаписываем end_date, если абонемент заморожен или был продлён заморозкой
    if subscription.start_date and not subscription.is_frozen and not (subscription.frozen_training_days_total or 0):
        correct_end_date = _calculate_12th_training_date(subscription.start_date, athlete.sport_type, athlete.age_group)
        if subscription.end_date != correct_end_date:
            old_end = subscription.end_date
            subscription.end_date = correct_end_date
            changes.append(f"Дата окончания исправлена: {old_end.strftime('%d.%m.%Y %H:%M') if old_end else '—'} → {correct_end_date.strftime('%d.%m.%Y %H:%M')}")
    
    # 2. Создаем тренировки по расписанию и списываем прошедшие
    schedule = TrainingManager.TRAINING_SCHEDULE.get(athlete.sport_type, {}).get(athlete.age_group)
    if not schedule:
        return {"success": False, "message": "Расписание не найдено"}
    
    days = schedule['days']
    time_str = schedule['time']
    hour, minute = map(int, time_str.split(':'))
    
    coach_id = athlete.created_by
    start_date = subscription.start_date if subscription.start_date else datetime.utcnow()
    end_date = subscription.end_date
    
    # Считаем списания по календарным датам (не по времени суток)
    current_date = start_date.date()
    end_date_only = end_date.date()
    today_date = datetime.utcnow().date()
    trainings_created = 0
    trainings_deducted = 0
    
    while current_date <= end_date_only and current_date <= today_date:
        # Проверяем, это ли день тренировки по расписанию
        if current_date.weekday() in days:
            training_datetime = datetime(current_date.year, current_date.month, current_date.day, hour, minute, 0)
            
            if training_datetime > end_date:
                break
            
            # Проверяем, есть ли уже такая тренировка
            existing_training = session.query(Training).filter_by(
                sport_type=athlete.sport_type,
                age_group=athlete.age_group,
                training_date=training_datetime,
                is_cancelled=False
            ).first()
            
            if not existing_training:
                # Создаем тренировку
                training = Training(
                    sport_type=athlete.sport_type,
                    age_group=athlete.age_group,
                    training_date=training_datetime,
                    is_cancelled=False,
                    coach_id=coach_id
                )
                session.add(training)
                session.flush()
                trainings_created += 1
            else:
                training = existing_training
            
            # Проверяем, списана ли уже тренировка
            existing_attendance = session.query(Attendance).filter_by(
                athlete_id=athlete.id,
                training_id=training.id,
                subscription_id=subscription.id
            ).first()
            
            # Списываем тренировки по календарной дате (а не времени), начиная с start_date
            if not existing_attendance and subscription.trainings_remaining > 0:
                attendance = Attendance(
                    athlete_id=athlete.id,
                    training_id=training.id,
                    subscription_id=subscription.id,
                    attended=False,  # Неиспользовано
                    marked_by=None,  # Автоматическое списание
                    created_at=datetime.utcnow()
                )
                session.add(attendance)
                subscription.trainings_remaining -= 1
                trainings_deducted += 1
        
        current_date += timedelta(days=1)

    # Финальная синхронизация: trainings_remaining должен соответствовать фактически списанным тренировкам.
    # Считаем "использовано" как количество завершенных и невосстановленных тренировок в пределах периода абонемента.
    if subscription.trainings_total is not None:
        prev_remaining = subscription.trainings_remaining
        current_time = datetime.utcnow()
        sync_filters = [
            Attendance.subscription_id == subscription.id,
            Training.training_date + timedelta(hours=1.5) <= current_time,
            or_(Attendance.was_restored == False, Attendance.was_restored == None)
        ]
        if subscription.start_date:
            sync_filters.append(Training.training_date >= subscription.start_date)
        if subscription.end_date:
            sync_filters.append(Training.training_date <= subscription.end_date)
        used_count = session.query(Attendance).join(Training).filter(and_(*sync_filters)).count()
        new_remaining = max(subscription.trainings_total - used_count, 0)
        if prev_remaining != new_remaining:
            subscription.trainings_remaining = new_remaining
            changes.append(f"Синхронизация остатка: {prev_remaining} -> {new_remaining}")
    
    if changes or trainings_created > 0 or trainings_deducted > 0:
        session.commit()
        changes.append(f"Создано тренировок: {trainings_created}")
        changes.append(f"Списано тренировок: {trainings_deducted}")
        return {"success": True, "message": "Абонемент обновлен", "changes": changes}
    else:
        return {"success": True, "message": "Абонемент уже актуален", "changes": []}


def migrate_subscription_by_athlete_name(session: Session, athlete_name: str):
    """
    Найти спортсмена по имени и применить миграцию к его активному абонементу.
    """
    athlete = session.query(Athlete).filter_by(full_name=athlete_name).first()
    if not athlete:
        return {"success": False, "message": f"Спортсмен '{athlete_name}' не найден"}
    
    # Получаем первый активный абонемент
    active_subscription = athlete.current_subscription
    if not active_subscription:
        return {"success": False, "message": "У спортсмена нет активного абонемента"}
    
    return migrate_existing_subscription(session, active_subscription.id)


def restore_training(session: Session, attendance_id: int, restored_by_id: int, reason: str = None):
    """Восстановить одну тренировку (для упрощенной системы)"""
    attendance = session.query(Attendance).filter_by(id=attendance_id).first()

    if not attendance:
        return {"success": False, "message": "Запись о посещении не найдена"}

    if attendance.attended:
        return {"success": False, "message": "Спортсмен присутствовал на тренировке"}

    if attendance.was_restored:
        return {"success": False, "message": "Тренировка уже была восстановлена"}

    # Находим активный абонемент спортсмена
    subscription = session.query(Subscription).filter(
        Subscription.athlete_id == attendance.athlete_id,
        Subscription.is_active == True
    ).first()

    if not subscription:
        return {"success": False, "message": "У спортсмена нет активного абонемента"}

    # Восстанавливаем тренировку
    subscription.trainings_remaining += 1
    subscription.total_restored += 1
    subscription.restored_this_month += 1

    # Помечаем тренировку как восстановленную
    attendance.was_restored = True
    attendance.restoration_reason = reason

    # Создаем запись о восстановлении
    restoration = RestorationRequest(
        athlete_id=attendance.athlete_id,
        subscription_id=subscription.id,
        missed_dates=json.dumps([attendance.training.training_date.strftime("%Y-%m-%d %H:%M")]),
        restored_count=1,
        reason=reason or "Восстановление тренером",
        restored_by=restored_by_id
    )
    session.add(restoration)

    session.commit()

    return {
        "success": True,
        "message": f"✅ Восстановлена 1 тренировка",
        "trainings_remaining": subscription.trainings_remaining,
        "total_restored": subscription.total_restored
    }


def restore_multiple_trainings(session: Session, athlete_id: int, training_ids: list, restored_by_id: int, reason: str):
    """Восстановить несколько тренировок"""
    # Получаем пропущенные тренировки
    attendances = session.query(Attendance).filter(
        Attendance.athlete_id == athlete_id,
        Attendance.id.in_(training_ids),
        Attendance.attended == False,
        Attendance.was_restored == False
    ).all()

    if not attendances:
        return {"success": False, "message": "Нет подходящих тренировок для восстановления"}

    # Находим активный абонемент
    subscription = session.query(Subscription).filter(
        Subscription.athlete_id == athlete_id,
        Subscription.is_active == True
    ).first()

    if not subscription:
        return {"success": False, "message": "У спортсмена нет активного абонемента"}

    restored_count = len(attendances)

    # Проверяем лимиты восстановления (нельзя восстановить больше, чем было в абонементе)
    if subscription.total_restored + restored_count > subscription.trainings_total:
        return {"success": False, "message": f"Нельзя восстановить больше {subscription.trainings_total} тренировок"}

    # Восстанавливаем тренировки
    subscription.trainings_remaining += restored_count
    subscription.total_restored += restored_count
    subscription.restored_this_month += restored_count

    # Помечаем тренировки как восстановленные
    missed_dates = []
    for attendance in attendances:
        attendance.was_restored = True
        attendance.restoration_reason = reason
        missed_dates.append(attendance.training.training_date.strftime("%Y-%m-%d %H:%M"))

    # Создаем запись о восстановлении
    restoration = RestorationRequest(
        athlete_id=athlete_id,
        subscription_id=subscription.id,
        missed_dates=json.dumps(missed_dates),
        restored_count=restored_count,
        reason=reason,
        restored_by=restored_by_id
    )
    session.add(restoration)

    session.commit()

    return {
        "success": True,
        "message": f"✅ Восстановлено {restored_count} тренировок",
        "trainings_remaining": subscription.trainings_remaining,
        "total_restored": subscription.total_restored
    }


def get_athlete_card_info(session: Session, athlete_id: int):
    """Получить информацию для карточки спортсмена"""
    athlete = session.query(Athlete).filter_by(id=athlete_id).first()

    if not athlete:
        return None

    # Получаем первый активный абонемент для обратной совместимости
    subscription = athlete.current_subscription
    
    # Получаем тренера по виду спорта из абонемента (если есть), иначе по виду спорта спортсмена
    coach = None
    if subscription and subscription.sport_type:
        coach = get_coach_by_sport_type(session, subscription.sport_type)
    elif athlete.sport_type:
        coach = get_coach_by_sport_type(session, athlete.sport_type)
    
    # Если не найден, используем тренера, который добавил спортсмена
    if not coach:
        coach = athlete.coach

    # АВТОМАТИЧЕСКАЯ ПРОВЕРКА СТАТУСА АБОНЕМЕНТА
    if subscription and subscription.is_active and subscription.end_date:
        current_time = datetime.utcnow()
        if subscription.end_date < current_time:
            # Автоматически деактивируем истекший абонемент
            subscription.is_active = False
            session.commit()
            print(f"🔄 Автоматически деактивирован абонемент #{subscription.id} для {athlete.full_name}")

    # Получаем статистику посещений за последние 30 дней
    # Используем вид спорта из абонемента, если он есть, иначе из спортсмена
    sport_type_for_stats = subscription.sport_type if subscription and subscription.sport_type else athlete.sport_type
    
    month_ago = datetime.utcnow() - timedelta(days=30)

    total_trainings = 0
    if sport_type_for_stats:
        total_trainings = session.query(Training).filter(
            Training.sport_type == sport_type_for_stats,
            Training.age_group == athlete.age_group,
            Training.training_date >= month_ago,
            Training.is_cancelled == False
        ).count()

    attended_trainings = session.query(Attendance).filter(
        Attendance.athlete_id == athlete_id,
        Attendance.attended == True,
        Attendance.training.has(Training.training_date >= month_ago)
    ).count()

    missed_trainings = session.query(Attendance).filter(
        Attendance.athlete_id == athlete_id,
        Attendance.attended == False,
        Attendance.training.has(Training.training_date >= month_ago),
        Attendance.was_restored == False
    ).count()

    attendance_rate = round((attended_trainings / total_trainings * 100), 1) if total_trainings > 0 else 0

    # Форматируем медицинскую информацию
    medical_display = athlete.medical_info
    if medical_display and len(medical_display) > 100:
        medical_display = medical_display[:97] + "..."

    # Получаем отформатированный статус абонемента
    status_display = SubscriptionChecker.format_subscription_status(subscription)

    return {
        "athlete": athlete,
        "subscription": subscription,
        "coach": coach,
        "stats": {
            "total_trainings": total_trainings,
            "attended_trainings": attended_trainings,
            "missed_trainings": missed_trainings,
            "attendance_rate": attendance_rate,
            # В новой схеме связь с Telegram хранится прямо в athletes.telegram_id
            "has_telegram": bool(getattr(athlete, "telegram_id", None))
        },
        "medical_display": medical_display,
        "age_group_display": "Детская" if athlete.age_group == "children" else "Взрослая",
        "status_display": status_display  # Добавляем отформатированный статус
    }


def _find_freeze_start_date(selected_date: datetime, sport_type: str, age_group: str) -> datetime:
    """
    Найти дату начала заморозки (ближайший тренировочный день + начало тренировки).
    
    Args:
        selected_date: Дата, выбранная тренером для начала заморозки
        sport_type: Вид спорта
        age_group: Возрастная группа (children, adults)
        
    Returns:
        Дата начала заморозки (тренировочный день + начало тренировки)
    """
    schedule = TrainingManager.TRAINING_SCHEDULE.get(sport_type, {}).get(age_group)
    if not schedule:
        # Если расписание не найдено, возвращаем дату как есть (fallback)
        return selected_date
    
    days = schedule['days']
    time_str = schedule['time']
    hour, minute = map(int, time_str.split(':'))
    
    # Нормализуем дату до начала дня
    date_only = selected_date.replace(hour=0, minute=0, second=0, microsecond=0)
    
    # Создаем datetime для тренировки в выбранный день
    training_datetime = date_only.replace(hour=hour, minute=minute, second=0, microsecond=0)
    
    # Проверяем, соответствует ли выбранная дата дню тренировки
    if date_only.weekday() in days:
        # Это день тренировки - проверяем, не прошло ли время
        current_time = datetime.utcnow()
        if training_datetime >= current_time:
            # Время тренировки еще не прошло - используем эту дату
            return training_datetime
        else:
            # Время уже прошло - начинаем поиск со следующего дня
            date_only += timedelta(days=1)
    
    # Ищем ближайший день тренировки (максимум 7 дней вперед)
    for i in range(7):
        check_date = date_only + timedelta(days=i)
        if check_date.weekday() in days:
            # Нашли день тренировки - возвращаем с правильным временем
            return check_date.replace(hour=hour, minute=minute, second=0, microsecond=0)
    
    # Если не нашли (не должно произойти), возвращаем исходную дату с временем тренировки
    return date_only.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _count_training_days_between(start_date: datetime, end_date: datetime, sport_type: str, age_group: str) -> int:
    """
    Подсчитать количество тренировочных дней между двумя датами.
    
    Args:
        start_date: Дата начала (включительно)
        end_date: Дата окончания (включительно)
        sport_type: Вид спорта
        age_group: Возрастная группа
        
    Returns:
        Количество тренировочных дней между датами
    """
    schedule = TrainingManager.TRAINING_SCHEDULE.get(sport_type, {}).get(age_group)
    if not schedule:
        # Если расписание не найдено, возвращаем количество календарных дней (fallback)
        return (end_date.date() - start_date.date()).days + 1
    
    days = schedule['days']
    
    # Нормализуем даты до начала дня
    start = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
    end = end_date.replace(hour=0, minute=0, second=0, microsecond=0)
    
    # Считаем количество тренировочных дней
    training_days_count = 0
    current_date = start
    
    while current_date <= end:
        if current_date.weekday() in days:
            training_days_count += 1
        current_date += timedelta(days=1)
    
    return training_days_count


def _find_freeze_end_date(selected_date: datetime, sport_type: str, age_group: str) -> datetime:
    """
    Найти дату окончания заморозки (тренировочный день + конец тренировки = начало + 1.5 часа).
    
    Args:
        selected_date: Дата, выбранная тренером для окончания заморозки
        sport_type: Вид спорта
        age_group: Возрастная группа (children, adults)
        
    Returns:
        Дата окончания заморозки (тренировочный день + конец тренировки)
    """
    schedule = TrainingManager.TRAINING_SCHEDULE.get(sport_type, {}).get(age_group)
    if not schedule:
        # Если расписание не найдено, возвращаем дату + 1.5 часа (fallback)
        return selected_date + timedelta(hours=1.5)
    
    days = schedule['days']
    time_str = schedule['time']
    hour, minute = map(int, time_str.split(':'))
    
    # Нормализуем дату до начала дня
    date_only = selected_date.replace(hour=0, minute=0, second=0, microsecond=0)
    
    # Создаем datetime для тренировки в выбранный день
    training_start = date_only.replace(hour=hour, minute=minute, second=0, microsecond=0)
    
    # Проверяем, соответствует ли выбранная дата дню тренировки
    if date_only.weekday() in days:
        # Это день тренировки - возвращаем начало тренировки + 1.5 часа
        return training_start + timedelta(hours=1.5)
    else:
        # Ищем ближайший день тренировки (максимум 7 дней вперед)
        for i in range(7):
            check_date = date_only + timedelta(days=i)
            if check_date.weekday() in days:
                # Нашли день тренировки - возвращаем начало тренировки + 1.5 часа
                training_start = check_date.replace(hour=hour, minute=minute, second=0, microsecond=0)
                return training_start + timedelta(hours=1.5)
    
    # Если не нашли (не должно произойти), возвращаем исходную дату + 1.5 часа
    return selected_date + timedelta(hours=1.5)


def freeze_subscription(
    session: Session,
    subscription_id: int,
    freeze_end_date: datetime
) -> dict:
    """
    Заморозить абонемент.
    
    Дата начала заморозки = ближайший тренировочный день + начало тренировки
    Дата окончания заморозки = тренировочный день + конец тренировки (начало + 1.5 часа)
    Срок действия абонемента продлевается на период заморозки.
    
    Args:
        session: Сессия базы данных
        subscription_id: ID абонемента
        freeze_end_date: Дата окончания заморозки (выбранная тренером)
        
    Returns:
        dict с результатом операции
    """
    subscription = session.query(Subscription).filter_by(id=subscription_id).first()
    if not subscription:
        return {"success": False, "message": "Абонемент не найден"}
    
    if not subscription.is_active:
        return {"success": False, "message": "Можно заморозить только активный абонемент"}
    
    if subscription.is_frozen:
        return {"success": False, "message": "Абонемент уже заморожен"}
    
    athlete = subscription.athlete
    if not athlete or not athlete.sport_type or not athlete.age_group:
        return {"success": False, "message": "Данные спортсмена неполные"}
    
    sport_type = subscription.sport_type or athlete.sport_type
    age_group = athlete.age_group
    
    # Находим дату начала заморозки (ближайший тренировочный день + начало тренировки)
    freeze_start = _find_freeze_start_date(datetime.utcnow(), sport_type, age_group)
    
    # Находим дату окончания заморозки (тренировочный день + конец тренировки)
    freeze_until = _find_freeze_end_date(freeze_end_date, sport_type, age_group)
    
    if freeze_until <= freeze_start:
        return {"success": False, "message": "Дата окончания заморозки должна быть позже даты начала"}
    
    # Подсчитываем количество тренировочных дней между датой начала и окончания заморозки
    training_days_count = _count_training_days_between(freeze_start, freeze_until, sport_type, age_group)
    
    # Логируем для отладки
    import logging
    logger = logging.getLogger(__name__)
    logger.info(
        f"Заморозка абонемента #{subscription_id}: "
        f"freeze_start={freeze_start.strftime('%d.%m.%Y %H:%M')}, "
        f"freeze_until={freeze_until.strftime('%d.%m.%Y %H:%M')}, "
        f"training_days_count={training_days_count}, "
        f"текущая end_date={subscription.end_date.strftime('%d.%m.%Y %H:%M') if subscription.end_date else 'None'}"
    )
    
    # Продлеваем срок действия абонемента при активации заморозки:
    # Дата окончания = Дата окончания + Кол-во замороженных тренировочных дней
    if subscription.end_date:
        schedule = TrainingManager.TRAINING_SCHEDULE.get(sport_type, {}).get(age_group)
        if schedule:
            days = schedule['days']
            time_str = schedule['time']
            hour, minute = map(int, time_str.split(':'))
            
            # Находим дату окончания абонемента (без времени)
            end_date_only = subscription.end_date.replace(hour=0, minute=0, second=0, microsecond=0)
            
            # Начинаем поиск с даты окончания абонемента (не со следующего дня!)
            current_date = end_date_only
            added_training_days = 0
            max_days_to_search = 90  # Защита от бесконечного цикла (примерно 3 месяца)
            days_searched = 0
            
            # Ищем тренировочные дни и добавляем их
            while added_training_days < training_days_count and days_searched < max_days_to_search:
                # Если текущая дата - тренировочный день, добавляем его
                if current_date.weekday() in days:
                    added_training_days += 1
                    if added_training_days == training_days_count:
                        # Это последний тренировочный день - устанавливаем дату окончания с временем окончания тренировки
                        subscription.end_date = current_date.replace(hour=hour, minute=minute, second=0, microsecond=0) + timedelta(hours=1.5)
                        break
                # Переходим к следующему дню
                current_date += timedelta(days=1)
                days_searched += 1
            
            # Логируем результат продления
            logger.info(
                f"Продление абонемента #{subscription_id}: "
                f"начальная end_date={end_date_only.strftime('%d.%m.%Y')}, "
                f"training_days_count={training_days_count}, "
                f"найдено тренировочных дней={added_training_days}, "
                f"новая end_date={subscription.end_date.strftime('%d.%m.%Y %H:%M') if subscription.end_date else 'None'}"
            )
            
            # Если не нашли нужное количество тренировочных дней, логируем предупреждение
            if added_training_days < training_days_count:
                logger.warning(
                    f"Не удалось найти {training_days_count} тренировочных дней для продления абонемента. "
                    f"Найдено: {added_training_days}, дней проверено: {days_searched}, "
                    f"текущая дата окончания: {subscription.end_date}"
                )
        else:
            # Если расписание не найдено, просто добавляем календарные дни (fallback)
            subscription.end_date = subscription.end_date + timedelta(days=training_days_count)
    
    # Устанавливаем параметры заморозки
    subscription.is_frozen = True
    subscription.frozen_from = freeze_start
    subscription.frozen_until = freeze_until
    # frozen_days_total - общее количество календарных дней заморозки (для статистики)
    freeze_calendar_days = (freeze_until.date() - freeze_start.date()).days
    subscription.frozen_days_total = (subscription.frozen_days_total or 0) + freeze_calendar_days
    # frozen_training_days_total - общее количество замороженных тренировочных дней
    subscription.frozen_training_days_total = (subscription.frozen_training_days_total or 0) + training_days_count
    
    session.commit()
    
    return {
        "success": True,
        "message": f"Абонемент заморожен до {freeze_until.strftime('%d.%m.%Y %H:%M')}",
        "freeze_start": freeze_start,
        "freeze_until": freeze_until,
        "training_days_count": training_days_count,
        "freeze_calendar_days": freeze_calendar_days
    }


def unfreeze_subscription(session: Session, subscription_id: int) -> dict:
    """
    Разморозить абонемент.
    
    Args:
        session: Сессия базы данных
        subscription_id: ID абонемента
        
    Returns:
        dict с результатом операции
    """
    subscription = session.query(Subscription).filter_by(id=subscription_id).first()
    if not subscription:
        return {"success": False, "message": "Абонемент не найден"}
    
    if not subscription.is_frozen:
        return {"success": False, "message": "Абонемент не заморожен"}
    
    # Размораживаем абонемент
    subscription.is_frozen = False
    subscription.frozen_from = None
    subscription.frozen_until = None
    
    session.commit()
    
    return {"success": True, "message": "Абонемент разморожен"}