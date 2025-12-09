from sqlalchemy.orm import Session
from database.models import User, Athlete, Subscription, Training, Attendance, RestorationRequest
from datetime import datetime, timedelta
import json
from utils.subscription_checker import SubscriptionChecker


def get_user_by_telegram_id(session: Session, telegram_id: int):
    """Получить пользователя по telegram_id"""
    return session.query(User).filter_by(telegram_id=telegram_id).first()


def create_user(session: Session, telegram_id: int, username: str, first_name: str, role: str = "athlete",
                sport_type: str = None):
    """Создать нового пользователя"""
    user = User(
        telegram_id=telegram_id,
        username=username,
        first_name=first_name,
        role=role,
        sport_type=sport_type
    )
    session.add(user)
    session.commit()
    return user


def create_athlete(session: Session, user_id: int, full_name: str, phone: str, medical_info: str,
                   sport_type: str, age_group: str, created_by: int, height: int = None, weight: int = None):
    """Создать спортсмена"""
    athlete = Athlete(
        user_id=user_id,
        full_name=full_name,
        phone=phone,
        height=height,
        weight=weight,
        medical_info=medical_info,
        sport_type=sport_type,
        age_group=age_group,
        created_by=created_by
    )
    session.add(athlete)
    session.commit()
    return athlete


def create_subscription(session: Session, athlete_id: int, subscription_type: str):
    """Создать абонемент для спортсмена"""
    if subscription_type == "monthly":
        trainings_total = 12
        end_date = datetime.utcnow() + timedelta(days=30)
    else:  # single
        trainings_total = 1
        end_date = datetime.utcnow() + timedelta(days=1)

    subscription = Subscription(
        athlete_id=athlete_id,
        subscription_type=subscription_type,
        end_date=end_date,
        trainings_total=trainings_total,
        trainings_remaining=trainings_total
    )
    session.add(subscription)
    session.commit()
    return subscription


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

    subscription = athlete.current_subscription
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
    month_ago = datetime.utcnow() - timedelta(days=30)

    total_trainings = session.query(Training).filter(
        Training.sport_type == athlete.sport_type,
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
            "has_telegram": bool(athlete.user_id)
        },
        "medical_display": medical_display,
        "age_group_display": "Детская" if athlete.age_group == "children" else "Взрослая",
        "status_display": status_display  # Добавляем отформатированный статус
    }