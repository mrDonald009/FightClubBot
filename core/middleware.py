"""Middleware для обработки запросов."""
import logging
from functools import wraps
from typing import Callable, Any
from telegram import Update
from telegram.ext import ContextTypes
from core.database import get_db_session
from core.exceptions import FightClubBotException, PermissionDeniedError
from services.user_service import UserService

logger = logging.getLogger(__name__)


def require_role(*allowed_roles: str):
    """
    Декоратор для проверки роли пользователя.
    
    Args:
        *allowed_roles: Разрешенные роли
        
    Example:
        @require_role('coach', 'admin')  # staff: coach и admin
        async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
            ...
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs) -> Any:
            user_id = update.effective_user.id
            
            try:
                with get_db_session() as session:
                    user = UserService.get_user_by_telegram_id(session, user_id)
                    
                    if not user:
                        await update.message.reply_text(
                            "❌ Вы не зарегистрированы. Используйте /start для регистрации."
                        )
                        return
                    
                    # Проверяем права
                    UserService.check_permission(user, list(allowed_roles))
                    
                    # Добавляем пользователя в контекст
                    context.user = user
                    
            except PermissionDeniedError:
                await update.message.reply_text("❌ У вас нет прав для выполнения этой команды")
                return
            except Exception as e:
                logger.error(f"Ошибка в middleware require_role: {e}", exc_info=True)
                await update.message.reply_text("❌ Произошла ошибка при проверке прав")
                return
            
            return await func(update, context, *args, **kwargs)
        
        return wrapper
    return decorator


def error_handler(func: Callable) -> Callable:
    """
    Декоратор для обработки ошибок в handlers.
    
    Example:
        @error_handler
        async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
            ...
    """
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs) -> Any:
        try:
            return await func(update, context, *args, **kwargs)
        except FightClubBotException as e:
            logger.warning(f"Ошибка приложения: {e}")
            await update.message.reply_text(f"❌ {str(e)}")
        except Exception as e:
            logger.error(f"Необработанная ошибка в {func.__name__}: {e}", exc_info=True)
            await update.message.reply_text(
                "❌ Произошла непредвиденная ошибка. Попробуйте позже или обратитесь к администратору."
            )
        return None
    
    return wrapper


def log_handler(func: Callable) -> Callable:
    """
    Декоратор для логирования вызовов handlers.
    
    Example:
        @log_handler
        async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
            ...
    """
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs) -> Any:
        user_id = update.effective_user.id
        username = update.effective_user.username or "N/A"
        handler_name = func.__name__
        
        logger.info(f"Handler {handler_name} вызван пользователем {user_id} (@{username})")
        
        try:
            result = await func(update, context, *args, **kwargs)
            logger.info(f"Handler {handler_name} выполнен успешно для пользователя {user_id}")
            return result
        except Exception as e:
            logger.error(f"Handler {handler_name} завершился с ошибкой для пользователя {user_id}: {e}")
            raise
    
    return wrapper

