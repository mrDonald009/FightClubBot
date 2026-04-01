"""Менеджер сессий базы данных."""
from contextlib import contextmanager
from typing import Generator
from sqlalchemy.orm import Session
from database.models import Session as DBSession


@contextmanager
def get_db_session() -> Generator[Session, None, None]:
    """
    Контекстный менеджер для работы с сессией БД.
    
    Yields:
        Session: Сессия базы данных
        
    Example:
        with get_db_session() as session:
            from database.models import Coach
            coach = session.query(Coach).first()
    """
    session = DBSession()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db_session_dependency() -> Generator[Session, None, None]:
    """
    Зависимость для использования с dependency injection.
    
    Yields:
        Session: Сессия базы данных
    """
    with get_db_session() as session:
        yield session

