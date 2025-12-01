from sqlalchemy import create_engine, Column, Integer, String, Boolean, Date, DateTime, Float, Text, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime
from config import config

# Создаем движок базы данных
engine = create_engine(config.DATABASE_URL, echo=False)
Base = declarative_base()
Session = sessionmaker(bind=engine)


class User(Base):
    """Модель пользователя (тренер/спортсмен)"""
    __tablename__ = 'users'

    id = Column(Integer, primary_key=True)
    telegram_id = Column(Integer, unique=True, nullable=True)  # Может быть None для спортсменов без Telegram
    username = Column(String(100))
    first_name = Column(String(100), nullable=False)
    last_name = Column(String(100))
    phone = Column(String(20))
    role = Column(String(20), default='athlete')  # 'coach', 'athlete', 'admin'
    sport_type = Column(String(50), default='MMA')  # 'MMA', 'Boxing', 'BJJ', etc.
    created_at = Column(DateTime, default=datetime.now)
    is_active = Column(Boolean, default=True)

    # Связи
    trainings = relationship('Training', back_populates='athlete', foreign_keys='Training.athlete_id')
    coach_trainings = relationship('Training', back_populates='coach', foreign_keys='Training.coach_id')
    athlete_info = relationship('AthleteInfo', back_populates='user', uselist=False)
    payments = relationship('Payment', back_populates='user')

    def __repr__(self):
        return f"<User {self.first_name} ({self.role})>"


class AthleteInfo(Base):
    """Дополнительная информация о спортсмене"""
    __tablename__ = 'athlete_info'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), unique=True)
    medical_notes = Column(Text)
    age_group = Column(String(50))  # 'Дети', 'Подростки', 'Взрослые'
    subscription_type = Column(String(50))  # 'Разовое', 'Месяц', 'Год'
    subscription_end = Column(Date)
    weight = Column(Float)
    height = Column(Float)
    emergency_contact = Column(String(100))
    emergency_phone = Column(String(20))
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    # Связь
    user = relationship('User', back_populates='athlete_info')

    def __repr__(self):
        return f"<AthleteInfo for User {self.user_id}>"


class Training(Base):
    """Модель тренировки"""
    __tablename__ = 'trainings'

    id = Column(Integer, primary_key=True)
    athlete_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    coach_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    training_date = Column(Date, nullable=False)
    training_time = Column(String(10), nullable=False)
    notes = Column(Text)
    attendance = Column(Boolean, default=False)  # Присутствовал ли спортсмен
    created_at = Column(DateTime, default=datetime.now)

    # Связи
    athlete = relationship('User', back_populates='trainings', foreign_keys=[athlete_id])
    coach = relationship('User', back_populates='coach_trainings', foreign_keys=[coach_id])

    def __repr__(self):
        return f"<Training {self.training_date} {self.training_time}>"


class Payment(Base):
    """Модель платежа"""
    __tablename__ = 'payments'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'))
    amount = Column(Float, nullable=False)
    payment_date = Column(DateTime, default=datetime.now)
    description = Column(String(200))
    payment_method = Column(String(50))  # 'cash', 'card', 'transfer'
    confirmed = Column(Boolean, default=False)

    # Связь
    user = relationship('User', back_populates='payments')

    def __repr__(self):
        return f"<Payment {self.amount} for User {self.user_id}>"


# Функция для создания таблиц
def create_tables():
    """Создать все таблицы в базе данных"""
    Base.metadata.create_all(bind=engine)
    print("✅ Таблицы созданы успешно!")


if __name__ == "__main__":
    create_tables()