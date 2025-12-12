"""Модуль для инициализации и стартовых задач приложения."""
import logging
from core.config import Config
from core.database import get_db_session
from services.user_service import UserService
from services.subscription_service import SubscriptionService

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
                username="coach_mma",
                first_name="Тренер ММА",
                sport_type="MMA"
            )
            logger.info(f"✅ Тестовый тренер проверен/создан: {test_coach_id}")
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

