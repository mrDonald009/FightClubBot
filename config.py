import os
from dotenv import load_dotenv

# Загружаем переменные из .env файла
load_dotenv()


class Config:
    def __init__(self):
        self.BOT_TOKEN = self._get_required_env('BOT_TOKEN')
        self.ADMIN_TELEGRAM_ID = int(self._get_required_env('ADMIN_TELEGRAM_ID'))
        self.DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///club.db')

    def _get_required_env(self, key):
        value = os.getenv(key)
        if not value:
            raise ValueError(f"❌ {key} не найден в .env файле!")
        return value


# Создаем экземпляр конфигурации
try:
    config = Config()
    print("✅ Конфигурация загружена успешно")
except ValueError as e:
    print(f"❌ Ошибка конфигурации: {e}")
    print("📝 Создайте файл .env с необходимыми переменными")
    exit(1)