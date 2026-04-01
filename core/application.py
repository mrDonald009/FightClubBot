"""Фабрика приложения и регистратор обработчиков."""
import logging
from typing import List, Type
from telegram.ext import Application, BaseHandler

from core.config import Config

logger = logging.getLogger(__name__)


class HandlerRegistrar:
    """Регистратор обработчиков для бота."""
    
    def __init__(self):
        self.handlers: List[BaseHandler] = []
    
    def register(self, handler: BaseHandler) -> None:
        """
        Зарегистрировать обработчик.
        
        Args:
            handler: Обработчик для регистрации
        """
        self.handlers.append(handler)
        logger.debug(f"Зарегистрирован обработчик: {type(handler).__name__}")
    
    def register_all(self, handlers: List[BaseHandler]) -> None:
        """
        Зарегистрировать список обработчиков.
        
        Args:
            handlers: Список обработчиков
        """
        for handler in handlers:
            self.register(handler)
    
    def apply_to_application(self, application: Application) -> None:
        """
        Применить все зарегистрированные обработчики к приложению.
        
        Args:
            application: Экземпляр приложения Telegram
        """
        for handler in self.handlers:
            application.add_handler(handler)
        logger.info(f"✅ Зарегистрировано обработчиков: {len(self.handlers)}")


class ApplicationFactory:
    """Фабрика для создания приложения бота."""
    
    @staticmethod
    def create(config: Config) -> Application:
        """
        Создать и настроить приложение бота.
        
        Args:
            config: Конфигурация приложения
            
        Returns:
            Настроенное приложение
        """
        application = Application.builder().token(config.BOT_TOKEN).build()
        logger.info("✅ Приложение Telegram создано")
        return application
    
    @staticmethod
    def setup_application(application: Application, registrar: HandlerRegistrar) -> None:
        """
        Настроить приложение с обработчиками.
        
        Args:
            application: Экземпляр приложения
            registrar: Регистратор обработчиков
        """
        registrar.apply_to_application(application)
        logger.info("✅ Приложение настроено")

