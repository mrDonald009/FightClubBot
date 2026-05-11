"""Модуль для инициализации и стартовых задач приложения."""
import logging
from datetime import time, timedelta
from typing import Set

from telegram.ext import Application

from core.config import Config
from core.database import get_db_session
from database.db_utils import (
    auto_deduct_daily_trainings,
    close_unmarked_attendance_after_grace,
    lock_attendances_for_ended_trainings,
)
from services.user_service import UserService
from services.subscription_service import SubscriptionService
from services.subscription_audit_service import run_subscription_audit, format_audit_report
from utils.time_utils import APP_TZ

logger = logging.getLogger(__name__)

# Вид спорта в БД для списка THAI_COACH_TELEGRAM_IDS
THAI_COACH_SPORT_TYPE = "Тайский Бокс"


def ensure_admin_user(config: Config) -> None:
    """Создать/проверить запись администратора по ADMIN_TELEGRAM_ID."""
    admin_id = getattr(config, "ADMIN_TELEGRAM_ID", None)
    if admin_id is None:
        return
    try:
        with get_db_session() as session:
            admin = UserService.ensure_admin(
                session=session,
                telegram_id=admin_id,
                username="admin",
                first_name="Администратор",
            )
            if admin:
                logger.info(f"✅ Администратор проверен/создан: {admin_id}")
    except Exception as e:
        logger.error(f"❌ Ошибка при проверке администратора: {e}", exc_info=True)


def ensure_coaches_from_merged_env(config: Config) -> None:
    """
    Автосоздание/обновление тренеров из env.

    - merged_thai_coach_telegram_ids (THAI_COACH_TELEGRAM_IDS + THAI_COACH_TELEGRAM_ID) —
      вид спорта всегда «Тайский Бокс».
    - MMA_COACH_TELEGRAM_IDS — всегда MMA; обрабатывается после тайского списка,
      чтобы перезаписать вид спорта, если id ошибочно указан в обоих местах.
    """
    ids = getattr(config, "merged_thai_coach_telegram_ids", None) or []
    mma_ids = getattr(config, "MMA_COACH_TELEGRAM_IDS", None) or []
    if not ids and not mma_ids:
        logger.info(
            "Тренеры: тайский список и MMA_COACH_TELEGRAM_IDS пусты — пропуск"
        )
        return

    admin_id = getattr(config, "ADMIN_TELEGRAM_ID", None)
    first_name_thai = "Тренер Тайский Бокс"

    def _ensure_one(tid: int, sport: str, first_name: str) -> None:
        if admin_id is not None and tid == admin_id:
            logger.error(
                "telegram_id=%s совпадает с ADMIN_TELEGRAM_ID — пропуск автосоздания тренера",
                tid,
            )
            return
        try:
            with get_db_session() as session:
                UserService.ensure_test_coach(
                    session=session,
                    telegram_id=tid,
                    username=f"coach_{tid}",
                    first_name=first_name,
                    sport_type=sport,
                )
            logger.info("✅ Тренер проверен/создан: %s (%s)", tid, sport)
        except ValueError as e:
            logger.warning("Тренер telegram_id=%s не создан: %s", tid, e)
        except Exception as e:
            logger.error(
                "❌ Ошибка при создании тренера telegram_id=%s: %s",
                tid,
                e,
                exc_info=True,
            )

    processed: Set[int] = set()
    for tid in ids:
        if tid in processed:
            continue
        processed.add(tid)
        _ensure_one(tid, THAI_COACH_SPORT_TYPE, first_name_thai)

    for tid in mma_ids:
        if tid in processed:
            logger.info(
                "telegram_id=%s также в списке тайских тренеров — выставляем MMA",
                tid,
            )
        _ensure_one(tid, "MMA", "Тренер ММА")


def check_subscriptions_on_startup() -> int:
    """
    Проверить абонементы при запуске бота.
    
    Returns:
        Количество обновленных абонементов
    """
    try:
        updated_count = SubscriptionService.check_and_update_subscriptions()
        if updated_count > 0:
            logger.info(f"🔄 При запуске обновлено {updated_count} абонементов")
        return updated_count
    except ImportError as e:
        logger.warning(f"⚠️ Не удалось загрузить SubscriptionChecker: {e}")
        logger.info("⚠️ Проверка абонементов будет выполнена при открытии карточек")
        return 0
    except Exception as e:
        logger.error(f"❌ Ошибка при проверке абонементов при запуске: {e}", exc_info=True)
        return 0


