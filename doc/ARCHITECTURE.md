# Архитектура FightClubBot

Краткое описание слоёв, потока данных по тренировкам и абонементам, переменных окружения и пакета `database.db_utils`.

## Слои

| Слой | Роль | Примеры |
|------|------|---------|
| **Точка входа** | Запуск бота, логирование, конфиг | `bot.py` |
| **core** | Конфиг, сессии БД, middleware, фабрика приложения | `core/config.py`, `core/database.py`, `core/application.py`, `core/middleware.py` |
| **handlers** | Telegram: команды, callback, FSM; тонкий слой над сервисами и `db_utils` | `handlers/router.py`, `handlers/coach_handlers.py`, `handlers/card_handlers.py` |
| **services** | Бизнес-логика без привязки к Telegram | `services/user_service.py`, `services/athlete_service.py`, `services/subscription_service.py`, `services/subscription_audit_service.py` |
| **database** | Модели ORM и функции доступа к данным | `database/models.py`, пакет `database/db_utils/` |
| **utils** | Расписание, время, проверки абонемента, выбор абонемента при нескольких направлениях | `utils/training_manager.py`, `utils/time_utils.py`, `utils/subscription_checker.py`, `utils/subscription_resolve.py`, `utils/discipline_keys.py` |

Поток запроса: **Telegram → handler → (service | db_utils) → Session → models**.

## Trainings и Attendances

- **`Training`** — слот занятия: вид спорта, возрастная группа, дата/время начала, тренер, флаг отмены. Один слот может соответствовать нескольким спортсменам.
- **`Attendance`** — факт «участия/списания» для пары (спортсмен, тренировка, абонемент): посещение, восстановление, кто отметил (`marked_by`), авто-списание (`marked_by is None`).

Авто-списание и миграции создают при необходимости строку `Training`, затем `Attendance`. Остаток по абонементу (`trainings_remaining`) синхронизируется с фактическим числом завершённых невосстановленных списаний через `sync_subscription_trainings_remaining` и `calculate_actual_trainings_remaining` (см. `database/db_utils/remaining.py`).

## Абонементы, остаток, заморозки

- **Несколько абонементов на спортсмена** — уникальная пара `(athlete_id, discipline_key)` в БД; ключ направления задаётся через `utils/discipline_keys.py` (`discipline_key_for`, и т.д.). Создание: `database/db_utils/subscriptions.create_subscription` (неактивная запись с тем же `discipline_key` переиспользуется).
- **Выбор абонемента в логике** — `utils/subscription_resolve.py`: например `active_subscription_for_training`, `subscription_for_coach_sport` (для UI тренера по его виду спорта). Свойства `Athlete.current_subscription` / `subscription` остаются для совместимости как «первый активный», но не должны использоваться там, где важен конкретный вид спорта или слот.
- **Личная заморозка (абонемент)** — поля на `Subscription` (`is_frozen`, `frozen_from` / `frozen_until`, продление `end_date`); низкоуровневые функции в `freeze_personal.py` с опцией `commit`.
- **Личная заморозка (спортсмен целиком)** — `freeze_athlete` / `unfreeze_athlete`: для всех активных абонементов применяется та же логика продления, что и для одного; в БД добавляется запись `athlete_freezes`; посещения учитывают период и через поля абонемента, и через `is_training_in_athlete_personal_freeze`.
- **Массовая заморозка** — `GlobalFreeze` + `GlobalFreezeApplication` на абонемент; слоты в активном периоде не должны попадать в «использованные»; см. `database/db_utils/global_freeze.py` и `remaining.py` (EXISTS по активной GF).

При деактивации массовой заморозки `deactivate_global_freeze_and_migrate` вызывает `migrate_existing_subscription` для месячных абонементов **через отложенный импорт**, чтобы избежать циклических импортов между `global_freeze` и `migrate`.

## Пакет `database.db_utils`

Импорт по-прежнему: `from database.db_utils import ...`. Публичные имена также доступны как атрибуты модуля-пакета (в т.ч. для `monkeypatch` в тестах).

| Модуль | Содержание |
|--------|------------|
| `remaining.py` | Остаток, синхронизация, purge авто-списаний в периоде GF |
| `users.py` | Роли, поиск по Telegram, создание пользователей/спортсменов, делегат тренера для админа |
| `schedule.py` | Даты абонемента по расписанию (`_find_nearest_training_date`, `_calculate_12th_training_date`, …) |
| `subscriptions.py` | `create_subscription`, создание слотов без списания |
| `freeze_personal.py` | `freeze_subscription` / `unfreeze_subscription`; `freeze_athlete` / `unfreeze_athlete`; `is_training_in_athlete_personal_freeze` |
| `global_freeze.py` | Массовая заморозка, компактные даты для callback, `find_next_non_frozen_training_date` |
| `auto_deduct.py` | Ежедневное авто-списание после окончания слота |
| `migrate.py` | Миграция/пересчёт существующего месячного абонемента |
| `restore.py` | Восстановление списаний |
| `athlete_card.py` | Данные для карточки спортсмена (`get_athlete_card_info`, опционально `preferred_sport_type` для тренера) |

Реэкспорт из `utils.time_utils`: `now_moscow`, `training_end_time`, `TRAINING_DURATION`, `ACTIVATION_GRACE_AFTER_START` (как в прежнем монолитном `db_utils`).

## Переменные окружения (важные)

Задаются в `.env` / окружении, читаются в `core/config.py`:

- **`BOT_TOKEN`** — токен Telegram-бота.
- **`ADMIN_TELEGRAM_ID`** — Telegram ID администратора.
- **`THAI_COACH_TELEGRAM_ID`** — опционально; тренер по умолчанию при создании спортсмена от админа без своей строки в `coaches`.
- **`DATABASE_URL`**, **`APP_TIMEZONE`**, **`TRAINING_DURATION_MINUTES`**, **`ACTIVATION_GRACE_AFTER_START_MINUTES`** — БД и правила времени тренировок/активации.

Полный список см. в `core/config.py`.

## Связанные документы

- Подробное описание связей и процессов: `ARCHITECTURE_DESCRIPTION.md` в корне репозитория.
- Миграции схемы БД: `database/migration.py`, при необходимости отдельный `MIGRATION_GUIDE.md`.
