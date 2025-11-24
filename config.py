# config.py
import os
import sys
from typing import Dict


class Config:
    def __init__(self):
        # Загружаем переменные из .env файла
        self._load_env_file()

        # Безопасное получение токена из переменных окружения
        self.TOKEN = os.getenv('BOT_TOKEN')

        # Проверяем, что токен установлен
        if not self.TOKEN:
            print("❌ ОШИБКА: Не установлена переменная окружения BOT_TOKEN")
            print("📝 Создайте файл .env и добавьте: BOT_TOKEN=your_actual_bot_token")
            print("💡 Или установите переменную окружения:")
            print("   Windows: set BOT_TOKEN=your_token")
            print("   Linux/Mac: export BOT_TOKEN=your_token")
            sys.exit(1)

        # Проверяем, что токен не является примером
        example_tokens = ['859514', '123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11', 'your_bot_token_here']
        if self.TOKEN in example_tokens:
            print("❌ ОШИБКА: Используется пример токена, а не реальный")
            print("💡 Замените токен в .env на ваш настоящий токен от @BotFather")
            sys.exit(1)

        # Конфигурация зала
        self.GYM_NAME = "FightClubManager"
        self.GYM_ADDRESS = "г. Люберцы, ул. 8 Марта, д. 20"
        self.GYM_PHONE = "+7 (965) 229-64-06"
        self.ADMIN_CONTACT = "@Zimin03"

        # Категории тренировок
        self.WORKOUT_CATEGORIES = {
            "Дети": "👶 Детские группы",
            "Взрослые": "👨‍🦰 Взрослые группы",
            "Индивидуально": "🎯 Индивидуальные тренировки",
            "Комbo": "🔥 Комбо-абонементы"
        }

        # Настройки базы данных
        self.DB_PATH = 'fightclub.db'
        self.BACKUP_PATH = 'backups/'

        # Настройки бота
        self.MAX_BOOKINGS_PER_USER = 3
        self.CANCELLATION_HOURS = 2

        # Текстовые константы
        self.MESSAGES = {
            'welcome': """💪 *Добро пожаловать в {gym_name}!*

Я — ваш цифровой помощник в мире единоборств! Выберите нужный раздел:""",

            'workout_booked': """🎉 *Отлично! Вы записаны на тренировку!*

📋 *Что дальше:*
• Придите за 10-15 минут до начала
• Возьмите сменную обувь
• Сообщите тренеру о записи через бота

💡 *Помните:* 
Первая тренировка - *БЕСПЛАТНО!*

🏋️ *Готовьтесь к тренировке и ждем вас в зале!*"""
        }

    def _load_env_file(self):
        """Загружает переменные из .env файла"""
        try:
            env_path = '.env'
            if os.path.exists(env_path):
                print(f"📁 Загружаем переменные из {env_path}")
                with open(env_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith('#') and '=' in line:
                            key, value = line.split('=', 1)
                            os.environ[key] = value
                            # Скрываем значение токена в логах
                            if 'TOKEN' in key:
                                print(f"   ✅ {key}=***")
                            else:
                                print(f"   ✅ {key}={value}")
            else:
                print("⚠️ Файл .env не найден, используем переменные окружения")
        except Exception as e:
            print(f"❌ Ошибка загрузки .env файла: {e}")


# Создаем экземпляр конфигурации
config = Config()