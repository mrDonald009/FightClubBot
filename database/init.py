# database/__init__.py
from .models import Base, engine, Session, User, Training, AthleteInfo, Payment
from .db_utils import *

__all__ = [
    'Base', 'engine', 'Session', 'User', 'Training', 'AthleteInfo', 'Payment',
    'create_user', 'get_user_by_telegram_id', 'create_athlete_info', 'get_coach_athletes',
    'create_training', 'get_athlete_trainings', 'get_coach_trainings', 'mark_attendance',
    'create_payment', 'get_user_payments', 'count_coach_athletes', 'get_athlete_info',
    'update_user', 'update_athlete_info', 'get_all_athletes', 'get_athlete_by_id', 'delete_user'
]