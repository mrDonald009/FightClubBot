"""Главный файл для запуска Telegram-бота FightClubBot."""
import logging
import sys
from pathlib import Path

# Windows/PowerShell часто падает на emoji в выводе (cp1251/cp866).
# Переключаем stdout/stderr на UTF-8 и включаем замену символов вместо падения.
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Добавляем текущую директорию в путь для импортов
sys.path.append(str(Path(__file__).parent))

# Настройка логирования перед импортами
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('bot.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

# Импорты из новой архитектуры
try:
    from core.config import Config
    from core.application import ApplicationFactory, HandlerRegistrar
    from core.startup import initialize_app, setup_scheduled_jobs
    from handlers.router import register_all_handlers
    
    logger.info("✅ Загружена новая архитектура")
except ImportError as e:
    logger.error(f"❌ Ошибка импорта: {e}", exc_info=True)
    logger.error("⚠️ Проверьте наличие всех необходимых модулей:")
    logger.error("   - core/config.py")
    logger.error("   - core/application.py")
    logger.error("   - core/startup.py")
    logger.error("   - handlers/router.py")
    sys.exit(1)


def main() -> None:
    """Запуск бота с новой масштабируемой архитектурой."""
    try:
        logger.info("=" * 50)
        logger.info("🚀 ЗАПУСК FIGHTCLUBBOT (МАСШТАБИРУЕМАЯ АРХИТЕКТУРА)")
        logger.info("=" * 50)

        # Загружаем конфигурацию
        config = Config()
        logger.info("✅ Конфигурация загружена")

        # Инициализация приложения (проверка тренеров, абонементов и т.д.)
        initialize_app(config)

        # Создаем приложение
        application = ApplicationFactory.create(config)
        logger.info("✅ Приложение Telegram создано")

        # Регистрируем обработчики
        registrar = HandlerRegistrar()
        register_all_handlers(registrar)
        ApplicationFactory.setup_application(application, registrar)
        logger.info("✅ Все обработчики зарегистрированы")

        # Плановые read-only проверки/оповещения
        setup_scheduled_jobs(application, config)

        # Запускаем бота
        logger.info("=" * 50)
        logger.info("🤖 БОТ ЗАПУСКАЕТСЯ...")
        logger.info("=" * 50)
        logger.info("ℹ️  Логи будут сохраняться в bot.log")
        logger.info("ℹ️  Для остановки нажмите Ctrl+C")
        logger.info("=" * 50)

        application.run_polling()

    except KeyboardInterrupt:
        logger.info("⏹️  Бот остановлен пользователем")
    except Exception as e:
        logger.critical(f"❌ Критическая ошибка при запуске бота: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()

