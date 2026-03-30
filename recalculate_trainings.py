"""Скрипт для пересчета оставшихся тренировок в абонементах."""
import sys
from pathlib import Path

# Настройка кодировки для Windows
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

# Добавляем текущую директорию в путь для импортов
sys.path.append(str(Path(__file__).parent))

from database.models import Session, Subscription
from database.db_utils import calculate_actual_trainings_remaining

def recalculate_trainings_remaining():
    """Пересчитать trainings_remaining для всех абонементов (логика как в calculate_actual_trainings_remaining)."""
    session = Session()
    try:
        print("🔄 Начинаю пересчет оставшихся тренировок...")
        
        # Получаем все абонементы
        subscriptions = session.query(Subscription).all()
        total_subscriptions = len(subscriptions)
        updated_count = 0
        
        for subscription in subscriptions:
            new_remaining = calculate_actual_trainings_remaining(session, subscription)
            if new_remaining is None:
                print(f"⚠️  Абонемент #{subscription.id}: trainings_total = None, пропускаем")
                continue
            
            # Обновляем только если значение изменилось
            if subscription.trainings_remaining != new_remaining:
                old_remaining = subscription.trainings_remaining
                subscription.trainings_remaining = new_remaining
                updated_count += 1
                
                # Получаем информацию о спортсмене для вывода
                athlete = subscription.athlete
                athlete_name = athlete.full_name if athlete else "Неизвестно"
                
                used = (subscription.trainings_total or 0) - new_remaining
                print(f"✅ Абонемент #{subscription.id} ({athlete_name}): "
                      f"{old_remaining} → {new_remaining} "
                      f"(использовано по расчёту: {used}/{subscription.trainings_total})")
        
        if updated_count > 0:
            session.commit()
            print(f"\n✅ Пересчет завершен!")
            print(f"   Всего абонементов: {total_subscriptions}")
            print(f"   Обновлено: {updated_count}")
        else:
            print(f"\n✅ Все абонементы уже имеют корректные значения!")
            print(f"   Всего абонементов: {total_subscriptions}")
            print(f"   Обновлено: 0")
        
        return updated_count
        
    except Exception as e:
        session.rollback()
        print(f"❌ Ошибка при пересчете: {e}")
        import traceback
        traceback.print_exc()
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    print("=" * 60)
    print("🔄 ПЕРЕСЧЕТ ОСТАВШИХСЯ ТРЕНИРОВОК В АБОНЕМЕНТАХ")
    print("=" * 60)
    print()
    
    updated = recalculate_trainings_remaining()
    
    print()
    print("=" * 60)
    if updated > 0:
        print(f"✅ Пересчет завершен успешно! Обновлено {updated} абонементов.")
    else:
        print("✅ Пересчет завершен. Изменений не требуется.")
    print("=" * 60)

