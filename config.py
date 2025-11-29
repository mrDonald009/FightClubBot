import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    print("❌ Модуль python-dotenv не установлен!")
    print("Установите: pip install python-dotenv")
    sys.exit(1)

# Загружаем переменные из .env файла
load_dotenv()


class Config:
    def __init__(self):
        try:
            self.BOT_TOKEN = self._get_required_env('BOT_TOKEN')
            self.ADMIN_TELEGRAM_ID = int(self._get_required_env('ADMIN_TELEGRAM_ID'))
            self.DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///database/club.db')

            # Создаем папку для базы данных если её нет
            db_path = self.DATABASE_URL.replace('sqlite:///', '')
            db_dir = Path(db_path).parent
            if not db_dir.exists():
                db_dir.mkdir(parents=True)
                print(f"✅ Создана папка: {db_dir}")

        except Exception as e:
            print(f"❌ Ошибка конфигурации: {e}")
            sys.exit(1)

    def _get_required_env(self, key):
        value = os.getenv(key)
        if not value:
            raise ValueError(f"❌ {key} не найден в .env файле!")
        return value


# Создаем экземпляр конфигурации
try:
    config = Config()
    print("✅ Конфигурация загружена успешно")
    print(f"📁 База данных: {config.DATABASE_URL}")
except Exception as e:
    print(f"❌ Ошибка при загрузке конфигурации: {e}")
    sys.exit(1)