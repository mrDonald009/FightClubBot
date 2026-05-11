from sqlalchemy import create_engine, Column, Integer, String, DateTime, Boolean, ForeignKey, Text, and_, UniqueConstraint, Index, CheckConstraint
from sqlalchemy.orm import declarative_base
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


class SportType(Base):
    """Таблица видов спорта"""
    __tablename__ = 'sport_types'
    __table_args__ = {'extend_existing': True}

    id = Column(Integer, primary_key=True)
    name = Column(String(50), unique=True, nullable=False)  # MMA, Тайский Бокс, Бокс и т.д.
    display_name = Column(String(100))  # Отображаемое название
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class SubscriptionTariff(Base):
    """Тарифы на абонементы и продукты (редактируемые в БД; цены при активации только отсюда).

    Вид спорта задаётся явно (как у абонемента): для каждого вида — свои строки monthly/single.
    tariff_kind: subscription_monthly, subscription_single; позже — individual_training и др.
    """

    __tablename__ = "subscription_tariffs"
    __table_args__ = (
        Index("ix_subscription_tariffs_kind_active_sport", "tariff_kind", "is_active", "sport_type_name"),
        CheckConstraint("amount_rubles >= 0", name="ck_subscription_tariffs_amount_nonneg"),
        CheckConstraint(
            "tariff_kind IN ('subscription_monthly', 'subscription_single', 'individual_training')",
            name="ck_subscription_tariffs_kind",
        ),
        {"extend_existing": True},
    )

    id = Column(Integer, primary_key=True)
    sport_type_name = Column(String(50), nullable=False)  # как subscriptions.sport_type / athlete.sport_type
    tariff_kind = Column(String(40), nullable=False)
    amount_rubles = Column(Integer, nullable=False)
    is_active = Column(Boolean, default=True)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Coach(Base):
    """Таблица тренеров"""
    __tablename__ = 'coaches'
    __table_args__ = {'extend_existing': True}

    id = Column(Integer, primary_key=True)
    telegram_id = Column(Integer, unique=True, nullable=False)  # Telegram ID
    username = Column(String(100))
    first_name = Column(String(100))
    sport_type_id = Column(Integer, ForeignKey('sport_types.id'), nullable=False)  # Вид спорта, который преподает
    sport_type = Column(String(50), nullable=True)  # Для обратной совместимости
    created_at = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Boolean, default=True)
    
    # Связи
    sport_type_rel = relationship("SportType", foreign_keys=[sport_type_id])


class Admin(Base):
    """Таблица администраторов"""
    __tablename__ = 'admins'
    __table_args__ = {'extend_existing': True}

    id = Column(Integer, primary_key=True)
    telegram_id = Column(Integer, unique=True, nullable=False)  # Telegram ID
    username = Column(String(100))
    first_name = Column(String(100))
    created_at = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Boolean, default=True)


class Assistant(Base):
    """Таблица ассистентов"""
    __tablename__ = 'assistants'
    __table_args__ = {'extend_existing': True}

    id = Column(Integer, primary_key=True)
    telegram_id = Column(Integer, unique=True, nullable=False)  # Telegram ID
    username = Column(String(100))
    first_name = Column(String(100))
    created_at = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Boolean, default=True)


class Athlete(Base):
    __tablename__ = 'athletes'
    __table_args__ = (
        Index('ix_athletes_created_by', 'created_by'),  # частые выборки по тренеру
        Index('ix_athletes_sport_age', 'sport_type', 'age_group'),  # календарь/фильтры
        CheckConstraint(
            "age_group IS NULL OR age_group IN ('children', 'adults')",
            name='ck_athletes_age_group'
        ),
        {'extend_existing': True}
    )

    id = Column(Integer, primary_key=True)
    telegram_id = Column(Integer, unique=True, nullable=True)  # Telegram ID спортсмена (может быть NULL)
    full_name = Column(String(200), nullable=False)
    phone = Column(String(20))
    birth_date = Column(DateTime, nullable=True)  # Дата рождения
    height = Column(Integer)  # рост в см
    weight = Column(Integer)  # вес в кг
    medical_info = Column(Text)
    sport_type = Column(String(50))
    age_group = Column(String(20))  # children, adults
    created_by = Column(Integer, ForeignKey('coaches.id'), nullable=True)  # Тренер, который добавил
    created_at = Column(DateTime, default=datetime.utcnow)  # Дата регистрации в зале

    # Связи
    coach = relationship("Coach", foreign_keys=[created_by])  # Связь с таблицей тренеров

    # Связь один-ко-многим с абонементами
    subscriptions = relationship(
        "Subscription",
        foreign_keys="Subscription.athlete_id",
        back_populates="athlete",
        uselist=True,
        lazy="select",
    )
    athlete_freezes = relationship(
        "AthleteFreeze",
        foreign_keys="AthleteFreeze.athlete_id",
        back_populates="athlete",
        lazy="select",
    )
    
    # Обратная совместимость - возвращает первый активный абонемент
    @property
    def current_subscription(self):
        """Обратная совместимость - возвращает первый активный абонемент"""
        active_subs = [s for s in self.subscriptions if s.is_active]
        return active_subs[0] if active_subs else None
    
    @property
    def current_subscription_id(self):
        """Обратная совместимость - возвращает ID первого активного абонемента"""
        sub = self.current_subscription
        return sub.id if sub else None
    
    @property
    def subscription(self):
        """Алиас для current_subscription для обратной совместимости"""
        return self.current_subscription


