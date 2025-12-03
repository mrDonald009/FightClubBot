import sys
import os
from pathlib import Path

# Добавляем корневую папку проекта в путь Python
project_root = Path(__file__).parent.parent
sys.path.append(str(project_root))

from database.models import Session, Athlete, Subscription


def check_database_structure():
    """Проверить структуру базы данных"""
    session = Session()

    print("📊 Проверка структуры базы данных...")

    # Проверить спортсменов
    athletes = session.query(Athlete).all()
    print(f"👥 Всего спортсменов: {len(athletes)}")

    athletes_with_subscription = session.query(Athlete).filter(
        Athlete.current_subscription_id.isnot(None)
    ).count()
    print(f"🎫 Спортсменов с текущим абонементом: {athletes_with_subscription}")

    # Проверить абонементы
    subscriptions = session.query(Subscription).all()
    print(f"💰 Всего абонементов: {len(subscriptions)}")

    active_subscriptions = session.query(Subscription).filter(
        Subscription.is_active == True
    ).count()
    print(f"✅ Активных абонементов: {active_subscriptions}")

    session.close()


if __name__ == "__main__":
    check_database_structure()