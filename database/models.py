from sqlalchemy import create_engine, Column, Integer, String, DateTime, Boolean, ForeignKey, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime
import enum
import os

# Создаем базовый класс с флагом extend_existing
Base = declarative_base()


class RestorationStatus(enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    COMPLETED = "completed"


class User(Base):
    __tablename__ = 'users'
    __table_args__ = {'extend_existing': True}  # Добавляем этот флаг

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
    __table_args__ = {'extend_existing': True}

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=True)  # Может быть NULL
    full_name = Column(String(200), nullable=False)
    phone = Column(String(20))
    height = Column(Integer)  # рост в см
    weight = Column(Integer)  # вес в кг
    medical_info = Column(Text)
    sport_type = Column(String(50))
    age_group = Column(String(20))  # children, adults
    created_by = Column(Integer, ForeignKey('users.id'))  # тренер, который добавил
    created_at = Column(DateTime, default=datetime.utcnow)

    # Связь с текущим активным абонементом
    current_subscription_id = Column(Integer, ForeignKey('subscriptions.id'), nullable=True)

    # Связи
    user = relationship("User", foreign_keys=[user_id])
    coach = relationship("User", foreign_keys=[created_by])

    current_subscription = relationship(
        "Subscription",
        foreign_keys=[current_subscription_id],
        backref="athlete_ref",
        post_update=True
    )

    subscriptions = relationship(
        "Subscription",
        foreign_keys="Subscription.athlete_id",
        back_populates="athlete"
    )


class Subscription(Base):
    __tablename__ = 'subscriptions'
    __table_args__ = {'extend_existing': True}  # Ключевой флаг

    id = Column(Integer, primary_key=True)
    athlete_id = Column(Integer, ForeignKey('athletes.id'))
    subscription_type = Column(String(20))  # monthly, single
    start_date = Column(DateTime, default=datetime.utcnow)
    end_date = Column(DateTime)
    trainings_total = Column(Integer)  # 12 для месячных, 1 для разовых
    trainings_remaining = Column(Integer)
    is_active = Column(Boolean, default=True)

    # Статистика восстановлений
    total_restored = Column(Integer, default=0)  # Всего восстановлено
    restored_this_month = Column(Integer, default=0)  # Восстановлено в этом месяце

    # Поле created_at без default для SQLite
    created_at = Column(DateTime)

    # Связи - явно указываем foreign_keys
    athlete = relationship(
        "Athlete",
        foreign_keys=[athlete_id],
        back_populates="subscriptions"
    )

    attendances = relationship(
        "Attendance",
        back_populates="subscription",
        foreign_keys="Attendance.subscription_id"
    )


class Training(Base):
    __tablename__ = 'trainings'
    __table_args__ = {'extend_existing': True}

    id = Column(Integer, primary_key=True)
    sport_type = Column(String(50))  # MMA, Thai
    age_group = Column(String(20))  # children, adults
    training_date = Column(DateTime)
    is_cancelled = Column(Boolean, default=False)


class Attendance(Base):
    __tablename__ = 'attendances'
    __table_args__ = {'extend_existing': True}

    id = Column(Integer, primary_key=True)
    athlete_id = Column(Integer, ForeignKey('athletes.id'))
    training_id = Column(Integer, ForeignKey('trainings.id'))
    subscription_id = Column(Integer, ForeignKey('subscriptions.id'))
    attended = Column(Boolean, default=False)  # True - присутствовал, False - отсутствовал
    marked_by = Column(Integer, ForeignKey('users.id'))
    created_at = Column(DateTime, default=datetime.utcnow)

    # Флаги восстановления
    was_restored = Column(Boolean, default=False)
    restoration_reason = Column(Text, nullable=True)

    # Связи
    athlete = relationship("Athlete")
    training = relationship("Training")
    subscription = relationship(
        "Subscription",
        foreign_keys=[subscription_id],
        back_populates="attendances"
    )
    marker = relationship("User", foreign_keys=[marked_by])


class RestorationRequest(Base):
    """Запрос на восстановление тренировок (упрощенный - сразу исполняется)"""
    __tablename__ = 'restoration_requests'
    __table_args__ = {'extend_existing': True}

    id = Column(Integer, primary_key=True)
    athlete_id = Column(Integer, ForeignKey('athletes.id'))
    subscription_id = Column(Integer, ForeignKey('subscriptions.id'))

    # Детали запроса
    missed_dates = Column(Text)  # Даты пропущенных тренировок в формате JSON
    restored_count = Column(Integer)  # Сколько тренировок восстановлено
    reason = Column(Text)
    notes = Column(Text, nullable=True)

    # Аудит
    restored_by = Column(Integer, ForeignKey('users.id'))
    restored_at = Column(DateTime, default=datetime.utcnow)

    # Связи
    athlete = relationship("Athlete")
    subscription = relationship("Subscription")
    restorer = relationship("User", foreign_keys=[restored_by])


# Путь к базе данных
DB_PATH = "database/club.db"

# Создаем папку если её нет
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

# Создаем движок SQLAlchemy
engine = create_engine(f'sqlite:///{DB_PATH}')

# Создаем таблицы если их нет
Base.metadata.create_all(engine)

# Создаем сессию
Session = sessionmaker(bind=engine)