class Subscription(Base):
    __tablename__ = 'subscriptions'
    __table_args__ = (
        Index('ix_subscriptions_active_end', 'is_active', 'end_date'),
        Index('ix_subscriptions_athlete_active', 'athlete_id', 'is_active'),
        UniqueConstraint('athlete_id', 'discipline_key', name='uq_subscriptions_athlete_discipline'),
        CheckConstraint(
            "subscription_type IS NULL OR subscription_type IN ('monthly', 'single', 'individual')",
            name='ck_subscriptions_type'
        ),
        CheckConstraint(
            "trainings_total IS NULL OR trainings_total >= 0",
            name='ck_subscriptions_total_nonnegative'
        ),
        CheckConstraint(
            "trainings_remaining IS NULL OR trainings_remaining >= 0",
            name='ck_subscriptions_remaining_nonnegative'
        ),
        CheckConstraint(
            "trainings_total IS NULL OR trainings_remaining IS NULL OR trainings_remaining <= trainings_total",
            name='ck_subscriptions_remaining_lte_total'
        ),
        {'extend_existing': True}
    )

    id = Column(Integer, primary_key=True)
    # Несколько абонементов на одного спортсмена — разные discipline_key (вид спорта × формат).
    athlete_id = Column(Integer, ForeignKey('athletes.id'), nullable=False)
    discipline_key = Column(String(64), nullable=False)  # например thai_boxing_group
    responsible_coach_id = Column(Integer, ForeignKey('coaches.id'), nullable=True)
    sport_type_id = Column(Integer, ForeignKey('sport_types.id'), nullable=True)  # Связь с таблицей видов спорта
    sport_type = Column(String(50))  # Вид спорта для абонемента (для обратной совместимости)
    subscription_type = Column(String(20))  # monthly, single, individual
    # Дата начала должна выставляться ТОЛЬКО при активации абонемента (а не при создании записи)
    start_date = Column(DateTime, nullable=True)
    end_date = Column(DateTime)
    trainings_total = Column(Integer)  # 12 для месячных, 1 для разовых
    trainings_remaining = Column(Integer)
    is_active = Column(Boolean, default=True)

    # Статистика восстановлений
    total_restored = Column(Integer, default=0)  # Всего восстановлено
    restored_this_month = Column(Integer, default=0)  # Восстановлено в этом месяце

    # Заморозка абонемента
    is_frozen = Column(Boolean, default=False)  # Заморожен ли абонемент
    frozen_from = Column(DateTime, nullable=True)  # Дата начала заморозки (тренировочный день + начало тренировки)
    frozen_until = Column(DateTime, nullable=True)  # Дата окончания заморозки (тренировочный день + конец тренировки)
    frozen_days_total = Column(Integer, default=0)  # Общее количество календарных дней заморозки
    frozen_training_days_total = Column(Integer, default=0)  # Общее количество замороженных тренировочных дней

    # Поле created_at без default для SQLite
    created_at = Column(DateTime)

    # Связи
    athlete = relationship(
        "Athlete",
        foreign_keys=[athlete_id],
        back_populates="subscriptions",
    )
    responsible_coach = relationship("Coach", foreign_keys=[responsible_coach_id])
    
    sport_type_rel = relationship("SportType", foreign_keys=[sport_type_id])  # Связь с таблицей видов спорта

    attendances = relationship(
        "Attendance",
        back_populates="subscription",
        foreign_keys="Attendance.subscription_id"
    )
    payments = relationship(
        "SubscriptionPayment",
        back_populates="subscription",
        foreign_keys="SubscriptionPayment.subscription_id",
    )


class SubscriptionPayment(Base):
    """Оплата по абонементу (для статистики выручки тренера за период).

    Минимум полей: к какому абонементу отнесена сумма, сколько рублей, когда учтена оплата.
    Опционально: комментарий, кто занёс запись (telegram_id).
    """

    __tablename__ = "subscription_payments"
    __table_args__ = (
        Index("ix_subscription_payments_paid_at", "paid_at"),
        Index("ix_subscription_payments_subscription", "subscription_id"),
        CheckConstraint("amount_rubles >= 0", name="ck_subscription_payments_amount_nonneg"),
        {"extend_existing": True},
    )

    id = Column(Integer, primary_key=True)
    subscription_id = Column(Integer, ForeignKey("subscriptions.id"), nullable=False)
    amount_rubles = Column(Integer, nullable=False)
    paid_at = Column(DateTime, nullable=False)
    # subscription_monthly | subscription_single | individual_training (как в subscription_tariffs.tariff_kind)
    payment_kind = Column(String(40), nullable=True)
    note = Column(Text, nullable=True)
    recorded_by_telegram_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    subscription = relationship("Subscription", back_populates="payments")


