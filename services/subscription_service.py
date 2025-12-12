"""Сервис для работы с абонементами."""
from typing import List, Optional
from sqlalchemy.orm import Session
from database.models import Subscription, Athlete
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
    def get_active_subscription(session: Session, athlete_id: int) -> Optional[Subscription]:
        """
        Получить активный абонемент спортсмена.
        
        Args:
            session: Сессия базы данных
            athlete_id: ID спортсмена
            
        Returns:
            Активный Subscription или None
        """
        athlete = AthleteService.get_athlete_or_raise(session, athlete_id)
        if athlete.current_subscription_id:
            return SubscriptionService.get_subscription_by_id(session, athlete.current_subscription_id)
        return None
    
    @staticmethod
    def create_subscription(
        session: Session,
        athlete_id: int,
        subscription_type: str,
    ) -> Subscription:
        """
        Создать новый абонемент для спортсмена.
        
        Args:
            session: Сессия базы данных
            athlete_id: ID спортсмена
            subscription_type: Тип абонемента (monthly, single)
            
        Returns:
            Созданный абонемент
            
        Raises:
            ValidationError: Если данные невалидны
        """
        # Проверяем, что спортсмен существует
        athlete = AthleteService.get_athlete_or_raise(session, athlete_id)
        
        # Валидация типа абонемента
        if subscription_type not in ['monthly', 'single']:
            raise ValidationError(f"Неизвестный тип абонемента: {subscription_type}")
        
        # Создаем абонемент
        subscription = db_create_subscription(session, athlete_id, subscription_type)
        
        # Обновляем текущий абонемент спортсмена
        athlete.current_subscription_id = subscription.id
        session.commit()
        
        return subscription
    
    @staticmethod
    def get_athlete_subscriptions(session: Session, athlete_id: int) -> List[Subscription]:
        """
        Получить все абонементы спортсмена.
        
        Args:
            session: Сессия базы данных
            athlete_id: ID спортсмена
            
        Returns:
            Список абонементов
        """
        athlete = AthleteService.get_athlete_or_raise(session, athlete_id)
        return athlete.subscriptions
    
    @staticmethod
    def check_and_update_subscriptions() -> int:
        """
        Проверить и обновить статусы всех абонементов.
        
        Returns:
            Количество обновленных абонементов
        """
        from utils.subscription_checker import SubscriptionChecker
        return SubscriptionChecker.check_and_update_subscriptions()

