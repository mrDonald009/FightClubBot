# Схема базы данных FightClubBot

Диаграмма связей таблиц. Можно открыть в VS Code (расширение Mermaid), на GitHub или на [mermaid.live](https://mermaid.live).

```mermaid
erDiagram
    sport_types {
        int id PK
        string name UK
        string display_name
        bool is_active
        datetime created_at
    }

    coaches {
        int id PK
        int telegram_id UK
        string username
        string first_name
        int sport_type_id FK
        string sport_type
        datetime created_at
        bool is_active
    }

    admins {
        int id PK
        int telegram_id UK
        string username
        string first_name
        datetime created_at
        bool is_active
    }

    assistants {
        int id PK
        int telegram_id UK
        string username
        string first_name
        datetime created_at
        bool is_active
    }

    athletes {
        int id PK
        int telegram_id UK
        string full_name
        string phone
        datetime birth_date
        int height
        int weight
        text medical_info
        string sport_type
        string age_group
        int created_by FK
        datetime created_at
    }

    subscriptions {
        int id PK
        int athlete_id FK
        int sport_type_id FK
        string sport_type
        string subscription_type
        datetime start_date
        datetime end_date
        int trainings_total
        int trainings_remaining
        bool is_active
        int total_restored
        int restored_this_month
        bool is_frozen
        datetime frozen_from
        datetime frozen_until
        int frozen_days_total
        int frozen_training_days_total
        datetime created_at
    }

    trainings {
        int id PK
        string sport_type
        string age_group
        datetime training_date
        bool is_cancelled
        int coach_id FK
    }

    attendances {
        int id PK
        int athlete_id FK
        int training_id FK
        int subscription_id FK
        bool attended
        int marked_by
        datetime created_at
        bool was_restored
        text restoration_reason
    }

    restoration_requests {
        int id PK
        int athlete_id FK
        int subscription_id FK
        text missed_dates
        int restored_count
        text reason
        text notes
        int restored_by
        datetime restored_at
    }

    sport_types ||--o{ coaches : "sport_type_id"
    sport_types ||--o{ subscriptions : "sport_type_id"
    coaches ||--o{ athletes : "created_by"
    coaches ||--o{ trainings : "coach_id"
    athletes ||--o{ subscriptions : "athlete_id"
    athletes ||--o{ attendances : "athlete_id"
    athletes ||--o{ restoration_requests : "athlete_id"
    subscriptions ||--o{ attendances : "subscription_id"
    subscriptions ||--o{ restoration_requests : "subscription_id"
    trainings ||--o{ attendances : "training_id"
```

## Условные обозначения

- **PK** — первичный ключ  
- **FK** — внешний ключ  
- **UK** — уникальное значение  
- `||--o{` — связь «один ко многим»

## Изолированные таблицы

Таблицы **admins** и **assistants** не связаны внешними ключами с остальными — в них хранятся только telegram_id для проверки прав доступа.
