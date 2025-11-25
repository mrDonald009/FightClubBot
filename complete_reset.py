import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from database import Database


def complete_reset():
    """Полностью пересоздает базу данных с нуля"""
    # Удаляем файл базы данных если существует
    db_file = 'fightclub.db'
    if os.path.exists(db_file):
        os.remove(db_file)
        print(f"🗑️ Удален старый файл базы данных: {db_file}")

    # Создаем новую базу данных
    db = Database()

    print("🔄 Полное пересоздание базы данных...")

    # Принудительно инициализируем данные
    db.initialize_real_data()

    # Проверяем результат
    workouts = db.get_workouts_by_date("2025-12-01")
    print(f"✅ После полного сброса на 01.12.2025: {len(workouts)} тренировок")

    for workout in workouts:
        print(f"  ⏰ {workout['time']} - {workout['type']}")


if __name__ == '__main__':
    complete_reset()