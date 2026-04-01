import os
from database.models import Base, engine

print("Создание таблиц базы данных...")

# Создаем папку для базы данных если её нет
db_path = "database/club.db"
os.makedirs(os.path.dirname(db_path), exist_ok=True)

Base.metadata.create_all(bind=engine)
print(f"✅ Таблицы созданы успешно в: {engine.url}")