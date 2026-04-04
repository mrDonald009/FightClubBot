from datetime import datetime
from typing import Optional, Union
from sqlalchemy.orm import Session
from database.models import Admin, Assistant, Athlete, Coach, SportType

def get_user_role(user: Union[Coach, Admin, Assistant, Athlete]) -> str:
    """Определить роль пользователя"""
    if isinstance(user, Coach):
        return "coach"
    elif isinstance(user, Admin):
        return "admin"
    elif isinstance(user, Assistant):
        return "assistant"
    elif isinstance(user, Athlete):
        return "athlete"
    return "unknown"


def get_user_by_telegram_id(session: Session, telegram_id: int) -> Optional[Union[Coach, Admin, Assistant, Athlete]]:
    """Получить пользователя по telegram_id (проверяет все таблицы: coaches, admins, assistants, athletes)"""
    # Проверяем тренеров
    coach = session.query(Coach).filter_by(telegram_id=telegram_id).first()
    if coach:
        return coach
    
    # Проверяем админов
    admin = session.query(Admin).filter_by(telegram_id=telegram_id).first()
    if admin:
        return admin
    
    # Проверяем ассистентов
    assistant = session.query(Assistant).filter_by(telegram_id=telegram_id).first()
    if assistant:
        return assistant
    
    # Проверяем спортсменов
    athlete = session.query(Athlete).filter_by(telegram_id=telegram_id).first()
    if athlete:
        return athlete
    
    return None


def get_coach_by_telegram_id(session: Session, telegram_id: int) -> Optional[Coach]:
    """Строка coaches с данным telegram_id (если есть)."""
    return session.query(Coach).filter_by(telegram_id=telegram_id).first()


def get_delegate_coach_for_admin(
    session: Session, delegate_telegram_id: Optional[int] = None
) -> Optional[Coach]:
    """
    Тренер-шаблон для сценария «добавить спортсмена» под админом (created_by у нового = NULL).
    Сначала ищем по telegram_id делегата (ADMIN_DELEGATE / устар. THAI / первый в COACH_TELEGRAM_IDS),
    иначе первый тренер в БД по id.
    """
    if delegate_telegram_id is not None:
        coach = session.query(Coach).filter_by(telegram_id=delegate_telegram_id).first()
        if coach:
            return coach
    return session.query(Coach).order_by(Coach.id.asc()).first()


def get_athletes_by_coach(session: Session, coach_id: int):
    """Получить список спортсменов, созданных конкретным тренером."""
    return (
        session.query(Athlete)
        .filter(Athlete.created_by == coach_id)
        .order_by(Athlete.full_name.asc())
        .all()
    )


def get_coach_by_sport_type(session: Session, sport_type: str) -> Optional[Coach]:
    """Получить тренера по виду спорта"""
    # Сначала пытаемся найти через связь с sport_types
    sport_type_obj = session.query(SportType).filter_by(name=sport_type).first()
    if sport_type_obj:
        coach = session.query(Coach).filter(
            Coach.sport_type_id == sport_type_obj.id,
            Coach.is_active == True
        ).first()
        if coach:
            return coach
    
    # Fallback: ищем по строке sport_type (для обратной совместимости)
    return session.query(Coach).filter(
        Coach.sport_type == sport_type,
        Coach.is_active == True
    ).first()


def create_user(session: Session, telegram_id: int, username: str, first_name: str, role: str = "athlete",
                sport_type: str = None) -> Union[Coach, Admin, Assistant]:
    """Создать нового пользователя в соответствующей таблице"""
    if role == "coach":
        # Получаем sport_type_id из таблицы sport_types
        sport_type_id = None
        if sport_type:
            sport_type_obj = session.query(SportType).filter_by(name=sport_type).first()
            if sport_type_obj:
                sport_type_id = sport_type_obj.id
            else:
                # Если вида спорта нет, создаем его
                sport_type_obj = SportType(name=sport_type, display_name=sport_type)
                session.add(sport_type_obj)
                session.flush()
                sport_type_id = sport_type_obj.id
        
        if not sport_type_id:
            raise ValueError(f"Не указан вид спорта для тренера или вид спорта '{sport_type}' не найден")
        
        coach = Coach(
            telegram_id=telegram_id,
            username=username,
            first_name=first_name,
            sport_type_id=sport_type_id,
            sport_type=sport_type  # Для обратной совместимости
        )
        session.add(coach)
        session.commit()
        return coach
    
    elif role == "admin":
        admin = Admin(
            telegram_id=telegram_id,
            username=username,
            first_name=first_name
        )
        session.add(admin)
        session.commit()
        return admin
    
    elif role == "assistant":
        assistant = Assistant(
            telegram_id=telegram_id,
            username=username,
            first_name=first_name
        )
        session.add(assistant)
        session.commit()
        return assistant
    
    else:
        # Для спортсменов не создаем запись в отдельной таблице, только в athletes
        raise ValueError(f"Для создания спортсмена используйте create_athlete")


def create_athlete(
    session: Session,
    telegram_id: Optional[int],
    full_name: str,
    phone: str,
    medical_info: str,
    sport_type: str,
    age_group: str,
    created_by: Optional[int] = None,
    birth_date: datetime = None,
    height: int = None,
    weight: int = None,
    *,
    commit: bool = True,
):
    """Создать спортсмена. При commit=False только add+flush (для одной транзакции с абонементом)."""
    athlete = Athlete(
        telegram_id=telegram_id,
        full_name=full_name,
        phone=phone,
        birth_date=birth_date,
        height=height,
        weight=weight,
        medical_info=medical_info,
        sport_type=sport_type,
        age_group=age_group,
        created_by=created_by
    )
    session.add(athlete)
    session.flush()
    if commit:
        session.commit()
    return athlete
