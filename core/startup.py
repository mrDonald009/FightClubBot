"""Модуль для инициализации и стартовых задач приложения."""
import logging
from datetime import time

from telegram.ext import Application

from core.config import Config
from core.database import get_db_session
from services.user_service import UserService
from services.subscription_service import SubscriptionService
from services.subscription_audit_service import run_subscription_audit, format_audit_report
from utils.time_utils import APP_TZ

logger = logging.getLogger(__name__)


def ensure_test_coach(config: Config) -> None:
    """
    Убедиться, что тестовый тренер существует с правильными параметрами.
    
    Args:
        config: Конфигурация приложения
    """
    test_coach_id = getattr(config, 'ADMIN_TELEGRAM_ID', 26655492)
    
    try:
        with get_db_session() as session:
            UserService.ensure_test_coach(
                session=session,
                telegram_id=test_coach_id,
                username="coach_thai",
                first_name="Тренер Тайский Бокс",
                sport_type="Тайский Бокс"
            )
            logger.info(f"✅ Тестовый тренер проверен/создан: {test_coach_id} (Тайский Бокс)")
    except Exception as e:
        logger.error(f"❌ Ошибка при проверке тренера: {e}", exc_info=True)


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
    
    # Проверяем тестового тренера
    ensure_test_coach(config)
    
    # Проверяем абонементы
    check_subscriptions_on_startup()
    
    logger.info("✅ Инициализация завершена")


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

    run_time = time(hour=8, minute=0, tzinfo=APP_TZ)
    application.job_queue.run_daily(
        _daily_subscription_audit_job,
        time=run_time,
        name="daily_subscription_audit",
    )
    logger.info("🗓️ Запланирован ежедневный аудит абонементов (08:00 APP_TIMEZONE)")

