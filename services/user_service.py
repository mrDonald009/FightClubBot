"""Сервис для работы с пользователями."""
from typing import Optional
from sqlalchemy.orm import Session
from database.models import User
from database.db_utils import get_user_by_telegram_id, create_user as db_create_user
from core.exceptions import UserNotFoundError, PermissionDeniedError


class UserService:
    """Сервис для работы с пользователями."""
    
    @staticmethod
    def get_user_by_telegram_id(session: Session, telegram_id: int) -> Optional[User]:
        """
        Получить пользователя по Telegram ID.
        
        Args:
            session: Сессия базы данных
            telegram_id: Telegram ID пользователя
            
        Returns:
            User или None, если не найден
        """
        return get_user_by_telegram_id(session, telegram_id)
    
    @staticmethod
    def get_user_or_raise(session: Session, telegram_id: int) -> User:
        """
        Получить пользователя по Telegram ID или выбросить исключение.
        
        Args:
            session: Сессия базы данных
            telegram_id: Telegram ID пользователя
            
        Returns:
            User
            
        Raises:
            UserNotFoundError: Если пользователь не найден
        """
        user = get_user_by_telegram_id(session, telegram_id)
        if not user:
            raise UserNotFoundError(f"Пользователь с telegram_id={telegram_id} не найден")
        return user
    
    @staticmethod
    def create_user(
        session: Session,
        telegram_id: int,
        username: str,
        first_name: str,
        role: str = "athlete",
        sport_type: Optional[str] = None
    ) -> User:
        """
        Создать нового пользователя.
        
        Args:
            session: Сессия базы данных
            telegram_id: Telegram ID
            username: Имя пользователя
            first_name: Имя
            role: Роль (athlete, coach, admin)
            sport_type: Тип спорта (для тренеров)
            
        Returns:
            Созданный пользователь
        """
        return db_create_user(session, telegram_id, username, first_name, role, sport_type)
    
    @staticmethod
    def get_or_create_user(
        session: Session,
        telegram_id: int,
        username: str,
        first_name: str,
        role: str = "athlete",
        sport_type: Optional[str] = None
    ) -> User:
        """
        Получить существующего пользователя или создать нового.
        
        Args:
            session: Сессия базы данных
            telegram_id: Telegram ID
            username: Имя пользователя
            first_name: Имя
            role: Роль по умолчанию
            sport_type: Тип спорта
            
        Returns:
            Пользователь
        """
        user = get_user_by_telegram_id(session, telegram_id)
        if not user:
            user = db_create_user(session, telegram_id, username, first_name, role, sport_type)
        return user
    
    @staticmethod
    def check_permission(user: User, required_roles: list[str]) -> bool:
        """
        Проверить, имеет ли пользователь требуемую роль.
        
        Args:
            user: Пользователь
            required_roles: Список разрешенных ролей
            
        Returns:
            True, если имеет права
            
        Raises:
            PermissionDeniedError: Если нет прав
        """
        if user.role not in required_roles:
            raise PermissionDeniedError(
                f"Пользователь {user.telegram_id} не имеет прав. "
                f"Требуется одна из ролей: {required_roles}, получено: {user.role}"
            )
        return True
    
    @staticmethod
    def ensure_test_coach(session: Session, telegram_id: int, username: str = "coach_mma", 
                         first_name: str = "Тренер ММА", sport_type: str = "MMA") -> User:
        """
        Убедиться, что тестовый тренер существует с правильными параметрами.
        
        Args:
            session: Сессия базы данных
            telegram_id: Telegram ID тренера
            username: Имя пользователя
            first_name: Имя
            sport_type: Тип спорта
            
        Returns:
            Пользователь-тренер
        """
        user = get_user_by_telegram_id(session, telegram_id)
        
        if user:
            # Обновляем роль и спорт, если нужно
            if user.role != 'coach' or user.sport_type != sport_type:
                user.role = 'coach'
                user.sport_type = sport_type
                session.commit()
        else:
            # Создаем нового тренера
            user = db_create_user(
                session=session,
                telegram_id=telegram_id,
                username=username,
                first_name=first_name,
                role="coach",
                sport_type=sport_type
            )
        
        return user

