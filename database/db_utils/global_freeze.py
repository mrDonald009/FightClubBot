import logging
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session
from database.models import (
    Athlete,
    GlobalFreeze,
    GlobalFreezeApplication,
    Subscription,
    Training,
)
from utils.training_manager import TrainingManager
from utils.time_utils import now_moscow, training_end_time

from .freeze_personal import _count_training_days_between
from .remaining import (
    purge_auto_attendances_during_active_global_freeze,
    sync_subscription_trainings_remaining,
)

logger = logging.getLogger(__name__)


def is_training_in_global_freeze(session: Session, training_datetime: datetime) -> bool:
    """Проверить, попадает ли тренировка в период активной массовой заморозки."""
    gf = session.query(GlobalFreeze).filter(
        GlobalFreeze.is_active == True,
        GlobalFreeze.start_date <= training_datetime,
        GlobalFreeze.end_date >= training_datetime
    ).first()
    return bool(gf)


def find_next_non_frozen_calendar_date(
    session: Session, from_day: date, max_days: int = 400
) -> date:
    """Первый календарный день (полдень как тестовая точка), не попадающий в активную массовую заморозку."""
    d = from_day
    for _ in range(max_days):
        noon = datetime(d.year, d.month, d.day, 12, 0, 0)
        if not is_training_in_global_freeze(session, noon):
            return d
        d = d + timedelta(days=1)
    return from_day


def training_datetime_compact(dt: datetime) -> str:
    """12 цифр YYYYMMDDHHMM для callback_data (лимит Telegram 64 байта)."""
    return (
        f"{dt.year:04d}{dt.month:02d}{dt.day:02d}"
        f"{dt.hour:02d}{dt.minute:02d}"
    )


def parse_training_datetime_compact(s: str) -> Optional[datetime]:
    """Разобрать суффикс из 12 цифр в datetime (naive, локальное время слота)."""
    if not s or len(s) != 12 or not s.isdigit():
        return None
    try:
        y = int(s[0:4])
        mo = int(s[4:6])
        d = int(s[6:8])
        h = int(s[8:10])
        mi = int(s[10:12])
        return datetime(y, mo, d, h, mi)
    except ValueError:
        return None


