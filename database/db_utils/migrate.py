from datetime import datetime, timedelta
from sqlalchemy import func
from sqlalchemy.orm import Session
from database.models import (
    Athlete,
    Attendance,
    GlobalFreeze,
    GlobalFreezeApplication,
    Subscription,
    Training,
)
from utils.time_utils import now_moscow
from utils.training_manager import TrainingManager

from .global_freeze import is_training_in_global_freeze
from .remaining import (
    purge_auto_attendances_during_active_global_freeze,
    sync_subscription_trainings_remaining,
)
from .schedule import _calculate_12th_training_date

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
    # НЕ перезаписываем end_date, если абонемент заморожен или был продлён личной заморозкой.
    # Массовая заморозка не ставит is_frozen / frozen_training_days_total, но фиксируется
    # в global_freeze_applications — нижняя граница end_date не должна быть ниже max(new_end_date).
    if subscription.start_date and not subscription.is_frozen and not (subscription.frozen_training_days_total or 0):
        correct_end_date = _calculate_12th_training_date(subscription.start_date, athlete.sport_type, athlete.age_group)
        gf_ceiling = (
            session.query(func.max(GlobalFreezeApplication.new_end_date))
            .join(GlobalFreeze, GlobalFreezeApplication.global_freeze_id == GlobalFreeze.id)
            .filter(
                GlobalFreezeApplication.subscription_id == subscription.id,
                GlobalFreezeApplication.training_days_added > 0,
                GlobalFreezeApplication.new_end_date.isnot(None),
                GlobalFreeze.is_active == True,
            )
            .scalar()
        )
        if gf_ceiling is not None:
            correct_end_date = max(correct_end_date, gf_ceiling)
        if subscription.end_date != correct_end_date:
            old_end = subscription.end_date
            subscription.end_date = correct_end_date
            changes.append(f"Дата окончания исправлена: {old_end.strftime('%d.%m.%Y %H:%M') if old_end else '—'} → {correct_end_date.strftime('%d.%m.%Y %H:%M')}")
    
    # 2. Создаем тренировки по расписанию и списываем прошедшие
    schedule = TrainingManager.TRAINING_SCHEDULE.get(athlete.sport_type, {}).get(athlete.age_group)
    if not schedule:
        return {"success": False, "message": "Расписание не найдено"}
    
    days = schedule['days']
    
    coach_id = athlete.created_by
    start_date = subscription.start_date if subscription.start_date else now_moscow()
    end_date = subscription.end_date
    
    # Считаем списания по календарным датам (не по времени суток)
    current_date = start_date.date()
    end_date_only = end_date.date()
    today_date = now_moscow().date()
    trainings_created = 0
    trainings_deducted = 0
    
    while current_date <= end_date_only and current_date <= today_date:
        # Проверяем, это ли день тренировки по расписанию
        if current_date.weekday() in days:
            hour, minute = TrainingManager.get_hour_minute_for_weekday(schedule, current_date.weekday())
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
            
            # Пропускаем тренировки в период заморозки — не списываем
            if subscription.is_frozen and subscription.frozen_from and subscription.frozen_until:
                if subscription.frozen_from <= training_datetime <= subscription.frozen_until:
                    current_date += timedelta(days=1)
                    continue

            # Массовая заморозка: как в auto_deduct_daily_trainings — не списываем и убираем ошибочное авто-списание
            if is_training_in_global_freeze(session, training_datetime):
                existing_attendance = session.query(Attendance).filter_by(
                    athlete_id=athlete.id,
                    training_id=training.id,
                    subscription_id=subscription.id,
                ).first()
                if existing_attendance and (
                    existing_attendance.was_restored is None or existing_attendance.was_restored is False
                ) and existing_attendance.marked_by is None:
                    session.delete(existing_attendance)
                current_date += timedelta(days=1)
                continue
            
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
                    created_at=now_moscow()
                )
                session.add(attendance)
                subscription.trainings_remaining -= 1
                trainings_deducted += 1
        
        current_date += timedelta(days=1)

    removed_gf = purge_auto_attendances_during_active_global_freeze(session, subscription)
    if removed_gf:
        changes.append(f"Удалено авто-списаний в периоде массовой заморозки: {removed_gf}")

    # Финальная синхронизация: единый source of truth для остатка.
    prev_remaining = subscription.trainings_remaining
    if sync_subscription_trainings_remaining(session, subscription):
        changes.append(f"Синхронизация остатка: {prev_remaining} -> {subscription.trainings_remaining}")
    
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
