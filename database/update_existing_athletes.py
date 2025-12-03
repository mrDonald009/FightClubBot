import sys
import os
from pathlib import Path

# Добавляем корневую папку проекта в путь Python
project_root = Path(__file__).parent.parent
sys.path.append(str(project_root))

from database.models import Session, Athlete, Subscription


def update_existing_athletes():
    """Обновить существующих спортсменов, добавив им текущий абонемент"""
    session = Session()
    try:
        # Получаем всех спортсменов без current_subscription_id
        athletes_without_subscription = session.query(Athlete).filter(
            Athlete.current_subscription_id.is_(None)
        ).all()

        print(f"🔍 Найдено {len(athletes_without_subscription)} спортсменов без текущего абонемента")

        updated_count = 0
        for athlete in athletes_without_subscription:
            # Находим самый последний абонемент спортсмена (по id, так как нет created_at)
            subscription = session.query(Subscription).filter(
                Subscription.athlete_id == athlete.id
            ).order_by(Subscription.id.desc()).first()  # Сортируем по id, так как он автоинкрементный

            if subscription:
                athlete.current_subscription_id = subscription.id
                updated_count += 1
                print(f"✅ Спортсмену {athlete.full_name} назначен абонемент #{subscription.id}")

        session.commit()
        print(f"🎉 Обновлено {updated_count} спортсменов")

    except Exception as e:
        print(f"❌ Ошибка при обновлении: {e}")
        session.rollback()
        import traceback
        traceback.print_exc()
    finally:
        session.close()


if __name__ == "__main__":
    update_existing_athletes()