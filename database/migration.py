import sys
import os
from pathlib import Path

# Добавляем корневую папку проекта в путь Python
project_root = Path(__file__).parent.parent
sys.path.append(str(project_root))

import sqlite3


def migrate_database():
    """Миграция базы данных для добавления новых полей"""

    # Путь к базе данных
    db_path = "database/club.db"

    # Создаем папку если её нет
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    # Используем sqlite3 напрямую для миграции
    connection = sqlite3.connect(db_path)
    cursor = connection.cursor()

    try:
        print("🔧 Начинаю миграцию базы данных...")

        # Проверяем существование столбцов в таблице athletes
        cursor.execute("PRAGMA table_info(athletes)")
        columns = [row[1] for row in cursor.fetchall()]

        # Добавляем current_subscription_id если его нет
        if 'current_subscription_id' not in columns:
            print("🔧 Добавляю current_subscription_id в таблицу athletes...")
            cursor.execute("ALTER TABLE athletes ADD COLUMN current_subscription_id INTEGER")
            print("✅ current_subscription_id добавлен")

        # Проверяем таблицу subscriptions
        cursor.execute("PRAGMA table_info(subscriptions)")
        columns = [row[1] for row in cursor.fetchall()]

        # Добавляем поля восстановлений если их нет
        if 'total_restored' not in columns:
            print("🔧 Добавляю total_restored в таблицу subscriptions...")
            cursor.execute("ALTER TABLE subscriptions ADD COLUMN total_restored INTEGER DEFAULT 0")
            print("✅ total_restored добавлен")

        if 'restored_this_month' not in columns:
            print("🔧 Добавляю restored_this_month в таблицу subscriptions...")
            cursor.execute("ALTER TABLE subscriptions ADD COLUMN restored_this_month INTEGER DEFAULT 0")
            print("✅ restored_this_month добавлен")

        # Добавляем created_at если его нет (без DEFAULT для SQLite)
        if 'created_at' not in columns:
            print("🔧 Добавляю created_at в таблицу subscriptions...")
            cursor.execute("ALTER TABLE subscriptions ADD COLUMN created_at DATETIME")
            print("✅ created_at добавлен")

            # Обновляем существующие записи текущим временем
            cursor.execute("UPDATE subscriptions SET created_at = datetime('now') WHERE created_at IS NULL")
            print("✅ Обновлены существующие записи в subscriptions")

        # Проверяем таблицу attendances
        cursor.execute("PRAGMA table_info(attendances)")
        columns = [row[1] for row in cursor.fetchall()]

        # Добавляем поля если их нет
        if 'was_restored' not in columns:
            print("🔧 Добавляю was_restored в таблицу attendances...")
            cursor.execute("ALTER TABLE attendances ADD COLUMN was_restored BOOLEAN DEFAULT FALSE")
            print("✅ was_restored добавлен")

        if 'restoration_reason' not in columns:
            print("🔧 Добавляю restoration_reason в таблицу attendances...")
            cursor.execute("ALTER TABLE attendances ADD COLUMN restoration_reason TEXT")
            print("✅ restoration_reason добавлен")

        if 'subscription_id' not in columns:
            print("🔧 Добавляю subscription_id в таблицу attendances...")
            cursor.execute("ALTER TABLE attendances ADD COLUMN subscription_id INTEGER")
            print("✅ subscription_id добавлен")

        # Создаем таблицу restoration_requests если её нет
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS restoration_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                athlete_id INTEGER,
                subscription_id INTEGER,
                missed_dates TEXT,
                restored_count INTEGER,
                reason TEXT,
                notes TEXT,
                restored_by INTEGER,
                restored_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        print("✅ Таблица restoration_requests создана")

        # Проверяем таблицу trainings
        cursor.execute("PRAGMA table_info(trainings)")
        columns = [row[1] for row in cursor.fetchall()]

        # Добавляем coach_id если его нет
        if 'coach_id' not in columns:
            print("🔧 Добавляю coach_id в таблицу trainings...")
            cursor.execute("ALTER TABLE trainings ADD COLUMN coach_id INTEGER")
            print("✅ coach_id добавлен в trainings")

        connection.commit()
        print("🎉 Миграция завершена успешно!")

    except Exception as e:
        print(f"❌ Ошибка при миграции: {e}")
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    migrate_database()