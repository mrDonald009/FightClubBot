import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from database import Database


def check_schedule():
    db = Database()
    date_to_check = "2025-12-01"

    print(f"🔍 Проверка расписания на {date_to_check}")

    with db.get_connection() as conn:
        # Проверим расписание в таблице schedule
        schedule_rows = conn.execute(
            'SELECT s.id, s.date, s.time, wt.name as workout_name, t.name as trainer, s.available_slots '
            'FROM schedule s '
            'LEFT JOIN workout_types wt ON s.workout_type_id = wt.id '
            'LEFT JOIN trainers t ON s.trainer_id = t.id '
            'WHERE s.date = ?',
            (date_to_check,)
        ).fetchall()

        print(f"📅 Найдено записей в расписании: {len(schedule_rows)}")
        for row in schedule_rows:
            print(f"  ⏰ {row['time']} - {row['workout_name']}")
            print(f"    👨‍🏫 Тренер: {row['trainer']}")
            print(f"    ✅ Свободно мест: {row['available_slots']}")

        # Проверим через метод get_workouts_by_date
        workouts = db.get_workouts_by_date(date_to_check)
        print(f"🎯 Метод get_workouts_by_date вернул: {len(workouts)} тренировок")
        for workout in workouts:
            print(f"  ⏰ {workout['time']} - {workout['type']}")


if __name__ == '__main__':
    check_schedule()