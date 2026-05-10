"""Публичный API доступа к БД: абонементы, посещения, заморозки, пользователи.

Реализация разнесена по модулям в этом пакете; сохраняются импорты
``from database.db_utils import ...`` и атрибуты вроде ``database.db_utils.training_end_time``
(для тестов с monkeypatch).
"""
from utils.time_utils import (
    ACTIVATION_GRACE_AFTER_START,
    TRAINING_DURATION,
    now_moscow,
    training_end_time,
)

from .remaining import (
    _active_global_freeze_covers_training_exists,
    calculate_actual_trainings_remaining,
    purge_auto_attendances_during_active_global_freeze,
    sync_subscription_trainings_remaining,
)
from .users import (
    create_athlete,
    create_user,
    get_athletes_by_coach,
    get_coach_by_sport_type,
    get_coach_by_telegram_id,
    get_user_by_telegram_id,
    get_user_role,
)
from .schedule import (
    _calculate_12th_training_date,
    _calculate_end_date,
    _find_nearest_training_date,
)
from .subscriptions import _create_and_deduct_scheduled_trainings, create_subscription
from .freeze_personal import (
    _count_training_days_between,
    _find_freeze_end_date,
    _find_freeze_start_date,
    freeze_athlete,
    freeze_subscription,
    is_training_in_athlete_personal_freeze,
    unfreeze_athlete,
    unfreeze_subscription,
)
from .global_freeze import (
    apply_global_freeze,
    deactivate_global_freeze_and_migrate,
    find_next_non_frozen_training_date,
    is_training_in_global_freeze,
    list_active_global_freezes_overlapping_range,
    parse_training_datetime_compact,
    training_datetime_compact,
)
from .auto_deduct import auto_deduct_daily_trainings
from .close_unmarked_attendance import close_unmarked_attendance_after_grace
from .migrate import migrate_existing_subscription, migrate_subscription_by_athlete_name
from .restore import restore_multiple_trainings, restore_training
from .athlete_card import get_athlete_card_info
from .coach_report import build_coach_period_report, coach_roster_athlete_ids, month_range
from .subscription_activation_payment import (
    record_payment_on_subscription_activation,
    resolve_subscription_activation_price_rubles,
)
from .subscription_tariffs import (
    TARIFF_KIND_INDIVIDUAL_TRAINING,
    TARIFF_KIND_SUBSCRIPTION_MONTHLY,
    TARIFF_KIND_SUBSCRIPTION_SINGLE,
    find_active_tariff_amount_rubles,
    tariff_kind_for_subscription_type,
    tariff_preview_for_sport,
    tariff_preview_monthly_single_for_sport,
)

__all__ = [
    "ACTIVATION_GRACE_AFTER_START",
    "TRAINING_DURATION",
    "now_moscow",
    "training_end_time",
    "_active_global_freeze_covers_training_exists",
    "calculate_actual_trainings_remaining",
    "purge_auto_attendances_during_active_global_freeze",
    "sync_subscription_trainings_remaining",
    "create_athlete",
    "create_user",
    "get_athletes_by_coach",
    "get_coach_by_sport_type",
    "get_coach_by_telegram_id",
    "get_user_by_telegram_id",
    "get_user_role",
    "_calculate_12th_training_date",
    "_calculate_end_date",
    "_find_nearest_training_date",
    "_create_and_deduct_scheduled_trainings",
    "create_subscription",
    "_count_training_days_between",
    "_find_freeze_end_date",
    "_find_freeze_start_date",
    "freeze_athlete",
    "freeze_subscription",
    "is_training_in_athlete_personal_freeze",
    "unfreeze_athlete",
    "unfreeze_subscription",
    "apply_global_freeze",
    "deactivate_global_freeze_and_migrate",
    "find_next_non_frozen_training_date",
    "is_training_in_global_freeze",
    "list_active_global_freezes_overlapping_range",
    "parse_training_datetime_compact",
    "training_datetime_compact",
    "auto_deduct_daily_trainings",
    "close_unmarked_attendance_after_grace",
    "migrate_existing_subscription",
    "migrate_subscription_by_athlete_name",
    "restore_multiple_trainings",
    "restore_training",
    "get_athlete_card_info",
    "build_coach_period_report",
    "coach_roster_athlete_ids",
    "month_range",
    "record_payment_on_subscription_activation",
    "resolve_subscription_activation_price_rubles",
    "TARIFF_KIND_INDIVIDUAL_TRAINING",
    "TARIFF_KIND_SUBSCRIPTION_MONTHLY",
    "TARIFF_KIND_SUBSCRIPTION_SINGLE",
    "find_active_tariff_amount_rubles",
    "tariff_kind_for_subscription_type",
    "tariff_preview_for_sport",
    "tariff_preview_monthly_single_for_sport",
]
