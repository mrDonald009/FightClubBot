import logging
from datetime import datetime, timedelta
from typing import List, Optional

from sqlalchemy.orm import Session
from database.models import Athlete, AthleteFreeze, Subscription
from utils.training_manager import TrainingManager
from utils.time_utils import ACTIVATION_GRACE_AFTER_START, now_moscow, training_end_time

from .remaining import sync_subscription_trainings_remaining

logger = logging.getLogger(__name__)


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
    
    # Нормализуем дату до начала дня
    date_only = selected_date.replace(hour=0, minute=0, second=0, microsecond=0)
    
    # Создаем datetime для тренировки в выбранный день
    hour, minute = TrainingManager.get_hour_minute_for_weekday(schedule, date_only.weekday())
    training_datetime = date_only.replace(hour=hour, minute=minute, second=0, microsecond=0)
    
    # Проверяем, соответствует ли выбранная дата дню тренировки
    if date_only.weekday() in days:
        current_time = now_moscow()
        slot_deadline = training_datetime + ACTIVATION_GRACE_AFTER_START
        if current_time <= slot_deadline:
            return training_datetime
        date_only += timedelta(days=1)
    
    # Ищем ближайший день тренировки (максимум 7 дней вперед)
    for i in range(7):
        check_date = date_only + timedelta(days=i)
        if check_date.weekday() in days:
            # Нашли день тренировки - возвращаем с правильным временем
            hour, minute = TrainingManager.get_hour_minute_for_weekday(schedule, check_date.weekday())
            return check_date.replace(hour=hour, minute=minute, second=0, microsecond=0)
    
    # Если не нашли (не должно произойти), возвращаем исходную дату с временем тренировки
    hour, minute = TrainingManager.get_hour_minute_for_weekday(schedule, date_only.weekday())
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
        return training_end_time(selected_date)
    
    days = schedule['days']
    
    # Нормализуем дату до начала дня
    date_only = selected_date.replace(hour=0, minute=0, second=0, microsecond=0)
    
    # Создаем datetime для тренировки в выбранный день
    hour, minute = TrainingManager.get_hour_minute_for_weekday(schedule, date_only.weekday())
    training_start = date_only.replace(hour=hour, minute=minute, second=0, microsecond=0)
    
    # Проверяем, соответствует ли выбранная дата дню тренировки
    if date_only.weekday() in days:
        # Это день тренировки - возвращаем начало тренировки + 1.5 часа
        return training_end_time(training_start)
    else:
        # Ищем ближайший день тренировки (максимум 7 дней вперед)
        for i in range(7):
            check_date = date_only + timedelta(days=i)
            if check_date.weekday() in days:
                # Нашли день тренировки - возвращаем начало тренировки + 1.5 часа
                hour, minute = TrainingManager.get_hour_minute_for_weekday(schedule, check_date.weekday())
                training_start = check_date.replace(hour=hour, minute=minute, second=0, microsecond=0)
                return training_end_time(training_start)
    
    # Если не нашли (не должно произойти), возвращаем исходную дату + 1.5 часа
    return training_end_time(selected_date)


def _clear_last_freeze_revert_markers(subscription: Subscription) -> None:
    subscription.last_freeze_pre_end_date = None
    subscription.last_freeze_credit_training_days = None
    subscription.last_freeze_credit_calendar_days = None


def _revert_last_freeze_extension(subscription: Subscription) -> bool:
    """
    Откатить продление срока, начисленное при текущей (последней) персональной заморозке.
    Используется при ручной разморозке до истечения frozen_until.
    """
    pre_end = subscription.last_freeze_pre_end_date
    if pre_end is None:
        return False
    training_credit = subscription.last_freeze_credit_training_days or 0
    calendar_credit = subscription.last_freeze_credit_calendar_days or 0
    subscription.end_date = pre_end
    subscription.frozen_training_days_total = max(
        0, (subscription.frozen_training_days_total or 0) - training_credit
    )
    subscription.frozen_days_total = max(
        0, (subscription.frozen_days_total or 0) - calendar_credit
    )
    _clear_last_freeze_revert_markers(subscription)
    return True


def is_training_in_athlete_personal_freeze(
    session: Session, athlete_id: int, training_datetime: datetime
) -> bool:
    """True, если дата слота попадает в период персональной заморозки спортсмена (athlete_freezes)."""
    row = (
        session.query(AthleteFreeze.id)
        .filter(
            AthleteFreeze.athlete_id == athlete_id,
            AthleteFreeze.frozen_from <= training_datetime,
            AthleteFreeze.frozen_until >= training_datetime,
        )
        .first()
    )
    return row is not None


