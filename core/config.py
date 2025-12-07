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

    def _validate(self):
        """Проверить конфигурацию"""
        if not self.BOT_TOKEN:
            raise ValueError("❌ BOT_TOKEN не установлен. Добавьте в .env файл или переменные окружения")

        print(f"✅ Конфигурация проверена")
        print(f"   Токен: {self.BOT_TOKEN[:10]}...")
        print(f"   Админ ID: {self.ADMIN_TELEGRAM_ID}")
        print(f"   БД: {self.DATABASE_URL}")

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