import sqlite3


def update_athletes_simple():
    """Простой скрипт для обновления спортсменов без SQLAlchemy"""

    db_path = "database/club.db"

    connection = sqlite3.connect(db_path)
    cursor = connection.cursor()

    try:
        print("🔍 Поиск спортсменов без текущего абонемента...")

        # Находим спортсменов без current_subscription_id
        cursor.execute("""
            SELECT a.id, a.full_name 
            FROM athletes a 
            WHERE a.current_subscription_id IS NULL
        """)

        athletes = cursor.fetchall()
        print(f"📊 Найдено {len(athletes)} спортсменов без текущего абонемента")

        updated_count = 0
        for athlete_id, athlete_name in athletes:
            # Находим последний абонемент спортсмена
            cursor.execute("""
                SELECT id 
                FROM subscriptions 
                WHERE athlete_id = ? 
                ORDER BY id DESC 
                LIMIT 1
            """, (athlete_id,))

            result = cursor.fetchone()
            if result:
                subscription_id = result[0]

                # Обновляем спортсмена
                cursor.execute("""
                    UPDATE athletes 
                    SET current_subscription_id = ? 
                    WHERE id = ?
                """, (subscription_id, athlete_id))

                updated_count += 1
                print(f"✅ {athlete_name}: абонемент #{subscription_id}")

        connection.commit()
        print(f"🎉 Обновлено {updated_count} спортсменов")

    except Exception as e:
        print(f"❌ Ошибка: {e}")
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    update_athletes_simple()