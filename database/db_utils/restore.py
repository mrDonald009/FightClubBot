import json
from sqlalchemy.orm import Session
from database.models import Attendance, RestorationRequest, Subscription

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
