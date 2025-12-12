# Архитектура FightClubBot

## Обзор

Проект использует многослойную архитектуру, разработанную для легкого масштабирования и поддержки.

## Структура проекта

```
FightClubBot/
├── bot_new.py              # Точка входа приложения
├── core/                   # Ядро приложения
│   ├── __init__.py
│   ├── config.py          # Конфигурация
│   ├── database.py        # Менеджер сессий БД
│   ├── exceptions.py      # Исключения приложения
│   ├── middleware.py      # Middleware (auth, logging, errors)
│   ├── application.py     # Фабрика приложения и регистратор
│   └── startup.py         # Инициализационные задачи
├── handlers/              # Обработчики Telegram-команд
│   ├── __init__.py
│   ├── router.py          # Регистрация всех обработчиков
│   ├── start.py           # Обработчик /start
│   ├── coach_handlers.py  # Обработчики для тренеров
│   ├── card_handlers.py   # Обработчики карточек
│   └── attendance_handlers.py
├── services/              # Бизнес-логика (Service Layer)
│   ├── __init__.py
│   ├── user_service.py       # Работа с пользователями
│   ├── athlete_service.py    # Работа со спортсменами
│   └── subscription_service.py  # Работа с абонементами
├── database/              # Работа с БД
│   ├── models.py          # SQLAlchemy модели
│   ├── db_utils.py        # Утилиты БД
│   └── ...
├── utils/                 # Утилиты
│   ├── subscription_checker.py
│   └── training_manager.py
└── keyboards/             # Клавиатуры Telegram
    └── coach_kb.py
```

## Слои архитектуры

### 1. Точка входа (Entry Point)
**Файл:** `bot_new.py`

Минимальный файл, который:
- Настраивает логирование
- Загружает конфигурацию
- Создает и запускает приложение

### 2. Ядро (Core)

#### `core/config.py`
- Загрузка конфигурации из переменных окружения
- Валидация настроек
- Настройка базы данных

#### `core/database.py`
- Контекстный менеджер для работы с сессиями БД
- Гарантирует правильное управление транзакциями (commit/rollback)

#### `core/exceptions.py`
- Базовые исключения приложения
- Специализированные исключения для разных случаев

#### `core/middleware.py`
Декораторы для обработчиков:
- `@require_role(*roles)` - проверка прав доступа
- `@error_handler` - обработка ошибок
- `@log_handler` - логирование вызовов

#### `core/application.py`
- `ApplicationFactory` - создание приложения Telegram
- `HandlerRegistrar` - регистрация обработчиков

#### `core/startup.py`
- Инициализационные задачи при запуске
- Проверка/создание тестовых пользователей
- Проверка абонементов

### 3. Сервисный слой (Services)

Бизнес-логика вынесена в отдельный слой сервисов. Обработчики используют сервисы для выполнения операций.

**Принципы:**
- Сервисы не зависят от Telegram API
- Сервисы содержат бизнес-логику
- Легко тестировать
- Легко переиспользовать

#### Примеры сервисов:
- `UserService` - управление пользователями
- `AthleteService` - управление спортсменами
- `SubscriptionService` - управление абонементами

### 4. Обработчики (Handlers)

Обработчики Telegram-команд и сообщений. Они должны быть "тонкими" - делегировать работу сервисам.

**Структура:**
- `handlers/router.py` - централизованная регистрация всех обработчиков
- Отдельные модули для разных групп команд

### 5. База данных (Database)

- `models.py` - SQLAlchemy модели
- `db_utils.py` - утилиты для работы с БД (legacy код, постепенно переносится в сервисы)

## Принципы масштабирования

### 1. Разделение ответственности (Separation of Concerns)
- Каждый слой имеет четкую ответственность
- Бизнес-логика отделена от представления (handlers)

### 2. Dependency Injection
- Сервисы получают сессии БД через параметры
- Легко тестировать с mock-объектами

### 3. Единая точка регистрации
- Все обработчики регистрируются в `handlers/router.py`
- Легко добавлять новые обработчики

### 4. Middleware паттерн
- Общая логика (auth, logging, errors) вынесена в middleware
- Не нужно дублировать код в каждом обработчике

### 5. Сервисный слой
- Бизнес-логика в сервисах
- Легко добавлять новые функции
- Можно переиспользовать в разных местах

## Как добавить новую функциональность

### 1. Добавить новый сервис
```python
# services/new_service.py
class NewService:
    @staticmethod
    def do_something(session: Session, param: str):
        # Бизнес-логика
        pass
```

### 2. Добавить новый обработчик
```python
# handlers/new_handlers.py
from core.middleware import require_role, error_handler

@error_handler
@require_role('coach', 'admin')
async def new_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with get_db_session() as session:
        # Используем сервисы
        result = NewService.do_something(session, param)
        await update.message.reply_text(result)
```

### 3. Зарегистрировать обработчик
```python
# handlers/router.py
from handlers.new_handlers import new_command

def register_all_handlers(registrar: HandlerRegistrar):
    # ... существующие обработчики
    registrar.register(CommandHandler("new_cmd", new_command))
```

## Тестирование

Архитектура позволяет легко тестировать:

1. **Сервисы** - можно тестировать без Telegram API
2. **Middleware** - можно тестировать отдельно
3. **Handlers** - можно мокать сервисы

## Миграция существующего кода

При рефакторинге существующего кода:

1. Вынести бизнес-логику из handlers в services
2. Использовать middleware для общей логики
3. Использовать `get_db_session()` для работы с БД
4. Обрабатывать ошибки через `core.exceptions`

## Будущие улучшения

- [ ] Dependency Injection контейнер
- [ ] Кэширование (Redis)
- [ ] Асинхронные задачи (Celery/RQ)
- [ ] API слой (REST/FastAPI)
- [ ] Миграции БД (Alembic)
- [ ] Метрики и мониторинг
- [ ] Интеграционные тесты

