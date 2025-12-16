"""Сервис для работы с пользователями."""
from typing import Optional, List, Union
from sqlalchemy.orm import Session
from database.models import Coach, Admin, Assistant, Athlete
from database.db_utils import get_user_by_telegram_id, get_user_role, create_user as db_create_user
from core.exceptions import UserNotFoundError, PermissionDeniedError


class UserService:
    """Сервис для работы с пользователями."""
    
    @staticmethod
    def get_user_by_telegram_id(session: Session, telegram_id: int) -> Optional[Union[Coach, Admin, Assistant, Athlete]]:
        """
        Получить пользователя по Telegram ID.
        
        Args:
            session: Сессия базы данных
            telegram_id: Telegram ID пользователя
            
        Returns:
            Coach, Admin, Assistant или Athlete, или None, если не найден
        """
        return get_user_by_telegram_id(session, telegram_id)
    
    @staticmethod
    def get_user_or_raise(session: Session, telegram_id: int) -> Union[Coach, Admin, Assistant, Athlete]:
        """
        Получить пользователя по Telegram ID или выбросить исключение.
        
        Args:
            session: Сессия базы данных
            telegram_id: Telegram ID пользователя
            
        Returns:
            Coach, Admin, Assistant или Athlete
            
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
    ) -> Union[Coach, Admin, Assistant]:
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
    ) -> Union[Coach, Admin, Assistant, Athlete]:
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
            Coach, Admin, Assistant или Athlete
        """
        user = get_user_by_telegram_id(session, telegram_id)
        if not user:
            if role == "athlete":
                # Для спортсменов создаем через create_athlete
                from database.db_utils import create_athlete
                user = create_athlete(
                    session=session,
                    telegram_id=telegram_id,
                    full_name=first_name,
                    phone=None,
                    medical_info="",
                    sport_type=None,
                    age_group=None,
                    created_by=None
                )
            else:
                user = db_create_user(session, telegram_id, username, first_name, role, sport_type)
        return user
    
    @staticmethod
    def check_permission(user: Union[Coach, Admin, Assistant, Athlete], required_roles: List[str]) -> bool:
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
        user_role = get_user_role(user)
        if user_role not in required_roles:
            raise PermissionDeniedError(
                f"Пользователь {user.telegram_id} не имеет прав. "
                f"Требуется одна из ролей: {required_roles}, получено: {user_role}"
            )
        return True
    
    @staticmethod
    def ensure_test_coach(session: Session, telegram_id: int, username: str = "coach_mma", 
                         first_name: str = "Тренер ММА", sport_type: str = "MMA") -> Coach:
        """
        Убедиться, что тестовый тренер существует с правильными параметрами.
        
        Args:
            session: Сессия базы данных
            telegram_id: Telegram ID тренера
            username: Имя пользователя
            first_name: Имя
            sport_type: Тип спорта
            
        Returns:
            Тренер
        """
        from database.models import Coach, SportType
        
        user = get_user_by_telegram_id(session, telegram_id)
        
        if isinstance(user, Coach):
            # Обновляем спорт, если нужно
            sport_type_obj = session.query(SportType).filter_by(name=sport_type).first()
            if not sport_type_obj:
                sport_type_obj = SportType(name=sport_type, display_name=sport_type)
                session.add(sport_type_obj)
                session.flush()
            
            if user.sport_type_id != sport_type_obj.id:
                user.sport_type_id = sport_type_obj.id
                user.sport_type = sport_type
                session.commit()
            return user
        elif user:
            # Если пользователь существует, но не тренер - удаляем и создаем тренера
            session.delete(user)
            session.commit()
        
        # Создаем нового тренера
        coach = db_create_user(
            session=session,
            telegram_id=telegram_id,
            username=username,
            first_name=first_name,
            role="coach",
            sport_type=sport_type
        )
        
        return coach

