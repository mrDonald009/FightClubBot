# Экспортируем обработчики
from .start import start, show_coach_menu, show_admin_menu, show_athlete_menu
from .coach_handlers import (
    coach_menu, add_athlete_start, add_athlete_full_name, add_athlete_phone,
    add_athlete_medical, add_athlete_age_group, add_athlete_subscription,
    athletes_list, cancel_athlete_creation,
    ATHLETE_FULL_NAME, ATHLETE_PHONE, ATHLETE_MEDICAL, ATHLETE_AGE_GROUP, ATHLETE_SUBSCRIPTION
)

__all__ = [
    'start', 'show_coach_menu', 'show_admin_menu', 'show_athlete_menu',
    'coach_menu', 'add_athlete_start', 'add_athlete_full_name', 'add_athlete_phone',
    'add_athlete_medical', 'add_athlete_age_group', 'add_athlete_subscription',
    'athletes_list', 'cancel_athlete_creation',
    'ATHLETE_FULL_NAME', 'ATHLETE_PHONE', 'ATHLETE_MEDICAL',
    'ATHLETE_AGE_GROUP', 'ATHLETE_SUBSCRIPTION'
]