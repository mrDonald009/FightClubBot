import os
from pathlib import Path
from typing import List, Optional, Set


def merge_coach_telegram_ids(
    coach_telegram_ids: List[int],
    thai_coach_telegram_id: Optional[int],
) -> List[int]:
    """
    Порядок id тренеров тайского бокса при старте.
    Сначала все из THAI_COACH_TELEGRAM_IDS, затем THAI_COACH_TELEGRAM_ID (один id)
    в начало, если задан и ещё не в списке.
    """
    seen: Set[int] = set()
    merged: List[int] = []
    for tid in coach_telegram_ids:
        if tid not in seen:
            seen.add(tid)
            merged.append(tid)
    if thai_coach_telegram_id is not None and thai_coach_telegram_id not in seen:
        merged.insert(0, thai_coach_telegram_id)
    return merged


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
        _admin_raw = os.getenv("ADMIN_TELEGRAM_ID", "").strip()
        self.ADMIN_TELEGRAM_ID = int(_admin_raw) if _admin_raw else None
        _thai_raw = os.getenv("THAI_COACH_TELEGRAM_ID", "").strip()
        self.THAI_COACH_TELEGRAM_ID = int(_thai_raw) if _thai_raw else None

        def _parse_id_list(raw: str, label: str) -> list[int]:
            out: list[int] = []
            for part in raw.split(","):
                part = part.strip()
                if not part:
                    continue
                try:
                    out.append(int(part))
                except ValueError:
                    print(f"⚠️ Пропуск невалидного id в {label}: {part!r}")
            return out

        _thai_list_raw = os.getenv("THAI_COACH_TELEGRAM_IDS", "").strip()
        self.THAI_COACH_TELEGRAM_IDS = _parse_id_list(_thai_list_raw, "THAI_COACH_TELEGRAM_IDS")

        _mma_coaches_raw = os.getenv("MMA_COACH_TELEGRAM_IDS", "").strip()
        self.MMA_COACH_TELEGRAM_IDS: list[int] = []
        for part in _mma_coaches_raw.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                self.MMA_COACH_TELEGRAM_IDS.append(int(part))
            except ValueError:
                print(f"⚠️ Пропуск невалидного id в MMA_COACH_TELEGRAM_IDS: {part!r}")
        self.merged_thai_coach_telegram_ids = merge_coach_telegram_ids(
            list(self.THAI_COACH_TELEGRAM_IDS), self.THAI_COACH_TELEGRAM_ID
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
        if self.ADMIN_TELEGRAM_ID is not None:
            print(f"   Администратор (ADMIN_TELEGRAM_ID): {self.ADMIN_TELEGRAM_ID}")
        else:
            print("   Администратор: не задан (ADMIN_TELEGRAM_ID) — автосоздание админа отключено")
        if self.merged_thai_coach_telegram_ids:
            print(
                f"   Тренеры тайского бокса (Тайский Бокс): {self.merged_thai_coach_telegram_ids}"
            )
        else:
            print(
                "   Тренеры тайского бокса: THAI_COACH_TELEGRAM_IDS и THAI_COACH_TELEGRAM_ID пусты"
            )
        if self.MMA_COACH_TELEGRAM_IDS:
            print(f"   Тренеры ММА (MMA_COACH_TELEGRAM_IDS): {self.MMA_COACH_TELEGRAM_IDS}")
        if not self.merged_thai_coach_telegram_ids and not self.MMA_COACH_TELEGRAM_IDS:
            print(
                "   Автосоздание тренеров выключено (нет id ни в одном списке)"
            )
        if self.THAI_COACH_TELEGRAM_ID is not None and self.THAI_COACH_TELEGRAM_IDS:
            print(
                "   ℹ️  THAI_COACH_TELEGRAM_ID (один id) при отсутствии в списке добавляется в начало "
                "тайских тренеров; все id удобнее задать в THAI_COACH_TELEGRAM_IDS."
            )
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