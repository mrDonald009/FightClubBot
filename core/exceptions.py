"""Исключения для приложения."""


class FightClubBotException(Exception):
    """Базовое исключение приложения."""
    pass


class UserNotFoundError(FightClubBotException):
    """Пользователь не найден."""
    pass


class PermissionDeniedError(FightClubBotException):
    """Доступ запрещен."""
    pass


class AthleteNotFoundError(FightClubBotException):
    """Спортсмен не найден."""
    pass


class SubscriptionNotFoundError(FightClubBotException):
    """Абонемент не найден."""
    pass


class ValidationError(FightClubBotException):
    """Ошибка валидации данных."""
    pass

