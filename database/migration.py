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

        # Нормализация age_group в athletes (безопасно для существующих данных)
        cursor.execute("""
            UPDATE athletes
            SET age_group = CASE
                WHEN age_group IN ('children', 'adults') THEN age_group
                WHEN age_group IN ('Детская', 'детская', 'child', 'kids') THEN 'children'
                WHEN age_group IN ('Взрослая', 'взрослая', 'adult') THEN 'adults'
                ELSE age_group
            END
            WHERE age_group IS NOT NULL
        """)
        print("✅ Нормализованы значения age_group (если были legacy-значения)")

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

        # Добавляем поля заморозки если их нет
        if 'is_frozen' not in columns:
            print("🔧 Добавляю is_frozen в таблицу subscriptions...")
            cursor.execute("ALTER TABLE subscriptions ADD COLUMN is_frozen BOOLEAN DEFAULT FALSE")
            print("✅ is_frozen добавлен")

        if 'frozen_from' not in columns:
            print("🔧 Добавляю frozen_from в таблицу subscriptions...")
            cursor.execute("ALTER TABLE subscriptions ADD COLUMN frozen_from DATETIME")
            print("✅ frozen_from добавлен")

        if 'frozen_until' not in columns:
            print("🔧 Добавляю frozen_until в таблицу subscriptions...")
            cursor.execute("ALTER TABLE subscriptions ADD COLUMN frozen_until DATETIME")
            print("✅ frozen_until добавлен")

        if 'frozen_count' not in columns:
            print("🔧 Добавляю frozen_count в таблицу subscriptions...")
            cursor.execute("ALTER TABLE subscriptions ADD COLUMN frozen_count INTEGER DEFAULT 0")
            print("✅ frozen_count добавлен")

        if 'frozen_days_total' not in columns:
            print("🔧 Добавляю frozen_days_total в таблицу subscriptions...")
            cursor.execute("ALTER TABLE subscriptions ADD COLUMN frozen_days_total INTEGER DEFAULT 0")
            print("✅ frozen_days_total добавлен")

        if 'frozen_training_days_total' not in columns:
            print("🔧 Добавляю frozen_training_days_total в таблицу subscriptions...")
            cursor.execute("ALTER TABLE subscriptions ADD COLUMN frozen_training_days_total INTEGER DEFAULT 0")
            print("✅ frozen_training_days_total добавлен")

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

        # Нормализация subscription_type в subscriptions (legacy значения -> текущие)
        cursor.execute("""
            UPDATE subscriptions
            SET subscription_type = CASE
                WHEN subscription_type IN ('monthly', 'single') THEN subscription_type
                WHEN subscription_type IN ('Месячный', 'месячный', 'month') THEN 'monthly'
                WHEN subscription_type IN ('Разовый', 'разовый', 'one_time', 'single_use') THEN 'single'
                ELSE subscription_type
            END
            WHERE subscription_type IS NOT NULL
        """)
        print("✅ Нормализованы значения subscription_type (если были legacy-значения)")

        # Защита от некорректных остатков: неотрицательные и не больше total
        cursor.execute("""
            UPDATE subscriptions
            SET trainings_total = 0
            WHERE trainings_total IS NOT NULL AND trainings_total < 0
        """)
        cursor.execute("""
            UPDATE subscriptions
            SET trainings_remaining = 0
            WHERE trainings_remaining IS NOT NULL AND trainings_remaining < 0
        """)
        cursor.execute("""
            UPDATE subscriptions
            SET trainings_remaining = trainings_total
            WHERE trainings_total IS NOT NULL
              AND trainings_remaining IS NOT NULL
              AND trainings_remaining > trainings_total
        """)
        print("✅ Нормализованы trainings_total/trainings_remaining")

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

        # Массовые заморозки клуба
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS global_freezes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title VARCHAR(200) NOT NULL,
                start_date DATETIME NOT NULL,
                end_date DATETIME NOT NULL,
                is_active BOOLEAN DEFAULT TRUE,
                created_by INTEGER,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        print("✅ Таблица global_freezes создана")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS global_freeze_applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                global_freeze_id INTEGER NOT NULL,
                subscription_id INTEGER NOT NULL,
                training_days_added INTEGER DEFAULT 0,
                old_end_date DATETIME,
                new_end_date DATETIME,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        print("✅ Таблица global_freeze_applications создана")

        # --- СТРУКТУРНЫЕ ОГРАНИЧЕНИЯ (SQLite UNIQUE INDEX) ---
        # Правило домена: у одного спортсмена (athlete_id) один абонемент.
        # В SQLite добавляем это через UNIQUE INDEX.

        cursor.execute("""
            SELECT athlete_id, COUNT(*) as cnt
            FROM subscriptions
            GROUP BY athlete_id
            HAVING COUNT(*) > 1
        """)
        duplicates = cursor.fetchall()
        if duplicates:
            print(f"⚠️ Найдены дубли subscriptions по athlete_id. UNIQUE athlete_id не включаем. Пример: {duplicates[0]}")
        else:
            cursor.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS uq_subscriptions_athlete_id
                ON subscriptions (athlete_id)
            """)
            print("✅ UNIQUE: uq_subscriptions_athlete_id создана")

        # Защита от дублей посещений: один athlete не должен иметь более одной записи на одну training.
        cursor.execute("""
            SELECT athlete_id, training_id, COUNT(*) as cnt
            FROM attendances
            GROUP BY athlete_id, training_id
            HAVING COUNT(*) > 1
        """)
        attendance_dups = cursor.fetchall()
        if attendance_dups:
            print(f"⚠️ Найдены дубли attendances по (athlete_id, training_id). UNIQUE не включаем. Пример: {attendance_dups[0]}")
        else:
            cursor.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS uq_attendances_athlete_training
                ON attendances (athlete_id, training_id)
            """)
            print("✅ UNIQUE: uq_attendances_athlete_training создана")

        # Индексы для частых проверок статуса абонементов
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS ix_subscriptions_active_end
            ON subscriptions (is_active, end_date)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS ix_subscriptions_athlete_active
            ON subscriptions (athlete_id, is_active)
        """)
        print("✅ Индексы subscriptions (active/end, athlete/active) созданы")

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS ix_global_freezes_active_range
            ON global_freezes (is_active, start_date, end_date)
        """)
        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_global_freeze_subscription
            ON global_freeze_applications (global_freeze_id, subscription_id)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS ix_gfa_subscription
            ON global_freeze_applications (subscription_id)
        """)
        print("✅ Индексы global_freezes/global_freeze_applications созданы")

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