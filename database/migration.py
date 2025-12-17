import sys
import os
from pathlib import Path

# Windows/PowerShell часто падает на emoji в выводе (cp1251/cp866).
# Переключаем stdout/stderr на UTF-8 и включаем замену символов вместо падения.
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

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

        # Добавляем birth_date если его нет
        if 'birth_date' not in columns:
            print("🔧 Добавляю birth_date в таблицу athletes...")
            cursor.execute("ALTER TABLE athletes ADD COLUMN birth_date DATETIME")
            print("✅ birth_date добавлен")

        # Добавляем subscription_id если его нет (связь один-к-одному)
        if 'subscription_id' not in columns:
            print("🔧 Добавляю subscription_id в таблицу athletes...")
            cursor.execute("ALTER TABLE athletes ADD COLUMN subscription_id INTEGER")
            print("✅ subscription_id добавлен")
            
            # Если есть current_subscription_id, копируем данные
            if 'current_subscription_id' in columns:
                print("🔧 Копирую данные из current_subscription_id в subscription_id...")
                cursor.execute("UPDATE athletes SET subscription_id = current_subscription_id WHERE current_subscription_id IS NOT NULL")
                print("✅ Данные скопированы")
        
        # Добавляем current_subscription_id если его нет (для обратной совместимости)
        if 'current_subscription_id' not in columns:
            print("🔧 Добавляю current_subscription_id в таблицу athletes (обратная совместимость)...")
            cursor.execute("ALTER TABLE athletes ADD COLUMN current_subscription_id INTEGER")
            print("✅ current_subscription_id добавлен")
            
            # Копируем данные из subscription_id
            if 'subscription_id' in columns:
                print("🔧 Копирую данные из subscription_id в current_subscription_id...")
                cursor.execute("UPDATE athletes SET current_subscription_id = subscription_id WHERE subscription_id IS NOT NULL")
                print("✅ Данные скопированы")

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
        
        # Добавляем sport_type если его нет
        if 'sport_type' not in columns:
            print("🔧 Добавляю sport_type в таблицу subscriptions...")
            cursor.execute("ALTER TABLE subscriptions ADD COLUMN sport_type VARCHAR(50)")
            print("✅ sport_type добавлен")
            
            # Обновляем существующие записи sport_type из связанного спортсмена
            cursor.execute("""
                UPDATE subscriptions 
                SET sport_type = (
                    SELECT athletes.sport_type 
                    FROM athletes 
                    WHERE athletes.id = subscriptions.athlete_id
                )
                WHERE sport_type IS NULL
            """)
            print("✅ Обновлены существующие записи в subscriptions с sport_type из спортсменов")

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