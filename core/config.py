import os
import sys
from pathlib import Path


class Config:
    """Упрощенная конфигурация приложения"""

    def __init__(self):
        self._load_from_env()
        self._validate()
        self._setup_database()

    def _load_from_env(self):
        """Загрузить переменные окружения"""
        # Пробуем загрузить из .env файла
        try:
            from dotenv import load_dotenv
            load_dotenv()
            print("✅ .env файл загружен")
        except ImportError:
            print("⚠️ python-dotenv не установлен, используем системные переменные")

        self.BOT_TOKEN = os.getenv("BOT_TOKEN")
        self.ADMIN_TELEGRAM_ID = int(os.getenv("ADMIN_TELEGRAM_ID", "26655492"))
        self.DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///database/club.db")
        self.APP_TIMEZONE = os.getenv("APP_TIMEZONE", "Europe/Moscow")
        self.TRAINING_DURATION_MINUTES = int(os.getenv("TRAINING_DURATION_MINUTES", "90"))
        self.ACTIVATION_GRACE_AFTER_START_MINUTES = int(
            os.getenv("ACTIVATION_GRACE_AFTER_START_MINUTES", "30")
        )

    def _validate(self):
        """Проверить конфигурацию"""
        if not self.BOT_TOKEN:
            raise ValueError("❌ BOT_TOKEN не установлен. Добавьте в .env файл или переменные окружения")
        if self.TRAINING_DURATION_MINUTES <= 0:
            raise ValueError("❌ TRAINING_DURATION_MINUTES должен быть положительным числом")
        if self.ACTIVATION_GRACE_AFTER_START_MINUTES < 0:
            raise ValueError("❌ ACTIVATION_GRACE_AFTER_START_MINUTES не может быть отрицательным")

        print(f"✅ Конфигурация проверена")
        print(f"   Токен: {self.BOT_TOKEN[:10]}...")
        print(f"   Админ ID: {self.ADMIN_TELEGRAM_ID}")
        print(f"   БД: {self.DATABASE_URL}")
        print(f"   Таймзона: {self.APP_TIMEZONE}")
        print(f"   Длительность тренировки: {self.TRAINING_DURATION_MINUTES} мин")
        print(f"   Запас на выбор слота после начала: {self.ACTIVATION_GRACE_AFTER_START_MINUTES} мин")

    def _setup_database(self):
        """Настроить базу данных"""
        if self.DATABASE_URL.startswith("sqlite:///"):
            db_path = self.DATABASE_URL.replace("sqlite:///", "")
            db_dir = Path(db_path).parent

            if not db_dir.exists():
                db_dir.mkdir(parents=True, exist_ok=True)
                print(f"✅ Создана папка для БД: {db_dir}")

        # Инициализируем модели
        from database.models import Base, engine
        Base.metadata.create_all(bind=engine)
        print("✅ Таблицы БД созданы/проверены")

        # Применяем легкую миграцию (добавление отсутствующих колонок в существующей БД)
        try:
            from database.migration import migrate_database
            migrate_database()
        except Exception as e:
            print(f"⚠️ Не удалось выполнить миграцию БД: {e}")