def initialize_app(config: Config) -> None:
    """
    Выполнить все инициализационные задачи при запуске.
    
    Args:
        config: Конфигурация приложения
    """
    logger.info("🔧 Выполнение инициализационных задач...")
    
    ensure_admin_user(config)
    ensure_coaches_from_merged_env(config)

    # Проверяем абонементы
    check_subscriptions_on_startup()

    # «Не отмечено» после конца пары → строки attendances в БД (как в UI)
    try:
        with get_db_session() as session:
            lock_attendances_for_ended_trainings(session)
            n_backfill = close_unmarked_attendance_after_grace(session)
        if n_backfill:
            logger.info(
                "🧾 При старте создано записей посещений (close_unmarked): %s",
                n_backfill,
            )
    except Exception as e:
        logger.error(
            "❌ Ошибка close_unmarked_attendance_after_grace при старте: %s",
            e,
            exc_info=True,
        )

    logger.info("✅ Инициализация завершена")


async def _close_unmarked_interval_job(context) -> None:
    """Частое закрытие слотов без отметки тренера (после конца пары)."""
    try:
        with get_db_session() as session:
            n_lock = lock_attendances_for_ended_trainings(session)
            n = close_unmarked_attendance_after_grace(session)
        if n_lock or n:
            logger.info(
                "🧾 close_unmarked (интервал 15 мин): lock=%s, создано записей=%s",
                n_lock,
                n,
            )
    except Exception as e:  # pragma: no cover
        logger.error("❌ Ошибка interval close_unmarked: %s", e, exc_info=True)


async def _daily_attendance_maintenance_job(context) -> None:
    """Авто-списание по расписанию + фиксация в БД без отметки после конца пары."""
    try:
        with get_db_session() as session:
            d0 = auto_deduct_daily_trainings(session)
        with get_db_session() as session:
            lock_attendances_for_ended_trainings(session)
            d1 = close_unmarked_attendance_after_grace(session)
        logger.info(
            "📋 Ежедневное обслуживание посещений: auto_deduct=%s, close_unmarked=%s",
            d0,
            d1,
        )
    except Exception as e:  # pragma: no cover
        logger.error("❌ Ошибка daily attendance maintenance: %s", e, exc_info=True)


async def _daily_subscription_audit_job(context) -> None:
    """Ежесуточный read-only аудит абонементов с отправкой отчета админу."""
    config = context.application.bot_data.get("config")
    admin_id = getattr(config, "ADMIN_TELEGRAM_ID", None) if config else None

    try:
        with get_db_session() as session:
            report = run_subscription_audit(session)
        report_text = format_audit_report(report)

        logger.info(
            "🩺 Daily audit: checked=%s issues=%s",
            report.get("total_subscriptions", 0),
            report.get("issues_total", 0),
        )

        if admin_id:
            await context.bot.send_message(chat_id=admin_id, text=report_text, parse_mode="HTML")
    except Exception as e:  # pragma: no cover
        logger.error(f"❌ Ошибка daily-аудита абонементов: {e}", exc_info=True)


def setup_scheduled_jobs(application: Application, config: Config) -> None:
    """Настроить плановые задачи приложения."""
    application.bot_data["config"] = config
    if not application.job_queue:
        logger.warning("⚠️ JobQueue недоступен: ежедневный аудит не запланирован")
        return

    application.job_queue.run_repeating(
        _close_unmarked_interval_job,
        interval=timedelta(minutes=15),
        first=timedelta(seconds=45),
        name="close_unmarked_attendance_interval",
    )
    logger.info(
        "🗓️ Запланировано закрытие посещений без отметки каждые 15 мин (после конца пары)"
    )

    maintenance_time = time(hour=8, minute=5, tzinfo=APP_TZ)
    application.job_queue.run_daily(
        _daily_attendance_maintenance_job,
        time=maintenance_time,
        name="daily_attendance_maintenance",
    )
    logger.info(
        "🗓️ Запланировано ежедневное обслуживание посещений (08:05 APP_TIMEZONE): "
        "auto_deduct + close_unmarked"
    )

    run_time = time(hour=8, minute=0, tzinfo=APP_TZ)
    application.job_queue.run_daily(
        _daily_subscription_audit_job,
        time=run_time,
        name="daily_subscription_audit",
    )
    logger.info("🗓️ Запланирован ежедневный аудит абонементов (08:00 APP_TIMEZONE)")