class Training(Base):
    __tablename__ = 'trainings'
    __table_args__ = (
        Index('ix_trainings_coach_date', 'coach_id', 'training_date'),
        Index('ix_trainings_sport_age_date', 'sport_type', 'age_group', 'training_date'),
        {'extend_existing': True}
    )

    id = Column(Integer, primary_key=True)
    sport_type = Column(String(50))  # MMA, Thai
    age_group = Column(String(20))  # children, adults
    training_date = Column(DateTime)
    is_cancelled = Column(Boolean, default=False)
    coach_id = Column(Integer, ForeignKey('coaches.id'), nullable=True)  # Тренер, проводящий тренировку
    # NULL / group — групповая пара по расписанию; individual — отдельный слот (дата/время = начало)
    training_format = Column(String(20), nullable=True)

    # Связи
    coach = relationship("Coach", foreign_keys=[coach_id])  # Связь с таблицей тренеров


class Attendance(Base):
    __tablename__ = 'attendances'
    __table_args__ = (
        UniqueConstraint('athlete_id', 'training_id', name='uq_attendances_athlete_training'),
        Index('ix_attendances_subscription', 'subscription_id'),
        Index('ix_attendances_athlete_created', 'athlete_id', 'created_at'),
        {'extend_existing': True}
    )

    id = Column(Integer, primary_key=True)
    athlete_id = Column(Integer, ForeignKey('athletes.id'))
    training_id = Column(Integer, ForeignKey('trainings.id'))
    subscription_id = Column(Integer, ForeignKey('subscriptions.id'))
    attended = Column(Boolean, default=False)  # True - присутствовал, False - отсутствовал
    # В БД исторически хранится один идентификатор отметившего (telegram_id или legacy id)
    marked_by = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    # После окончания пары NULL→now: строка не меняется тренером; до этого можно менять «был/не был»
    locked_at = Column(DateTime, nullable=True)

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
    # В БД исторически хранится один идентификатор восстановившего (telegram_id или legacy id)
    restored_by = Column(Integer, nullable=True)
    restored_at = Column(DateTime, default=datetime.utcnow)

    # Связи
    athlete = relationship("Athlete")
    subscription = relationship("Subscription")


class AthleteFreeze(Base):
    """Персональная заморозка спортсмена целиком (все направления/абонементы)."""
    __tablename__ = 'athlete_freezes'
    __table_args__ = (
        Index('ix_athlete_freezes_athlete_range', 'athlete_id', 'frozen_from', 'frozen_until'),
        {'extend_existing': True},
    )

    id = Column(Integer, primary_key=True)
    athlete_id = Column(Integer, ForeignKey('athletes.id'), nullable=False)
    frozen_from = Column(DateTime, nullable=False)
    frozen_until = Column(DateTime, nullable=False)
    initiated_by_coach_id = Column(Integer, ForeignKey('coaches.id'), nullable=True)
    global_freeze_id = Column(Integer, ForeignKey('global_freezes.id'), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    athlete = relationship("Athlete", back_populates="athlete_freezes")
    initiated_by_coach = relationship("Coach", foreign_keys=[initiated_by_coach_id])


class GlobalFreeze(Base):
    """Массовая заморозка клуба (праздники/каникулы)."""
    __tablename__ = 'global_freezes'
    __table_args__ = (
        Index('ix_global_freezes_active_range', 'is_active', 'start_date', 'end_date'),
        {'extend_existing': True}
    )

    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False)
    start_date = Column(DateTime, nullable=False)
    end_date = Column(DateTime, nullable=False)
    is_active = Column(Boolean, default=True)
    created_by = Column(Integer, nullable=True)  # telegram_id инициатора
    created_at = Column(DateTime, default=datetime.utcnow)


class GlobalFreezeApplication(Base):
    """Фиксация применения массовой заморозки к конкретному абонементу."""
    __tablename__ = 'global_freeze_applications'
    __table_args__ = (
        UniqueConstraint('global_freeze_id', 'subscription_id', name='uq_global_freeze_subscription'),
        Index('ix_gfa_subscription', 'subscription_id'),
        {'extend_existing': True}
    )

    id = Column(Integer, primary_key=True)
    global_freeze_id = Column(Integer, ForeignKey('global_freezes.id'), nullable=False)
    subscription_id = Column(Integer, ForeignKey('subscriptions.id'), nullable=False)
    training_days_added = Column(Integer, default=0)
    old_end_date = Column(DateTime, nullable=True)
    new_end_date = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    global_freeze = relationship("GlobalFreeze")
    subscription = relationship("Subscription")


# URL базы данных (по умолчанию SQLite в папке проекта)
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///database/club.db")

# Для SQLite гарантируем наличие директории файла БД
if DATABASE_URL.startswith("sqlite:///"):
    db_path = DATABASE_URL.replace("sqlite:///", "", 1)
    db_dir = os.path.dirname(db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

# Создаем движок SQLAlchemy
engine = create_engine(DATABASE_URL)

# Создаем таблицы если их нет
Base.metadata.create_all(engine)

# Создаем сессию
Session = sessionmaker(bind=engine)