def freeze_subscription(
    session: Session,
    subscription_id: int,
    freeze_end_date: datetime,
    *,
    commit: bool = True,
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
    
    current_time = now_moscow()
    # Не допускаем заморозку "в прошлое" (по текущему времени приложения).
    if freeze_end_date <= current_time:
        return {
            "success": False,
            "message": "Дата окончания заморозки должна быть позже текущего времени"
        }

    # Находим дату начала заморозки (ближайший тренировочный день + начало тренировки)
    freeze_start = _find_freeze_start_date(now_moscow(), sport_type, age_group)
    
    # Находим дату окончания заморозки (тренировочный день + конец тренировки)
    freeze_until = _find_freeze_end_date(freeze_end_date, sport_type, age_group)
    
    if freeze_until <= freeze_start:
        return {"success": False, "message": "Дата окончания заморозки должна быть позже даты начала"}
    
    # Важно: заморозка не должна начинаться раньше начала абонемента.
    # Для разового абонемента end_date = конец одной тренировки, поэтому
    # считаем период до freeze_until (без ограничения текущим end_date),
    # чтобы корректно переносить единственную тренировку на следующий доступный слот.
    effective_freeze_start = max(freeze_start, subscription.start_date or freeze_start)
    if subscription.subscription_type in ("single", "individual"):
        effective_freeze_end = freeze_until
    else:
        effective_freeze_end = min(freeze_until, subscription.end_date) if subscription.end_date else freeze_until

    if effective_freeze_end <= effective_freeze_start:
        return {
            "success": False,
            "message": "Период заморозки не пересекается со сроком действия абонемента"
        }

    # Подсчитываем количество тренировочных дней в эффективном периоде заморозки
    training_days_count = _count_training_days_between(
        effective_freeze_start,
        effective_freeze_end,
        sport_type,
        age_group
    )
    
    # Логируем для отладки
    logger.info(
        f"Заморозка абонемента #{subscription_id}: "
        f"freeze_start={freeze_start.strftime('%d.%m.%Y %H:%M')}, "
        f"freeze_until={freeze_until.strftime('%d.%m.%Y %H:%M')}, "
        f"effective_start={effective_freeze_start.strftime('%d.%m.%Y %H:%M')}, "
        f"effective_end={effective_freeze_end.strftime('%d.%m.%Y %H:%M')}, "
        f"training_days_count={training_days_count}, "
        f"текущая end_date={subscription.end_date.strftime('%d.%m.%Y %H:%M') if subscription.end_date else 'None'}"
    )
    
    freeze_calendar_days = (effective_freeze_end.date() - effective_freeze_start.date()).days
    pre_extension_end_date = subscription.end_date

    # Продлеваем срок действия абонемента при активации заморозки:
    # Дата окончания = следующий(е) тренировочный(е) день(дни) после текущего end_date.
    credited_training_days = 0
    if subscription.end_date:
        schedule = TrainingManager.TRAINING_SCHEDULE.get(sport_type, {}).get(age_group)
        if schedule:
            days = schedule['days']
            
            # Берем день окончания абонемента (без времени)
            end_date_only = subscription.end_date.replace(hour=0, minute=0, second=0, microsecond=0)

            # Ключевой момент: добавляем ТОЛЬКО дни после текущего end_date,
            # чтобы не пересчитать последнюю уже включенную тренировку.
            current_date = end_date_only + timedelta(days=1)
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
                        hour, minute = TrainingManager.get_hour_minute_for_weekday(schedule, current_date.weekday())
                        subscription.end_date = training_end_time(
                            current_date.replace(hour=hour, minute=minute, second=0, microsecond=0)
                        )
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
            credited_training_days = added_training_days
        else:
            # Если расписание не найдено, просто добавляем календарные дни (fallback)
            subscription.end_date = subscription.end_date + timedelta(days=training_days_count)
            credited_training_days = training_days_count

    subscription.last_freeze_pre_end_date = pre_extension_end_date
    subscription.last_freeze_credit_training_days = credited_training_days
    subscription.last_freeze_credit_calendar_days = freeze_calendar_days

    # Устанавливаем параметры заморозки
    subscription.is_frozen = True
    subscription.frozen_from = effective_freeze_start
    subscription.frozen_until = effective_freeze_end
    subscription.frozen_days_total = (subscription.frozen_days_total or 0) + freeze_calendar_days
    # frozen_training_days_total - общее количество замороженных тренировочных дней
    subscription.frozen_training_days_total = (subscription.frozen_training_days_total or 0) + training_days_count
    sync_subscription_trainings_remaining(session, subscription, reason="after_freeze")
    if commit:
        session.commit()
    else:
        session.flush()

    return {
        "success": True,
        "message": f"Абонемент заморожен до {effective_freeze_end.strftime('%d.%m.%Y %H:%M')}",
        "freeze_start": effective_freeze_start,
        "freeze_until": effective_freeze_end,
        "training_days_count": training_days_count,
        "freeze_calendar_days": freeze_calendar_days
    }


def unfreeze_subscription(
    session: Session, subscription_id: int, *, commit: bool = True
) -> dict:
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

    now = now_moscow()
    reverted = False
    # Ручная разморозка до конца запланированного периода — отменяем продление этой сессии.
    if subscription.frozen_until and now < subscription.frozen_until:
        reverted = _revert_last_freeze_extension(subscription)

    subscription.is_frozen = False
    subscription.frozen_from = None
    subscription.frozen_until = None
    if not reverted:
        _clear_last_freeze_revert_markers(subscription)
    sync_subscription_trainings_remaining(session, subscription, reason="after_unfreeze")
    if commit:
        session.commit()
    else:
        session.flush()

    msg = "Абонемент разморожен"
    if reverted:
        msg += "; продление по этой заморозке отменено"
    return {"success": True, "message": msg, "reverted_extension": reverted}


def subscription_is_currently_frozen(
    subscription: Subscription, *, now: datetime = None
) -> bool:
    """True, если персональная заморозка ещё действует (frozen_until не прошёл)."""
    if not subscription or not subscription.is_frozen:
        return False
    now = now or now_moscow()
    if subscription.frozen_until and subscription.frozen_until < now:
        return False
    return True


def expire_stale_subscription_freezes(
    session: Session, *, athlete_id: int = None, commit: bool = True
) -> int:
    """
    Снять is_frozen у абонементов с истёкшим frozen_until.
    Возвращает число обновлённых строк subscriptions.
    """
    now = now_moscow()
    q = session.query(Subscription).filter(
        Subscription.is_frozen == True,
        Subscription.frozen_until.isnot(None),
        Subscription.frozen_until < now,
    )
    if athlete_id is not None:
        q = q.filter(Subscription.athlete_id == athlete_id)
    updated = 0
    for sub in q.all():
        sub.is_frozen = False
        sub.frozen_from = None
        sub.frozen_until = None
        _clear_last_freeze_revert_markers(sub)
        sync_subscription_trainings_remaining(
            session, sub, reason="after_auto_unfreeze"
        )
        updated += 1
    if updated:
        af_q = session.query(AthleteFreeze).filter(AthleteFreeze.frozen_until < now)
        if athlete_id is not None:
            af_q = af_q.filter(AthleteFreeze.athlete_id == athlete_id)
        af_q.delete(synchronize_session=False)
    if updated and commit:
        session.commit()
    elif updated:
        session.flush()
    return updated


def freeze_athlete(
    session: Session,
    athlete_id: int,
    freeze_end_date: datetime,
    *,
    initiated_by_coach_id: Optional[int] = None,
    global_freeze_id: Optional[int] = None,
    commit: bool = True,
) -> dict:
    """
    Персональная заморозка спортсмена: применяет логику freeze_subscription ко всем активным абонементам
    и создаёт запись athlete_freezes.
    """
    athlete = session.query(Athlete).filter_by(id=athlete_id).first()
    if not athlete:
        return {"success": False, "message": "Спортсмен не найден"}

    active_subs = [s for s in athlete.subscriptions if s.is_active]
    if not active_subs:
        return {"success": False, "message": "Нет активного абонемента"}

    if any(s.is_frozen for s in active_subs):
        return {"success": False, "message": "У спортсмена уже заморожен абонемент"}

    freeze_starts: List[datetime] = []
    freeze_ends: List[datetime] = []
    for sub in active_subs:
        res = freeze_subscription(
            session, sub.id, freeze_end_date, commit=False
        )
        if not res["success"]:
            session.rollback()
            return res
        freeze_starts.append(res["freeze_start"])
        freeze_ends.append(res["freeze_until"])

    af = AthleteFreeze(
        athlete_id=athlete_id,
        frozen_from=min(freeze_starts),
        frozen_until=max(freeze_ends),
        initiated_by_coach_id=initiated_by_coach_id,
        global_freeze_id=global_freeze_id,
    )
    session.add(af)
    if commit:
        session.commit()
    else:
        session.flush()

    until = max(freeze_ends)
    return {
        "success": True,
        "message": f"Заморозка до {until.strftime('%d.%m.%Y %H:%M')}",
        "freeze_start": min(freeze_starts),
        "freeze_until": until,
        "subscriptions_updated": len(active_subs),
    }


def unfreeze_athlete(
    session: Session, athlete_id: int, *, commit: bool = True
) -> dict:
    """Снять персональную заморозку: разморозить все активные замороженные абонементы и удалить athlete_freezes."""
    athlete = session.query(Athlete).filter_by(id=athlete_id).first()
    if not athlete:
        return {"success": False, "message": "Спортсмен не найден"}

    frozen_subs = [s for s in athlete.subscriptions if s.is_active and s.is_frozen]
    if not frozen_subs:
        session.query(AthleteFreeze).filter_by(athlete_id=athlete_id).delete(
            synchronize_session=False
        )
        if commit:
            session.commit()
        else:
            session.flush()
        return {"success": False, "message": "Нет замороженных абонементов"}

    for sub in frozen_subs:
        res = unfreeze_subscription(session, sub.id, commit=False)
        if not res["success"]:
            session.rollback()
            return res

    session.query(AthleteFreeze).filter_by(athlete_id=athlete_id).delete(
        synchronize_session=False
    )
    if commit:
        session.commit()
    else:
        session.flush()

    return {"success": True, "message": "Спортсмен разморожен"}
