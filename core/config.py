import os
from pathlib import Path


class Config:
    """Упрощенная конфигурация приложения"""

    def __init__(self):
        self._load_from_env()
        self._validate()
        self._setup_database()

    def _load_from_env(self):
        """Загрузить переменные окружения"""
        # Пробуем загрузить из файла окружения.
        # По умолчанию используем .env, но можно переопределить:
        # ENV_FILE=.env.dev python bot.py
        env_file = os.getenv("ENV_FILE", ".env")
        try:
            from dotenv import load_dotenv
            loaded = load_dotenv(dotenv_path=env_file)
            if loaded:
                print(f"✅ Загружен env файл: {env_file}")
            else:
                print(f"ℹ️ Env файл не найден: {env_file}, используем системные переменные")
        except ImportError:
            print("⚠️ python-dotenv не установлен, используем системные переменные")

        self.BOT_TOKEN = os.getenv("BOT_TOKEN")
        self.ADMIN_TELEGRAM_ID = int(os.getenv("ADMIN_TELEGRAM_ID", "26655492"))
        _thai_raw = os.getenv("THAI_COACH_TELEGRAM_ID", "").strip()
        self.THAI_COACH_TELEGRAM_ID = int(_thai_raw) if _thai_raw else None
        _coaches_raw = os.getenv("COACH_TELEGRAM_IDS", "").strip()
        self.COACH_TELEGRAM_IDS: list[int] = []
        for part in _coaches_raw.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                self.COACH_TELEGRAM_IDS.append(int(part))
            except ValueError:
                print(f"⚠️ Пропуск невалидного id в COACH_TELEGRAM_IDS: {part!r}")
        self.COACH_DEFAULT_SPORT_TYPE = (
            os.getenv("COACH_DEFAULT_SPORT_TYPE", "MMA").strip() or "MMA"
        )
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
        print(f"   Администратор (ADMIN_TELEGRAM_ID): {self.ADMIN_TELEGRAM_ID}")
        if self.THAI_COACH_TELEGRAM_ID is not None:
            print(f"   Тренер Тайский бокс (THAI_COACH_TELEGRAM_ID): {self.THAI_COACH_TELEGRAM_ID}")
        else:
            print("   Тренер Тайский бокс: не задан (THAI_COACH_TELEGRAM_ID) — автосоздание отключено")
        if self.COACH_TELEGRAM_IDS:
            print(
                f"   Тренеры из COACH_TELEGRAM_IDS ({self.COACH_DEFAULT_SPORT_TYPE}): "
                f"{self.COACH_TELEGRAM_IDS}"
            )
        else:
            print("   COACH_TELEGRAM_IDS: не задан — автосоздание тренеров из списка отключено")
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