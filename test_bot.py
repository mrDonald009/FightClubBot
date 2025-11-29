import requests
import json
from config import config


def test_bot_connection():
    """Проверка подключения к боту"""
    url = f"https://api.telegram.org/bot{config.BOT_TOKEN}/getMe"
    response = requests.get(url)

    if response.status_code == 200:
        data = response.json()
        print("✅ Бот подключен успешно!")
        print(f"🤖 Имя бота: {data['result']['first_name']}")
        print(f"🔗 Username: @{data['result']['username']}")
        return True
    else:
        print("❌ Ошибка подключения к боту")
        return False


def test_db_connection():
    """Проверка подключения к базе данных"""
    try:
        from database.models import Session
        session = Session()
        from database.models import User
        users_count = session.query(User).count()
        print(f"✅ База данных подключена, пользователей: {users_count}")

        # Покажем всех пользователей
        users = session.query(User).all()
        print("\n📋 Пользователи в базе:")
        for user in users:
            print(f"  - {user.first_name} (ID: {user.telegram_id}, Роль: {user.role})")

        session.close()
        return True
    except Exception as e:
        print(f"❌ Ошибка базы данных: {e}")
        return False


if __name__ == "__main__":
    print("🔍 Тестирование системы...")
    test_bot_connection()
    test_db_connection()