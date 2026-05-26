# Описание архитектуры и логики связей FightClubBot

Актуально для ветки с **несколькими абонементами на спортсмена** (уникальная пара `athlete_id` + `discipline_key`), персональной заморозкой на уровне спортсмена и резолвером абонементов в UI.

## 📋 Общая структура данных

### 1. **Тренер (Coach)**

**Характеристики (ORM):**
- `id` — PK, `telegram_id`, привязка к виду спорта (`sport_type_id` / legacy `sport_type`)

**Связи:**
- Создаёт спортсменов через `Athlete.created_by` → `coaches.id`
- Проводит тренировки через `Training.coach_id`
- Отмечает посещения (в Telegram хранится `marked_by` как telegram_id пользователя)

**Функциональность:**
- Добавление спортсменов, списки и календарь, отметка посещений — с учётом **вида спорта тренера** при выборе «релевантного» абонемента (см. `utils/subscription_resolve.py`).

---

### 2. **Спортсмен (Athlete)**

**Характеристики:**
- `id`, `telegram_id` (опционально), `full_name`, `phone`, `birth_date`, …
- `sport_type`, `age_group` — профиль спортсмена (для расписания и отображения; при нескольких абонементах уточнение идёт по `Subscription.sport_type` слота/абонемента)
- `created_by` — FK → `coaches.id`

**Legacy-поля в БД (миграции):** `subscription_id`, `current_subscription_id` на `athletes` могут присутствовать для старых скриптов; **источник истины** — связь `Subscription.athlete_id`.

**Связи:**
- **Один-ко-многим с абонементами:** `athlete.subscriptions` → список `Subscription`
- **Один-ко-многим с персональными заморозками:** `athlete_freezes` (запись на период «заморозил весь спортсмен»)
- **Свойства для совместимости:** `current_subscription` / `subscription` — **первый активный** абонемент (порядок не гарантирует «главный»); в новом коде предпочтительно `subscription_resolve`

**Важно:**
- У спортсмена может быть **несколько активных абонементов** в **разных направлениях** (`discipline_key`).
- Один активный абонемент на пару **`(athlete_id, discipline_key)`** (уникальное ограничение в БД после миграции).
- Активация одного направления **не отключает** остальные активные абонементы.

---

### 3. **Абонемент (Subscription)**

**Ключевые поля:**
- `athlete_id` — FK → `athletes.id`
- **`discipline_key`** — стабильный ключ направления (вид спорта × формат group/individual), см. `utils/discipline_keys.py`
- **`responsible_coach_id`** — FK → `coaches.id` (опционально; по умолчанию из `athlete.created_by` при создании)
- `sport_type`, `subscription_type` (`monthly` / `single`), даты, остатки, флаги заморозки на **уровне абонемента**

**Связи:**
- `subscription.athlete` — один спортсмен
- `subscription.attendances` — посещения, привязанные к **конкретному** абонементу

**Создание / upsert:**
- `database/db_utils/subscriptions.create_subscription` — при неактивной записи с тем же `discipline_key` строка **переиспользуется**, а не дублируется.

**Личная заморозка (два уровня):**
- На абонементе: `is_frozen`, `frozen_from`, `frozen_until`, продление `end_date` — как раньше (`freeze_subscription`).
- **На спортсмене:** при действии «заморозить спортсмена» вызывается `freeze_athlete`: для каждого активного абонемента применяется та же логика продления, плюс строка в **`athlete_freezes`** (аудит и проверки в `is_training_in_athlete_personal_freeze`).

**Массовая заморозка клуба:** по-прежнему `global_freezes` + `global_freeze_applications` на **абонементы**.

---

### 4. **Календарь тренера**

#### 4.1–4.2 — как раньше
Расписание в `TrainingManager.TRAINING_SCHEDULE`.

#### 4.3. Учёт абонементов
- Слоты в БД: спортсмены с активным `Subscription`, у которых **`Subscription.sport_type`** и возрастная группа совпадают со слотом, дата попадает в `[start_date, end_date]`.
- Режим «только расписание, без слотов»: выборка идёт по **`Subscription.sport_type`**, а не только по `Athlete.sport_type`, чтобы спортсмен с абонементом MMA и профилем «другой вид» не терялся.
- Статус посещения — по паре **`(athlete_id, subscription_id)`**.

---

### 5. **Тренировка (Training)**

Без изменений по смыслу: слот по `sport_type`, `age_group`, `training_date`, `coach_id`.

---

### 6. **Посещение (Attendance)**

- Каждая запись привязана к **`subscription_id`** — списание идёт с того абонемента, который соответствует слоту (**`active_subscription_for_training`** в `handlers/attendance_handlers.py`).
- Учитываются персональная заморозка абонемента и период **`athlete_freezes`**.

---

## 🔄 Процессы (обновлённые акценты)

### Добавление спортсмена тренером
- Создаётся абонемент с `discipline_key` для выбранного вида спорта (групповой формат по умолчанию), `responsible_coach_id` при необходимости.
- Отдельное направление — отдельная строка `Subscription` (через «создать абонемент» / активацию), без массового отключения других активных направлений.

### Отметка посещения
- Тренер видит слоты по **своему виду спорта**; абонемент выбирается по **тренировке** (вид спорта + возрастная группа).

### Авто-списание
- Обходятся **все** активные незамороженные абонементы с подходящим расписанием (`auto_deduct_daily_trainings`).

---

## 🔗 Схема связей (упрощённо)

```
Coach
  └─→ Athlete (created_by) [один-ко-многим]
        ├─→ Subscription (athlete_id) [один-ко-многим]
        │     ├─→ Attendance (subscription_id)
        │     └─→ GlobalFreezeApplication / …
        └─→ AthleteFreeze (athlete_id) [персональная заморозка «целиком»]

Training
  └─→ Attendance (training_id)
```

**Ключевая особенность:** у одного `Athlete` — **много** `Subscription`; уникальность **`(athlete_id, discipline_key)`**.

---

## 📊 Фильтрация и права доступа

### Тренер
- Свои спортсмены: `Athlete.created_by == coach.id`
- Статусы в списках и счётчики: абонемент в контексте **`resolve_coach_sport_type_name(user)`** (`subscription_for_coach_sport`)

### Администратор
- Шире права просмотра/действий по сценариям handlers

---

## 🎯 Ключевые моменты

1. **Несколько активных абонементов** — разные `discipline_key`; резолвер: `utils/subscription_resolve.py`.
2. **Личная заморозка UI** — на спортсмена (`freeze_athlete` / `unfreeze_athlete`); на стороне БД синхронизированы поля всех активных абонементов + `athlete_freezes`.
3. **Массовая заморозка** — без изменения концепции (`global_freezes`).
4. **Миграция SQLite** — `database/migration.py`: столбцы `discipline_key`, `responsible_coach_id`, таблица `athlete_freezes`, индекс `uq_subscriptions_athlete_discipline`.