def find_next_non_frozen_training_date(
    session: Session, base_date: datetime, sport_type: str, age_group: str
) -> datetime:
    """Ближайшая дата тренировки по расписанию вне активной массовой заморозки."""
    candidate = _find_nearest_training_date(base_date, sport_type, age_group)
    for _ in range(120):
        if not is_training_in_global_freeze(session, candidate):
            return candidate
        next_day = (candidate + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        candidate = _find_nearest_training_date(next_day, sport_type, age_group)
    return candidate


def list_active_global_freezes_overlapping_range(
    session: Session,
    start_date: datetime,
    end_date: datetime,
):
    """
    Активные массовые заморозки, пересекающиеся с интервалом [start_date .. end_date]
    (нормализация границ дня совпадает с apply_global_freeze).
    """
    freeze_start = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
    freeze_end = end_date.replace(hour=23, minute=59, second=59, microsecond=999999)
    return (
        session.query(GlobalFreeze)
        .filter(GlobalFreeze.is_active == True)
        .filter(GlobalFreeze.start_date <= freeze_end)
        .filter(GlobalFreeze.end_date >= freeze_start)
        .order_by(GlobalFreeze.start_date.asc())
        .all()
    )


def apply_global_freeze(
    session: Session,
    start_date: datetime,
    end_date: datetime,
    title: str,
    created_by: int = None,
) -> dict:
    """
    Применить массовую заморозку:
    - создается запись global_freezes,
    - все активные абонементы продлеваются на число тренировочных дней в периоде,
    - фиксируется application для идемпотентности.
    """
    if end_date < start_date:
        return {"success": False, "message": "Дата окончания меньше даты начала"}

    # Нормализуем границы до полного диапазона дней
    freeze_start = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
    freeze_end = end_date.replace(hour=23, minute=59, second=59, microsecond=999999)

    overlapping = list_active_global_freezes_overlapping_range(session, start_date, end_date)
    if overlapping:
        ids = ", ".join(str(gf.id) for gf in overlapping[:5])
        if len(overlapping) > 5:
            ids += ", ..."
        return {
            "success": False,
            "message": (
                "Новая массовая заморозка пересекается с уже существующей "
                f"(ID: {ids}). Пересечения запрещены."
            ),
        }

    global_freeze = GlobalFreeze(
        title=title.strip() or "Массовая заморозка",
        start_date=freeze_start,
        end_date=freeze_end,
        is_active=True,
        created_by=created_by,
        created_at=now_moscow(),
    )
    session.add(global_freeze)
    session.flush()

    updated = 0
    skipped = 0
    total_training_days_added = 0

    active_subscriptions = session.query(Subscription).filter(Subscription.is_active == True).all()
    for subscription in active_subscriptions:
        # Защита от повторного применения к тому же абонементу
        existing = session.query(GlobalFreezeApplication).filter_by(
            global_freeze_id=global_freeze.id,
            subscription_id=subscription.id
        ).first()
        if existing:
            skipped += 1
            continue

        athlete = subscription.athlete
        if not athlete:
            skipped += 1
            continue

        sport_type = subscription.sport_type or athlete.sport_type
        age_group = athlete.age_group
        if not sport_type or not age_group or not subscription.end_date:
            skipped += 1
            continue

        # Если период заморозки полностью вне диапазона абонемента, application фиксируем с 0 дней.
        period_start = max(subscription.start_date or freeze_start, freeze_start)
        period_end = min(subscription.end_date, freeze_end)
        if period_end < period_start:
            app = GlobalFreezeApplication(
                global_freeze_id=global_freeze.id,
                subscription_id=subscription.id,
                training_days_added=0,
                old_end_date=subscription.end_date,
                new_end_date=subscription.end_date,
                created_at=now_moscow(),
            )
            session.add(app)
            skipped += 1
            continue

        training_days_count = _count_training_days_between(period_start, period_end, sport_type, age_group)
        # Учитываем персональную заморозку: не добавляем дни, которые уже покрыты ею
        overlap_training_days = 0
        if subscription.is_frozen and subscription.frozen_from and subscription.frozen_until:
            overlap_start = max(period_start, subscription.frozen_from)
            overlap_end = min(period_end, subscription.frozen_until)
            if overlap_end >= overlap_start:
                overlap_training_days = _count_training_days_between(
                    overlap_start, overlap_end, sport_type, age_group
                )

        effective_training_days = max(training_days_count - overlap_training_days, 0)
        old_end_date = subscription.end_date
        new_end_date = old_end_date

        if effective_training_days > 0:
            schedule = TrainingManager.TRAINING_SCHEDULE.get(sport_type, {}).get(age_group)
            if schedule:
                days = schedule['days']
                current_date = old_end_date.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
                added = 0
                searched = 0
                while added < effective_training_days and searched < 180:
                    if current_date.weekday() in days:
                        added += 1
                        if added == effective_training_days:
                            hour, minute = TrainingManager.get_hour_minute_for_weekday(schedule, current_date.weekday())
                            new_end_date = training_end_time(
                                current_date.replace(hour=hour, minute=minute, second=0, microsecond=0)
                            )
                            break
                    current_date += timedelta(days=1)
                    searched += 1
            else:
                new_end_date = old_end_date + timedelta(days=effective_training_days)

            subscription.end_date = new_end_date
            purge_auto_attendances_during_active_global_freeze(session, subscription)
            sync_subscription_trainings_remaining(session, subscription, reason="after_global_freeze")
            updated += 1
            total_training_days_added += effective_training_days
        else:
            skipped += 1

        app = GlobalFreezeApplication(
            global_freeze_id=global_freeze.id,
            subscription_id=subscription.id,
            training_days_added=effective_training_days,
            old_end_date=old_end_date,
            new_end_date=new_end_date,
            created_at=now_moscow(),
        )
        session.add(app)

    session.commit()

    logger.info(
        "GF apply: id=%s title=%r range=%s..%s updated=%s skipped=%s added_days=%s",
        global_freeze.id,
        global_freeze.title,
        freeze_start.strftime("%Y-%m-%d"),
        freeze_end.strftime("%Y-%m-%d"),
        updated,
        skipped,
        total_training_days_added,
    )
    return {
        "success": True,
        "message": "Массовая заморозка применена",
        "global_freeze_id": global_freeze.id,
        "updated_subscriptions": updated,
        "skipped_subscriptions": skipped,
        "total_training_days_added": total_training_days_added,
        "start_date": freeze_start,
        "end_date": freeze_end,
    }


def deactivate_global_freeze_and_migrate(session: Session, gf_id: int) -> dict:
    """
    Деактивировать массовую заморозку (is_active=False) и пересчитать затронутые абонементы.
    - monthly: полный migrate_existing_subscription
    - все типы: контрольная синхронизация trainings_remaining
    """
    gf = session.query(GlobalFreeze).filter_by(id=gf_id).first()
    if not gf:
        return {
            "success": False,
            "message": f"Массовая заморозка с ID={gf_id} не найдена",
            "migrated": 0,
        }

    if not gf.is_active:
        return {
            "success": True,
            "already_inactive": True,
            "message": f"Массовая заморозка #{gf_id} уже не активна",
            "migrated": 0,
            "checked": 0,
            "synced": 0,
            "title": gf.title,
            "global_freeze_id": gf_id,
        }

    # Защита от "случайного отката истории": очень старые периоды отключаем только вручную.
    now = now_moscow()
    if gf.end_date and gf.end_date < (now - timedelta(days=30)):
        return {
            "success": False,
            "message": (
                f"Массовая заморозка #{gf_id} завершилась более 30 дней назад. "
                "Для таких записей используйте ручной регламент (админ/БД)."
            ),
            "migrated": 0,
            "checked": 0,
            "synced": 0,
        }

    gf.is_active = False
    session.commit()

    subscription_ids = (
        session.query(GlobalFreezeApplication.subscription_id)
        .filter(GlobalFreezeApplication.global_freeze_id == gf_id)
        .distinct()
        .all()
    )
    subscription_ids = [x[0] for x in subscription_ids]

    from .migrate import migrate_existing_subscription

    migrated = 0
    checked = 0
    synced = 0
    updated = 0
    for sid in subscription_ids:
        sub = session.query(Subscription).filter_by(id=sid).first()
        if not sub:
            continue
        checked += 1
        sub_updated = False
        if sub.subscription_type == "monthly":
            migrate_result = migrate_existing_subscription(session, sid)
            migrated += 1
            if migrate_result.get("success") and (
                migrate_result.get("message") == "Абонемент обновлен" or migrate_result.get("changes")
            ):
                sub_updated = True
        if sync_subscription_trainings_remaining(session, sub, reason="after_global_freeze_deactivate"):
            synced += 1
            sub_updated = True
        if sub_updated:
            updated += 1

    if synced > 0:
        session.commit()

    logger.info(
        "GF deactivate: id=%s title=%r checked=%s migrated=%s synced=%s updated=%s",
        gf_id,
        gf.title,
        checked,
        migrated,
        synced,
        updated,
    )

    return {
        "success": True,
        "already_inactive": False,
        "message": "Деактивирована",
        "migrated": migrated,
        "checked": checked,
        "synced": synced,
        "updated_subscriptions": updated,
        "skipped_subscriptions": max(checked - updated, 0),
        "title": gf.title,
        "global_freeze_id": gf_id,
    }