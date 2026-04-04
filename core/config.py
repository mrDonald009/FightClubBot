import os
from pathlib import Path
from typing import List, Optional, Set


def merge_coach_telegram_ids(
    coach_telegram_ids: List[int],
    thai_coach_telegram_id: Optional[int],
) -> List[int]:
    """
    Единый порядок id тренеров для автосоздания при старте.
    Сначала все из COACH_TELEGRAM_IDS (как в env), затем THAI_COACH_TELEGRAM_ID
    в начало списка, если задан и ещё не встречался (обратная совместимость).
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


def resolve_delegate_coach_telegram_id(
    admin_delegate_explicit: Optional[int],
    thai_coach_telegram_id: Optional[int],
    merged_coach_ids: List[int],
) -> Optional[int]:
    """
    Какой тренер — шаблон при добавлении спортсмена админом.
    Явный ADMIN_DELEGATE_COACH_TELEGRAM_ID > устар. THAI > первый в объединённом списке.
    """
    if admin_delegate_explicit is not None:
        return admin_delegate_explicit
    if thai_coach_telegram_id is not None:
        return thai_coach_telegram_id
    if merged_coach_ids:
        return merged_coach_ids[0]
    return None


def read_delegate_coach_telegram_id_from_env() -> Optional[int]:
    """Если нет application.bot_data['config'] (тесты): тот же алгоритм, что у Config."""
    coach_ids: List[int] = []
    for part in os.getenv("COACH_TELEGRAM_IDS", "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            coach_ids.append(int(part))
        except ValueError:
            print(f"⚠️ Пропуск невалидного id в COACH_TELEGRAM_IDS: {part!r}")
    thai_raw = os.getenv("THAI_COACH_TELEGRAM_ID", "").strip()
    thai_id = int(thai_raw) if thai_raw else None
    del_raw = os.getenv("ADMIN_DELEGATE_COACH_TELEGRAM_ID", "").strip()
    explicit = int(del_raw) if del_raw else None
    merged = merge_coach_telegram_ids(coach_ids, thai_id)
    return resolve_delegate_coach_telegram_id(explicit, thai_id, merged)


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
        _delegate_raw = os.getenv("ADMIN_DELEGATE_COACH_TELEGRAM_ID", "").strip()
        self._admin_delegate_explicit = int(_delegate_raw) if _delegate_raw else None
        self.merged_coach_telegram_ids = merge_coach_telegram_ids(
            list(self.COACH_TELEGRAM_IDS), self.THAI_COACH_TELEGRAM_ID
        )
        self.delegate_coach_telegram_id = resolve_delegate_coach_telegram_id(
            self._admin_delegate_explicit,
            self.THAI_COACH_TELEGRAM_ID,
            self.merged_coach_telegram_ids,
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
        if self.merged_coach_telegram_ids:
            print(
                f"   Тренеры (автосоздание, {self.COACH_DEFAULT_SPORT_TYPE}): "
                f"{self.merged_coach_telegram_ids}"
            )
        else:
            print(
                "   Тренеры: COACH_TELEGRAM_IDS и THAI_COACH_TELEGRAM_ID пусты — автосоздание отключено"
            )
        if self.THAI_COACH_TELEGRAM_ID is not None and self.COACH_TELEGRAM_IDS:
            print(
                "   ℹ️  THAI_COACH_TELEGRAM_ID устарел: задайте все id в COACH_TELEGRAM_IDS "
                "или оставьте THAI только для обратной совместимости."
            )
        if self.delegate_coach_telegram_id is not None:
            print(
                f"   Делегат админа при добавлении спортсмена (telegram_id): "
                f"{self.delegate_coach_telegram_id}"
            )
        else:
            print("   Делегат админа: не задан (нет ни одного id тренера в env)")
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