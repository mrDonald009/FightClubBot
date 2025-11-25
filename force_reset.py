import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from database import Database


def force_reset():
    """Принудительно переинициализирует базу данных с расписанием на 30 дней"""
    db = Database()

    print("🔄 Принудительная переинициализация базы данных на 30 дней...")

    # Вызываем инициализацию реальных данных
    db.initialize_real_data()

    # Проверяем расписание на 01.12.2025
    workouts = db.get_workouts_by_date("2025-12-01")
    print(f"✅ После переинициализации на 01.12.2025: {len(workouts)} тренировок")

    for workout in workouts:
        print(f"  ⏰ {workout['time']} - {workout['type']}")


if __name__ == '__main__':
    force_reset()