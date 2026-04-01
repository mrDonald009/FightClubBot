import logging
from datetime import datetime, timedelta
from database.models import Session, Athlete, Subscription, Training, Attendance
from utils.subscription_checker import SubscriptionChecker
from utils.time_utils import now_moscow, training_end_time

logger = logging.getLogger(__name__)


class TrainingManager:
    """Управление тренировками и автоматическим списанием"""

    # Расписание тренировок
    TRAINING_SCHEDULE = {
        'MMA': {
            'children': {
                'days': [0, 2, 4],  # Пн, Ср, Пт (0=понедельник)
                'time': '18:00'
            },
            'adults': {
                'days': [0, 2, 4],
                'time': '20:00'
            }
        },
        'Тайский Бокс': {
            'children': {
                'days': [1, 3, 5],  # Вт, Чт, Сб
                'time': '18:00',
                # Точечные переопределения времени по дням недели (0=Пн ... 6=Вс)
                'day_times': {
                    5: '12:30',  # Суббота
                },
            },
            'adults': {
                'days': [1, 3, 5],
                'time': '20:00',
                'day_times': {
                    5: '14:00',  # Суббота
                },
            }
        }
    }

    @staticmethod
    def get_time_str_for_weekday(schedule: dict, weekday: int) -> str:
        """Получить строку времени тренировки для конкретного дня недели."""
        day_times = schedule.get('day_times') or {}
        return day_times.get(weekday, schedule['time'])

    @staticmethod
    def get_hour_minute_for_weekday(schedule: dict, weekday: int):
        """Получить (hour, minute) для конкретного дня недели."""
        time_str = TrainingManager.get_time_str_for_weekday(schedule, weekday)
        return map(int, time_str.split(':'))

    @staticmethod
    def get_next_training_date(sport_type, age_group, start_date=None):
        """Получить дату следующей тренировки"""
        if not start_date:
            start_date = now_moscow()

        schedule = TrainingManager.TRAINING_SCHEDULE.get(sport_type, {}).get(age_group)
        if not schedule:
            raise ValueError(f"Расписание не найдено для {sport_type}/{age_group}")

        days = schedule['days']
        current_weekday = start_date.weekday()

        # Ищем ближайший день тренировки
        for i in range(7):
            check_date = start_date + timedelta(days=i)
            if check_date.weekday() in days:
                return check_date

        return None

    @staticmethod
    def calculate_missed_trainings(athlete_id):
        """Рассчитать пропущенные тренировки для спортсмена"""
        session = Session()
        try:
            athlete = session.query(Athlete).filter_by(id=athlete_id).first()
            if not athlete or not athlete.current_subscription:
                return {"missed": 0, "total_passed": 0, "details": []}

            subscription = athlete.current_subscription
            schedule = TrainingManager.TRAINING_SCHEDULE.get(athlete.sport_type, {}).get(athlete.age_group)
            if not schedule:
                return {"missed": 0, "total_passed": 0, "details": []}

            # Дата начала абонемента
            start_date = subscription.start_date
            current_date = now_moscow()

            days = schedule['days']
            missed_trainings = 0
            total_passed_trainings = 0
            training_dates = []

            # Проходим по всем дням с начала абонемента
            # Нормализуем start_date до начала дня для корректной итерации
            current_day = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
            while current_day <= current_date:
                # Проверяем, это ли день тренировки
                if current_day.weekday() in days:
                    hour, minute = TrainingManager.get_hour_minute_for_weekday(schedule, current_day.weekday())
                    # Устанавливаем время начала тренировки
                    training_start_datetime = current_day.replace(
                        hour=hour, minute=minute, second=0, microsecond=0
                    )
                    
                    # Время окончания тренировки = время начала + 1.5 часа
                    training_end_datetime = training_end_time(training_start_datetime)
                    
                    # Проверяем, прошло ли время окончания тренировки
                    if training_end_datetime > current_date:
                        # Тренировка еще не закончилась, пропускаем
                        current_day += timedelta(days=1)
                        continue

                    # Проверяем, была ли эта тренировка
                    training = session.query(Training).filter_by(
                        sport_type=athlete.sport_type,
                        age_group=athlete.age_group,
                        training_date=training_start_datetime,
                        is_cancelled=False
                    ).first()

                    if training:
                        # Проверяем, есть ли запись о посещении
                        attendance = session.query(Attendance).filter_by(
                            athlete_id=athlete_id,
                            training_id=training.id
                        ).first()

                        if attendance:
                            if not attendance.attended and not attendance.was_restored:
                                missed_trainings += 1
                                training_dates.append({
                                    'date': training_start_datetime,
                                    'status': 'missed',
                                    'training_id': training.id
                                })
                        else:
                            # Если нет записи о посещении - считаем пропущенной
                            missed_trainings += 1
                            training_dates.append({
                                'date': training_start_datetime,
                                'status': 'missed_no_record',
                                'training_id': training.id
                            })

                    total_passed_trainings += 1

                current_day += timedelta(days=1)

            return {
                "missed": missed_trainings,
                "total_passed": total_passed_trainings,
                "details": training_dates,
                "trainings_remaining": subscription.trainings_remaining
            }

        except Exception as e:
            logger.error(f"❌ Ошибка расчета пропущенных тренировок: {e}")
            return {"missed": 0, "total_passed": 0, "details": [], "error": str(e)}
        finally:
            session.close()

    @staticmethod
    def auto_deduct_trainings(athlete_id):
        """Автоматическое списание тренировок по расписанию"""
        session = Session()
        try:
            athlete = session.query(Athlete).filter_by(id=athlete_id).first()
            if not athlete or not athlete.current_subscription:
                return {"success": False, "message": "Спортсмен или абонемент не найден"}

            subscription = athlete.current_subscription

            # Проверяем статус абонемента
            status = SubscriptionChecker.get_subscription_status(subscription)
            if status != "active":
                return {"success": False, "message": f"Абонемент не активен (статус: {status})"}

            # Рассчитываем пропущенные тренировки
            result = TrainingManager.calculate_missed_trainings(athlete_id)
            missed_trainings = result['missed']

            if missed_trainings == 0:
                return {"success": True, "message": "Нет тренировок для списания", "deducted": 0}

            # Проверяем, что у абонемента достаточно тренировок
            if subscription.trainings_remaining < missed_trainings:
                return {
                    "success": False,
                    "message": f"Недостаточно тренировок в абонементе. Нужно: {missed_trainings}, есть: {subscription.trainings_remaining}"
                }

            # Создаем записи о пропущенных тренировках
            for training_info in result['details']:
                training_date = training_info['date']

                # Находим или создаем тренировку
                training = session.query(Training).filter_by(
                    sport_type=athlete.sport_type,
                    age_group=athlete.age_group,
                    training_date=training_date,
                    is_cancelled=False
                ).first()

                if not training:
                    # Используем тренера спортсмена (кто его создал)
                    coach_id = athlete.created_by if athlete.created_by else None
                    
                    training = Training(
                        sport_type=athlete.sport_type,
                        age_group=athlete.age_group,
                        training_date=training_date,
                        is_cancelled=False,
                        coach_id=coach_id
                    )
                    session.add(training)
                    session.flush()

                # Проверяем, нет ли уже записи о посещении
                attendance = session.query(Attendance).filter_by(
                    athlete_id=athlete_id,
                    training_id=training.id
                ).first()

                if not attendance:
                    # Создаем запись о пропуске
                    attendance = Attendance(
                        athlete_id=athlete_id,
                        training_id=training.id,
                        subscription_id=subscription.id,
                        attended=False,  # Пропущено
                        marked_by=None,  # Автоматическое списание
                        was_restored=False
                    )
                    session.add(attendance)

            # Списываем тренировки
            subscription.trainings_remaining -= missed_trainings
            session.commit()

            return {
                "success": True,
                "message": f"Автоматически списано {missed_trainings} тренировок",
                "deducted": missed_trainings,
                "remaining": subscription.trainings_remaining
            }

        except Exception as e:
            logger.error(f"❌ Ошибка автоматического списания: {e}")
            session.rollback()
            return {"success": False, "message": f"Ошибка: {str(e)}"}
        finally:
            session.close()

    @staticmethod
    def get_training_schedule_info(sport_type, age_group):
        """Получить информацию о расписании"""
        schedule = TrainingManager.TRAINING_SCHEDULE.get(sport_type, {}).get(age_group)
        if not schedule:
            return None

        day_names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
        day_entries = []
        for day in schedule['days']:
            day_time = TrainingManager.get_time_str_for_weekday(schedule, day)
            day_entries.append(f"{day_names[day]} {day_time}")
        days_text = ", ".join(day_entries)

        return {
            "days": ", ".join([day_names[day] for day in schedule['days']]),
            "time": schedule['time'],
            "full_schedule": days_text,
        }