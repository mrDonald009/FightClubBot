import datetime
import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from database import Database


def fix_schedule_01_dec():
    """Исправляет расписание на 01 декабря 2025 с правильными ID"""
    db = Database()

    target_date = datetime.datetime(2025, 12, 1).date()

    print(f"🔧 Исправление расписания на {target_date.strftime('%d.%m.%Y')}")

    with db.get_connection() as conn:
        # Удаляем старые некорректные записи
        conn.execute('DELETE FROM schedule WHERE date = ?', (target_date,))

        # Используем правильные ID из проверки:
        # Тайский бокс (взрослые от 15 лет) -> ID 47
        # ММА (взрослые) -> ID 50
        # Тренер по тайскому боксу -> ID 16

        schedule_slots = [
            (47, 16, target_date, '19:00', 15),  # Тайский бокс (взрослые)
            (50, 16, target_date, '20:30', 15)  # ММА (взрослые)
        ]

        # Добавляем слоты в расписание
        for slot in schedule_slots:
            conn.execute(
                'INSERT INTO schedule (workout_type_id, trainer_id, date, time, available_slots) VALUES (?, ?, ?, ?, ?)',
                slot
            )

        print(f"✅ Исправлено {len(schedule_slots)} слотов на 01.12.2025")

        # Проверяем результат через JOIN
        result = conn.execute('''
            SELECT s.date, s.time, wt.name as workout_name, t.name as trainer_name, s.available_slots 
            FROM schedule s 
            JOIN workout_types wt ON s.workout_type_id = wt.id 
            JOIN trainers t ON s.trainer_id = t.id 
            WHERE s.date = ? ORDER BY s.time
        ''', (target_date,)).fetchall()

        print(f"📅 Исправленное расписание на {target_date.strftime('%d.%m.%Y')}:")
        for row in result:
            print(f"  ⏰ {row['time']} - {row['workout_name']}")
            print(f"    👨‍🏫 Тренер: {row['trainer_name']}")
            print(f"    ✅ Свободно мест: {row['available_slots']}")


if __name__ == '__main__':
    fix_schedule_01_dec()