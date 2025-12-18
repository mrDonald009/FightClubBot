"""Скрипт для генерации случайной истории абонементов для спортсменов."""
import sys
import random
from pathlib import Path
from datetime import datetime, timedelta

# Добавляем корневую папку проекта в путь Python
project_root = Path(__file__).parent
sys.path.append(str(project_root))

from database.models import Session, Athlete, Subscription
from database.db_utils import _calculate_end_date


def generate_subscription_history():
    """Генерирует случайную историю абонементов для всех спортсменов."""
    session = Session()
    
    try:
        # Получаем всех спортсменов
        athletes = session.query(Athlete).all()
        
        if not athletes:
            print("❌ Спортсмены не найдены")
            return
        
        print(f"📋 Найдено спортсменов: {len(athletes)}")
        print("🔄 Начинаю генерацию истории абонементов...\n")
        
        total_generated = 0
        
        for athlete in athletes:
            # Проверяем, есть ли уже абонементы у спортсмена
            existing_subscriptions = session.query(Subscription).filter_by(athlete_id=athlete.id).all()
            
            if existing_subscriptions:
                print(f"👤 {athlete.full_name} (ID: {athlete.id}) - уже есть {len(existing_subscriptions)} абонемент(ов), пропускаю")
                print()
                continue
            
            # Генерируем от 2 до 5 исторических абонементов для каждого спортсмена
            num_subscriptions = random.randint(2, 5)
            
            print(f"👤 {athlete.full_name} (ID: {athlete.id})")
            
            # Начинаем с даты 6 месяцев назад
            base_date = datetime.utcnow() - timedelta(days=180)
            
            for i in range(num_subscriptions):
                # Случайная дата начала (от base_date до текущей даты)
                days_ago = random.randint(0, 150)
                start_date = base_date + timedelta(days=random.randint(0, days_ago))
                
                # Случайный тип абонемента
                subscription_type = random.choice(['monthly', 'single'])
                
                # Вычисляем дату окончания
                if subscription_type == 'monthly':
                    end_date = _calculate_end_date(start_date, months=1)
                    trainings_total = 12
                else:
                    end_date = start_date + timedelta(days=random.randint(1, 7))
                    trainings_total = 1
                
                # Случайное количество использованных тренировок
                if subscription_type == 'monthly':
                    used_trainings = random.randint(0, trainings_total)
                else:
                    used_trainings = random.randint(0, 1)
                
                trainings_remaining = max(0, trainings_total - used_trainings)
                
                # Абонемент активен только если он последний и не истек
                is_active = (i == num_subscriptions - 1) and (end_date >= datetime.utcnow())
                
                # Случайное количество восстановлений
                total_restored = random.randint(0, 3) if subscription_type == 'monthly' else 0
                restored_this_month = random.randint(0, total_restored) if total_restored > 0 else 0
                
                # Создаем абонемент
                subscription = Subscription(
                    athlete_id=athlete.id,
                    subscription_type=subscription_type,
                    start_date=start_date,
                    end_date=end_date,
                    trainings_total=trainings_total,
                    trainings_remaining=trainings_remaining,
                    is_active=is_active,
                    total_restored=total_restored,
                    restored_this_month=restored_this_month,
                    created_at=start_date
                )
                
                session.add(subscription)
                session.flush()  # Получаем ID абонемента
                
                # Если это последний абонемент и он активен, просто создаем его
                # Больше не нужно связывать через subscription_id - связь через athlete_id
                # У спортсмена может быть несколько активных абонементов
                
                sub_type = "Месячный" if subscription_type == 'monthly' else "Разовый"
                status = "🟢 Активен" if is_active else "🔴 Неактивен"
                print(f"   ✅ Создан {sub_type} абонемент: {start_date.strftime('%d.%m.%Y')} - {end_date.strftime('%d.%m.%Y')} ({status})")
                
                total_generated += 1
                
                # Обновляем base_date для следующего абонемента
                base_date = end_date + timedelta(days=random.randint(1, 10))
            
            print()
        
        session.commit()
        print(f"🎉 Генерация завершена!")
        print(f"📊 Всего создано абонементов: {total_generated}")
        print(f"👥 Обработано спортсменов: {len(athletes)}")
        
    except Exception as e:
        print(f"❌ Ошибка при генерации истории: {e}")
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    print("=" * 50)
    print("🎲 ГЕНЕРАТОР ИСТОРИИ АБОНЕМЕНТОВ")
    print("=" * 50)
    print()
    
    response = input("⚠️  Это действие создаст исторические абонементы для всех спортсменов.\n"
                     "   Продолжить? (yes/no): ")
    
    if response.lower() in ['yes', 'y', 'да', 'д']:
        generate_subscription_history()
    else:
        print("❌ Операция отменена")



