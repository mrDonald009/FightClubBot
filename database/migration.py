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

from sqlalchemy.engine.url import make_url

from utils.discipline_keys import default_group_key_for_subscription_sport


def _sqlite_database_path_for_migration() -> str:
    """Тот же файл SQLite, что и в DATABASE_URL (для dev/prod с разными путями)."""
    raw = os.getenv("DATABASE_URL", "sqlite:///database/club.db")
    try:
        u = make_url(raw)
        if u.drivername != "sqlite":
            return "database/club.db"
        db = u.database
        if not db or db == ":memory:":
            return "database/club.db"
        return db
    except Exception:
        return "database/club.db"


def _seed_default_subscription_tariffs(cursor) -> None:
    """Тарифы по умолчанию: MMA и Тайский Бокс (как в TRAINING_SCHEDULE).

    Месячный абонемент = групповые занятия; разовый = одно разовое занятие.
    Одна сумма на взрослую и детскую группу (поле age_group в тарифах не используется).

    Вставка только если для пары (вид спорта, tariff_kind) ещё нет ни одной строки —
    чтобы не затирать ручные правки при каждом запуске миграции.
    """
    note = "Групповые / разовое; взрослая и детская группа (единая цена)"
    defaults = [
        ("MMA", "subscription_monthly", 6500),
        ("MMA", "subscription_single", 550),
        ("MMA", "individual_training", 3000),
        ("Тайский Бокс", "subscription_monthly", 6500),
        ("Тайский Бокс", "subscription_single", 550),
        ("Тайский Бокс", "individual_training", 3000),
    ]
    for sport, kind, amount in defaults:
        cursor.execute(
            """
            SELECT COUNT(*) FROM subscription_tariffs
            WHERE sport_type_name = ? AND tariff_kind = ?
            """,
            (sport, kind),
        )
        if (cursor.fetchone() or (0,))[0] > 0:
            continue
        cursor.execute(
            """
            INSERT INTO subscription_tariffs
            (sport_type_name, tariff_kind, amount_rubles, is_active, note)
            VALUES (?, ?, ?, 1, ?)
            """,
            (sport, kind, amount, note),
        )
    print("✅ Тарифы по умолчанию (MMA / Тайский Бокс): проверены при необходимости добавлены")


