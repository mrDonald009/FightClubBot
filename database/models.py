from sqlalchemy import create_engine, Column, Integer, String, DateTime, Boolean, ForeignKey, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime
from config import config

# Создаем базовый класс
Base = declarative_base()

class User(Base):
    __tablename__ = 'users'

    id = Column(Integer, primary_key=True)
    telegram_id = Column(Integer, unique=True, nullable=False)
    username = Column(String(100))
    first_name = Column(String(100))
    role = Column(String(20), default='athlete')  # admin, coach, assistant, athlete
    sport_type = Column(String(50), nullable=True)  # MMA, Thai - только для тренеров
    created_at = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Boolean, default=True)

class Athlete(Base):
    __tablename__ = 'athletes'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'))
    full_name = Column(String(200), nullable=False)
    phone = Column(String(20))
    height = Column(Integer)  # рост в см
    weight = Column(Integer)  # вес в кг
    medical_info = Column(Text)  # медицинские противопоказания
    sport_type = Column(String(50))  # MMA, Thai
    age_group = Column(String(20))  # children, adults
    created_by = Column(Integer, ForeignKey('users.id'))  # тренер, который добавил
    created_at = Column(DateTime, default=datetime.utcnow)

    # Связи
    user = relationship("User", foreign_keys=[user_id])
    coach = relationship("User", foreign_keys=[created_by])

class Subscription(Base):
    __tablename__ = 'subscriptions'

    id = Column(Integer, primary_key=True)
    athlete_id = Column(Integer, ForeignKey('athletes.id'))
    subscription_type = Column(String(20))  # monthly, single
    start_date = Column(DateTime, default=datetime.utcnow)
    end_date = Column(DateTime)
    trainings_total = Column(Integer)  # 12 для месячных, 1 для разовых
    trainings_remaining = Column(Integer)
    is_active = Column(Boolean, default=True)

    athlete = relationship("Athlete")

class Training(Base):
    __tablename__ = 'trainings'

    id = Column(Integer, primary_key=True)
    sport_type = Column(String(50))  # MMA, Thai
    age_group = Column(String(20))  # children, adults
    training_date = Column(DateTime)
    is_cancelled = Column(Boolean, default=False)

class Attendance(Base):
    __tablename__ = 'attendances'

    id = Column(Integer, primary_key=True)
    athlete_id = Column(Integer, ForeignKey('athletes.id'))
    training_id = Column(Integer, ForeignKey('trainings.id'))
    attended = Column(Boolean, default=False)
    marked_by = Column(Integer, ForeignKey('users.id'))  # кто отметил
    created_at = Column(DateTime, default=datetime.utcnow)

    athlete = relationship("Athlete")
    training = relationship("Training")
    marker = relationship("User", foreign_keys=[marked_by])

# Создаем движок и таблицы
engine = create_engine(config.DATABASE_URL)

# УДАЛЯЕМ И ПЕРЕСОЗДАЕМ ТАБЛИЦЫ (только для разработки)
Base.metadata.drop_all(engine)
Base.metadata.create_all(engine)

# Создаем сессию
Session = sessionmaker(bind=engine)