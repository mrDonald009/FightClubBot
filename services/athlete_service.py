"""Сервис для работы со спортсменами."""
from typing import List, Optional
from sqlalchemy.orm import Session
from database.models import Athlete, Coach
from database.db_utils import (
    create_athlete as db_create_athlete,
    get_athletes_by_coach,
)
from core.exceptions import AthleteNotFoundError, ValidationError
from services.user_service import UserService


class AthleteService:
    """Сервис для работы со спортсменами."""
    
    @staticmethod
    def get_athlete_by_id(session: Session, athlete_id: int) -> Optional[Athlete]:
        """
        Получить спортсмена по ID.
        
        Args:
            session: Сессия базы данных
            athlete_id: ID спортсмена
            
        Returns:
            Athlete или None
        """
        return session.query(Athlete).filter_by(id=athlete_id).first()
    
    @staticmethod
    def get_athlete_or_raise(session: Session, athlete_id: int) -> Athlete:
        """
        Получить спортсмена по ID или выбросить исключение.
        
        Args:
            session: Сессия базы данных
            athlete_id: ID спортсмена
            
        Returns:
            Athlete
            
        Raises:
            AthleteNotFoundError: Если спортсмен не найден
        """
        athlete = AthleteService.get_athlete_by_id(session, athlete_id)
        if not athlete:
            raise AthleteNotFoundError(f"Спортсмен с id={athlete_id} не найден")
        return athlete
    
    @staticmethod
    def get_athletes_by_coach(session: Session, coach_id: int) -> List[Athlete]:
        """
        Получить список спортсменов тренера.
        
        Args:
            session: Сессия базы данных
            coach_id: ID тренера
            
        Returns:
            Список спортсменов
        """
        return get_athletes_by_coach(session, coach_id)
    
    @staticmethod
    def create_athlete(
        session: Session,
        coach_id: int,
        full_name: str,
        phone: str,
        medical_info: str,
        sport_type: str,
        age_group: str,
        height: Optional[int] = None,
        weight: Optional[int] = None,
    ) -> Athlete:
        """
        Создать нового спортсмена.
        
        Args:
            session: Сессия базы данных
            coach_id: ID тренера, создающего спортсмена
            full_name: Полное имя
            phone: Телефон
            medical_info: Медицинская информация
            sport_type: Тип спорта
            age_group: Возрастная группа
            height: Рост (опционально)
            weight: Вес (опционально)
            
        Returns:
            Созданный спортсмен
            
        Raises:
            ValidationError: Если данные невалидны
        """
        # Проверяем, что тренер существует
        coach = session.query(Coach).filter_by(id=coach_id).first()
        if not coach:
            raise ValidationError(f"Тренер с id={coach_id} не найден")
        
        # Валидация данных
        if not full_name or len(full_name.strip()) < 2:
            raise ValidationError("ФИО должно содержать минимум 2 символа")
        
        if not phone:
            raise ValidationError("Телефон обязателен")
        
        # Создаем спортсмена
        athlete = db_create_athlete(
            session=session,
            user_id=None,  # Спортсмен может не иметь Telegram аккаунта
            full_name=full_name.strip(),
            phone=phone.strip(),
            medical_info=medical_info.strip() if medical_info else "",
            sport_type=sport_type,
            age_group=age_group,
            created_by=coach_id,
            height=height,
            weight=weight,
        )
        
        return athlete
    
    @staticmethod
    def validate_phone(phone: str) -> bool:
        """
        Валидировать номер телефона.
        
        Args:
            phone: Номер телефона
            
        Returns:
            True, если номер валиден
        """
        import re
        # Простая валидация: цифры, могут быть пробелы, скобки, дефисы, плюс
        pattern = r'^[\+]?[(]?[0-9]{1,4}[)]?[-\s\.]?[(]?[0-9]{1,4}[)]?[-\s\.]?[0-9]{1,9}$'
        return bool(re.match(pattern, phone.strip()))