def migrate_database():
    """Миграция базы данных для добавления новых полей"""

    db_path = _sqlite_database_path_for_migration()

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
                WHEN subscription_type IN ('monthly', 'single', 'individual') THEN subscription_type
                WHEN subscription_type IN ('Месячный', 'месячный', 'month') THEN 'monthly'
                WHEN subscription_type IN ('Разовый', 'разовый', 'one_time', 'single_use') THEN 'single'
                WHEN subscription_type IN ('Индивидуальный', 'индивидуальный') THEN 'individual'
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

        # Мульти-абонементы: направление (discipline_key) и ответственный тренер
        cursor.execute("PRAGMA table_info(subscriptions)")
        columns = [row[1] for row in cursor.fetchall()]

        if "discipline_key" not in columns:
            print("🔧 Добавляю discipline_key в таблицу subscriptions...")
            cursor.execute("ALTER TABLE subscriptions ADD COLUMN discipline_key VARCHAR(64)")
            print("✅ discipline_key добавлен")

        if "responsible_coach_id" not in columns:
            print("🔧 Добавляю responsible_coach_id в таблицу subscriptions...")
            cursor.execute("ALTER TABLE subscriptions ADD COLUMN responsible_coach_id INTEGER")
            print("✅ responsible_coach_id добавлен")

        cursor.execute(
            """
            SELECT s.id, s.sport_type, a.sport_type
            FROM subscriptions s
            LEFT JOIN athletes a ON a.id = s.athlete_id
            """
        )
        for sid, st_sub, st_ath in cursor.fetchall():
            cursor.execute("SELECT discipline_key FROM subscriptions WHERE id = ?", (sid,))
            current = cursor.fetchone()[0]
            if current is None or current == "":
                st = (st_sub or st_ath or "").strip()
                dk = default_group_key_for_subscription_sport(st or "unknown")
                cursor.execute(
                    "UPDATE subscriptions SET discipline_key = ? WHERE id = ?",
                    (dk, sid),
                )
        print("✅ Заполнен discipline_key (где был пустой)")

        while True:
            cursor.execute(
                """
                SELECT athlete_id, discipline_key, COUNT(*) AS cnt
                FROM subscriptions
                GROUP BY athlete_id, discipline_key
                HAVING cnt > 1
                LIMIT 1
                """
            )
            row = cursor.fetchone()
            if not row:
                break
            aid, dk, _cnt = row
            cursor.execute(
                """
                SELECT id FROM subscriptions
                WHERE athlete_id = ? AND discipline_key = ?
                ORDER BY id
                """,
                (aid, dk),
            )
            ids = [r[0] for r in cursor.fetchall()]
            for sid in ids[1:]:
                suffix = f"_{sid}"
                base = (dk or "legacy")[: max(0, 64 - len(suffix))]
                new_dk = f"{base}{suffix}"
                cursor.execute(
                    "UPDATE subscriptions SET discipline_key = ? WHERE id = ?",
                    (new_dk, sid),
                )
        print("✅ Устранены дубликаты (athlete_id, discipline_key) при необходимости")

        cursor.execute(
            """
            UPDATE subscriptions
            SET responsible_coach_id = (
                SELECT created_by FROM athletes WHERE athletes.id = subscriptions.athlete_id
            )
            WHERE responsible_coach_id IS NULL
            """
        )
        print("✅ Заполнен responsible_coach_id из athletes.created_by (где был NULL)")

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

        if "locked_at" not in columns:
            print("🔧 Добавляю locked_at в таблицу attendances...")
            cursor.execute("ALTER TABLE attendances ADD COLUMN locked_at DATETIME")
            print("✅ locked_at добавлен")

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

        # Персональная заморозка спортсмена (все направления)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS athlete_freezes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                athlete_id INTEGER NOT NULL,
                frozen_from DATETIME NOT NULL,
                frozen_until DATETIME NOT NULL,
                initiated_by_coach_id INTEGER,
                global_freeze_id INTEGER,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS ix_athlete_freezes_athlete_range
            ON athlete_freezes (athlete_id, frozen_from, frozen_until)
        """)
        print("✅ Таблица athlete_freezes и индекс созданы")

        # --- СТРУКТУРНЫЕ ОГРАНИЧЕНИЯ (SQLite UNIQUE INDEX) ---
        # Раньше: один абонемент на спортсмена (uq_subscriptions_athlete_id).
        # Теперь: уникальная пара (athlete_id, discipline_key).

        cursor.execute("DROP INDEX IF EXISTS uq_subscriptions_athlete_id")

        cursor.execute("""
            SELECT athlete_id, discipline_key, COUNT(*) as cnt
            FROM subscriptions
            WHERE discipline_key IS NOT NULL AND discipline_key != ''
            GROUP BY athlete_id, discipline_key
            HAVING COUNT(*) > 1
        """)
        pair_dups = cursor.fetchall()
        if pair_dups:
            print(
                f"⚠️ Остаются дубли (athlete_id, discipline_key). "
                f"UNIQUE не создан. Пример: {pair_dups[0]}"
            )
        else:
            cursor.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS uq_subscriptions_athlete_discipline
                ON subscriptions (athlete_id, discipline_key)
            """)
            print("✅ UNIQUE: uq_subscriptions_athlete_discipline создана")

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

        # Тарифы абонементов (редактируемые цены при активации)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS subscription_tariffs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sport_type_name VARCHAR(50) NOT NULL,
                tariff_kind VARCHAR(40) NOT NULL,
                amount_rubles INTEGER NOT NULL,
                is_active BOOLEAN DEFAULT 1,
                note TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                CHECK (amount_rubles >= 0),
                CHECK (tariff_kind IN (
                    'subscription_monthly',
                    'subscription_single',
                    'individual_training'
                ))
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS ix_subscription_tariffs_kind_active_sport
            ON subscription_tariffs (tariff_kind, is_active, sport_type_name)
        """)
        print("✅ Таблица subscription_tariffs и индекс созданы")

        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='subscription_tariffs'"
        )
        if cursor.fetchone():
            _seed_default_subscription_tariffs(cursor)

        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='subscription_payments'"
        )
        if cursor.fetchone():
            cursor.execute("PRAGMA table_info(subscription_payments)")
            pay_cols = [row[1] for row in cursor.fetchall()]
            if "payment_kind" not in pay_cols:
                print("🔧 Добавляю payment_kind в subscription_payments...")
                cursor.execute(
                    "ALTER TABLE subscription_payments ADD COLUMN payment_kind VARCHAR(40)"
                )
                cursor.execute(
                    """
                    UPDATE subscription_payments SET payment_kind = (
                        SELECT CASE
                            WHEN s.subscription_type = 'monthly' THEN 'subscription_monthly'
                            WHEN s.subscription_type = 'single' THEN 'subscription_single'
                            WHEN s.subscription_type = 'individual' THEN 'individual_training'
                            ELSE 'individual_training'
                        END
                        FROM subscriptions s WHERE s.id = subscription_payments.subscription_id
                    ) WHERE payment_kind IS NULL
                    """
                )
                print("✅ payment_kind добавлен и заполнен по типу абонемента")

        # Расширение CHECK subscription_type (individual): SQLite не умеет ALTER CHECK
        cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='subscriptions'")
        sub_sql_row = cursor.fetchone()
        if sub_sql_row and sub_sql_row[0]:
            sub_sql = sub_sql_row[0]
            if (
                "individual" not in sub_sql.lower()
                and "IN ('monthly', 'single')" in sub_sql
            ):
                print("🔧 Пересоздаю таблицу subscriptions (CHECK + individual)...")
                cursor.execute("PRAGMA foreign_keys=OFF")
                try:
                    new_sql = sub_sql.replace(
                        "IN ('monthly', 'single')",
                        "IN ('monthly', 'single', 'individual')",
                        1,
                    )
                    new_sql = new_sql.replace(
                        "CREATE TABLE subscriptions",
                        "CREATE TABLE subscriptions_mig_nr",
                        1,
                    )
                    cursor.execute(new_sql)
                    cursor.execute(
                        "INSERT INTO subscriptions_mig_nr SELECT * FROM subscriptions"
                    )
                    cursor.execute("DROP TABLE subscriptions")
                    cursor.execute(
                        "ALTER TABLE subscriptions_mig_nr RENAME TO subscriptions"
                    )
                    cursor.execute("""
                        CREATE INDEX IF NOT EXISTS ix_subscriptions_active_end
                        ON subscriptions (is_active, end_date)
                    """)
                    cursor.execute("""
                        CREATE INDEX IF NOT EXISTS ix_subscriptions_athlete_active
                        ON subscriptions (athlete_id, is_active)
                    """)
                    cursor.execute("""
                        SELECT athlete_id, discipline_key, COUNT(*) as cnt
                        FROM subscriptions
                        WHERE discipline_key IS NOT NULL AND discipline_key != ''
                        GROUP BY athlete_id, discipline_key
                        HAVING COUNT(*) > 1
                    """)
                    if not cursor.fetchall():
                        cursor.execute("""
                            CREATE UNIQUE INDEX IF NOT EXISTS uq_subscriptions_athlete_discipline
                            ON subscriptions (athlete_id, discipline_key)
                        """)
                    print("✅ subscriptions: CHECK допускает individual")
                finally:
                    cursor.execute("PRAGMA foreign_keys=ON")

        # Проверяем таблицу trainings
        cursor.execute("PRAGMA table_info(trainings)")
        columns = [row[1] for row in cursor.fetchall()]

        # Добавляем coach_id если его нет
        if 'coach_id' not in columns:
            print("🔧 Добавляю coach_id в таблицу trainings...")
            cursor.execute("ALTER TABLE trainings ADD COLUMN coach_id INTEGER")
            print("✅ coach_id добавлен в trainings")

        if "training_format" not in columns:
            print("🔧 Добавляю training_format в таблицу trainings...")
            cursor.execute(
                "ALTER TABLE trainings ADD COLUMN training_format VARCHAR(20)"
            )
            print("✅ training_format добавлен в trainings")

        # Материализованная история посещений
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS visit_history (
                id INTEGER PRIMARY KEY,
                athlete_id INTEGER NOT NULL,
                training_id INTEGER NOT NULL,
                subscription_id INTEGER NULL,
                attendance_id INTEGER NULL,
                status_code VARCHAR(16) NOT NULL,
                status_label VARCHAR(128) NULL,
                source VARCHAR(24) NULL,
                recorded_at DATETIME,
                updated_at DATETIME,
                FOREIGN KEY(athlete_id) REFERENCES athletes(id),
                FOREIGN KEY(training_id) REFERENCES trainings(id),
                FOREIGN KEY(subscription_id) REFERENCES subscriptions(id),
                FOREIGN KEY(attendance_id) REFERENCES attendances(id)
            )
            """
        )
        cursor.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_visit_history_athlete_training
            ON visit_history (athlete_id, training_id)
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS ix_visit_history_athlete_date
            ON visit_history (athlete_id, training_id)
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS ix_visit_history_status
            ON visit_history (status_code)
            """
        )

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