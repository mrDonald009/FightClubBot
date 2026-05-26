# Руководство по миграции на новую архитектуру

## Что изменилось

### Новая структура проекта

Проект был реорганизован с использованием многослойной архитектуры:

1. **Сервисный слой** (`services/`) - вся бизнес-логика
2. **Middleware** (`core/middleware.py`) - общая логика (auth, logging, errors)
3. **Менеджер БД** (`core/database.py`) - централизованное управление сессиями
4. **Фабрика приложения** (`core/application.py`) - создание и настройка приложения
5. **Роутер обработчиков** (`handlers/router.py`) - централизованная регистрация

### Основные улучшения

#### 1. Разделение ответственности
- Бизнес-логика вынесена из handlers в services
- Handlers стали "тонкими" - только принимают запросы и вызывают сервисы

#### 2. Управление БД
**Было:**
```python
session = Session()
try:
    # работа с БД
    session.commit()
except:
    session.rollback()
finally:
    session.close()
```

**Стало:**
```python
from core.database import get_db_session

with get_db_session() as session:
    # работа с БД - автоматически commit/rollback
```

#### 3. Обработка ошибок
**Было:** Дублирование проверок в каждом обработчике

**Стало:** Используем middleware
```python
from core.middleware import require_role, error_handler

@error_handler
@require_role('coach', 'admin')
async def my_handler(update, context):
    # обработчик автоматически проверяет права и обрабатывает ошибки
```

#### 4. Регистрация обработчиков
**Было:** Все в `setup_handlers()` в `bot_new.py`

**Стало:** Централизованно в `handlers/router.py`

## Как использовать новую архитектуру

### Добавление нового сервиса

1. Создайте файл в `services/`:
```python
# services/my_service.py
from sqlalchemy.orm import Session
from core.database import get_db_session

class MyService:
    @staticmethod
    def do_something(session: Session, param: str):
        # Бизнес-логика
        return result
```

2. Добавьте в `services/__init__.py`:
```python
from .my_service import MyService

__all__ = [..., 'MyService']
```

### Добавление нового обработчика

1. Создайте обработчик в `handlers/`:
```python
# handlers/my_handlers.py
from core.middleware import require_role, error_handler
from core.database import get_db_session
from services.my_service import MyService

@error_handler
@require_role('coach')
async def my_command(update, context):
    with get_db_session() as session:
        result = MyService.do_something(session, param)
        await update.message.reply_text(result)
```

2. Зарегистрируйте в `handlers/router.py`:
```python
from handlers.my_handlers import my_command

def register_all_handlers(registrar):
    # ...
    registrar.register(CommandHandler("my_cmd", my_command))
```

### Работа с пользователями

**Используйте сервисы:**
```python
from core.database import get_db_session
from services.user_service import UserService

with get_db_session() as session:
    # Получить пользователя
    user = UserService.get_user_by_telegram_id(session, telegram_id)
    
    # Или выбросит исключение, если не найден
    user = UserService.get_user_or_raise(session, telegram_id)
    
    # Проверить права
    UserService.check_permission(user, ['coach', 'admin'])
```

### Обработка ошибок

**Используйте исключения:**
```python
from core.exceptions import UserNotFoundError, PermissionDeniedError

try:
    user = UserService.get_user_or_raise(session, telegram_id)
except UserNotFoundError:
    await update.message.reply_text("Пользователь не найден")
except PermissionDeniedError:
    await update.message.reply_text("Нет прав")
```

Или используйте декоратор `@error_handler` - он автоматически обработает все исключения.

## Обратная совместимость

Старый код продолжает работать:
- `database/db_utils/` - пакет утилит БД (импорт `from database.db_utils import ...` без изменений)
- `handlers/*.py` - обработчики работают как раньше
- Можно постепенно мигрировать код на использование сервисов

## Следующие шаги

1. Постепенно рефакторить существующие handlers для использования сервисов
2. Добавлять новые функции используя новую архитектуру
3. Писать тесты для сервисов
4. При необходимости добавить dependency injection контейнер

## Полезные ссылки

- `ARCHITECTURE.md` - полное описание архитектуры
- `core/` - ядро приложения
- `services/` - бизнес-логика
- `handlers/router.py` - регистрация обработчиков

