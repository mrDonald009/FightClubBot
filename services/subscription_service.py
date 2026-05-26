"""Сервис для работы с абонементами."""
from typing import List, Optional
from sqlalchemy.orm import Session
from database.models import Subscription
from database.db_utils import create_subscription as db_create_subscription
from core.exceptions import SubscriptionNotFoundError, ValidationError
from services.athlete_service import AthleteService


class SubscriptionService:
    """Сервис для работы с абонементами."""
    
    @staticmethod
    def get_subscription_by_id(session: Session, subscription_id: int) -> Optional[Subscription]:
        """
        Получить абонемент по ID.
        
        Args:
            session: Сессия базы данных
            subscription_id: ID абонемента
            
        Returns:
            Subscription или None
        """
        return session.query(Subscription).filter_by(id=subscription_id).first()
    
    @staticmethod
    def get_subscription_or_raise(session: Session, subscription_id: int) -> Subscription:
        """
        Получить абонемент по ID или выбросить исключение.
        
        Args:
            session: Сессия базы данных
            subscription_id: ID абонемента
            
        Returns:
            Subscription
            
        Raises:
            SubscriptionNotFoundError: Если абонемент не найден
        """
        subscription = SubscriptionService.get_subscription_by_id(session, subscription_id)
        if not subscription:
            raise SubscriptionNotFoundError(f"Абонемент с id={subscription_id} не найден")
        return subscription
    
    @staticmethod
    def get_active_subscription(session: Session, athlete_id: int, sport_type: str = None) -> Optional[Subscription]:
        """
        Получить активный абонемент спортсмена.
        
        Args:
            session: Сессия базы данных
            athlete_id: ID спортсмена
            sport_type: Вид спорта (опционально, для фильтрации)
            
        Returns:
            Активный Subscription или None
        """
        athlete = AthleteService.get_athlete_or_raise(session, athlete_id)
        active_subs = [s for s in athlete.subscriptions if s.is_active]
        if sport_type:
            active_subs = [s for s in active_subs if s.sport_type == sport_type]
        return active_subs[0] if active_subs else None
    
    @staticmethod
    def create_subscription(
        session: Session,
        athlete_id: int,
        subscription_type: str = None,
        sport_type: str = None,
        *,
        discipline_key: str = None,
        subscription_format: str = "group",
        responsible_coach_id: int = None,
    ) -> Subscription:
        """
        Создать новый абонемент для спортсмена.
        
        Args:
            session: Сессия базы данных
            athlete_id: ID спортсмена
            subscription_type: Тип абонемента (monthly, single, individual) или None
            sport_type: Вид спорта для абонемента (если None, берется из спортсмена)
            discipline_key: Явный ключ направления; иначе из sport_type и subscription_format
            subscription_format: group или individual (для вычисления discipline_key)
            responsible_coach_id: Ответственный тренер (coaches.id); иначе athletes.created_by
            
        Returns:
            Созданный абонемент
            
        Raises:
            ValidationError: Если данные невалидны
        """
        # Проверяем, что спортсмен существует
        athlete = AthleteService.get_athlete_or_raise(session, athlete_id)
        
        # Валидация типа абонемента (если указан)
        if subscription_type is not None and subscription_type not in [
            "monthly",
            "single",
            "individual",
        ]:
            raise ValidationError(f"Неизвестный тип абонемента: {subscription_type}")
        
        # Если sport_type не указан, берем из спортсмена
        if not sport_type:
            sport_type = athlete.sport_type
        
        return db_create_subscription(
            session=session,
            athlete_id=athlete_id,
            subscription_type=subscription_type,
            sport_type=sport_type,
            discipline_key=discipline_key,
            subscription_format=subscription_format,
            responsible_coach_id=responsible_coach_id,
        )
    
    @staticmethod
    def get_athlete_subscriptions(session: Session, athlete_id: int, sport_type: str = None) -> List[Subscription]:
        """
        Получить все абонементы спортсмена.
        
        Args:
            session: Сессия базы данных
            athlete_id: ID спортсмена
            sport_type: Вид спорта (опционально, для фильтрации)
            
        Returns:
            Список абонементов
        """
        athlete = AthleteService.get_athlete_or_raise(session, athlete_id)
        subscriptions = list(athlete.subscriptions)
        if sport_type:
            subscriptions = [s for s in subscriptions if s.sport_type == sport_type]
        return subscriptions
    
    @staticmethod
    def check_and_update_subscriptions() -> int:
        """
        Проверить и обновить статусы всех абонементов.
        
        Returns:
            Количество обновленных абонементов
        """
        from utils.subscription_checker import SubscriptionChecker
        return SubscriptionChecker.check_and_update_subscriptions()

