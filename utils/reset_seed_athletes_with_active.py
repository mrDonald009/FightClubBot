"""
Сброс и генерация тестовых спортсменов/абонементов с активными абонементами.

Что делает:
- Делает бэкап SQLite
- Без флага --add-only: полностью очищает операционные данные (спортсмены, абонементы,
  посещения, заявки, заморозки, расписание trainings, global_freezes и т.д.);
  не трогает coaches, sport_types, admins, assistants
- Создаёт спортсменов и абонементы с рандомными данными
- Создает абонементы: часть неактивных, часть активных с правильными датами

Запуск:
  python utils/reset_seed_athletes_with_active.py --count 25
  python utils/reset_seed_athletes_with_active.py --count 30 --coach-telegram-id 26655492 --db database/club_dev.db
  python utils/reset_seed_athletes_with_active.py --count 30 --coaches 416035374,26655492 --db database/club_dev.db
"""

import sys
from pathlib import Path

# Репозиторий в PYTHONPATH до любых импортов utils / database (запуск: python utils/...py из корня)
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import argparse
import os
import random
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Tuple, List

from utils.time_utils import now_moscow
from database.db_utils import _find_nearest_training_date, _calculate_12th_training_date
from utils.discipline_keys import discipline_key_for

# Windows/PowerShell часто падает на emoji/юникод в выводе (cp1251/cp866).
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _now() -> datetime:
    return now_moscow()


def _dt_str(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _random_date_between(start: datetime, end: datetime) -> datetime:
    """Случайная дата между start и end (включительно), с временем 00:00:00."""
    if end < start:
        start, end = end, start
    delta_days = (end.date() - start.date()).days
    days = random.randint(0, max(delta_days, 0))
    d = start.date() + timedelta(days=days)
    return datetime(d.year, d.month, d.day, 0, 0, 0)


def _random_phone_ru() -> str:
    """Формат как в боте: +7-925-123-45-67"""
    a = random.randint(900, 999)
    b = random.randint(100, 999)
    c = random.randint(10, 99)
    d = random.randint(10, 99)
    return f"+7-{a:03d}-{b:03d}-{c:02d}-{d:02d}"


@dataclass
class CoachInfo:
    id: int
    telegram_id: Optional[int]
    sport_type_id: Optional[int]
    sport_type_name: Optional[str]
    sport_type_legacy: Optional[str]

    @property
    def sport_type(self) -> str:
        return (self.sport_type_name or self.sport_type_legacy or "MMA").strip()


def _get_coach(cur: sqlite3.Cursor, coach_telegram_id: Optional[int]) -> CoachInfo:
    if coach_telegram_id is not None:
        cur.execute(
            """
            SELECT c.id, c.telegram_id, c.sport_type_id, st.name as sport_type_name, c.sport_type as sport_type_legacy
            FROM coaches c
            LEFT JOIN sport_types st ON st.id = c.sport_type_id
            WHERE c.telegram_id = ?
            """,
            (coach_telegram_id,),
        )
        row = cur.fetchone()
        if not row:
            raise RuntimeError(
                f"Тренер с telegram_id={coach_telegram_id} не найден в таблице coaches."
            )
    else:
        cur.execute(
            """
            SELECT c.id, c.telegram_id, c.sport_type_id, st.name as sport_type_name, c.sport_type as sport_type_legacy
            FROM coaches c
            LEFT JOIN sport_types st ON st.id = c.sport_type_id
            ORDER BY c.id ASC
            LIMIT 1
            """
        )
        row = cur.fetchone()
        if not row:
            raise RuntimeError("В БД нет тренеров (таблица coaches пустая). Сначала создайте тренера.")
    return CoachInfo(
        id=row[0],
        telegram_id=row[1],
        sport_type_id=row[2],
        sport_type_name=row[3],
        sport_type_legacy=row[4],
    )


def _resolve_db_path(cli_db: Optional[str]) -> Path:
    """Определить путь к SQLite БД: --db -> DATABASE_URL -> fallback."""
    if cli_db:
        return Path(cli_db)

    database_url = os.getenv("DATABASE_URL", "sqlite:///database/club.db")
    if database_url.startswith("sqlite:///"):
        return Path(database_url.replace("sqlite:///", "", 1))

    raise ValueError(
        "Поддерживается только sqlite DATABASE_URL для этого скрипта. "
        "Передайте --db database/club_dev.db"
    )


def _backup_db(db_path: Path) -> Path:
    if not db_path.exists():
        raise FileNotFoundError(f"База не найдена: {db_path}")
    ts = now_moscow().strftime("%Y%m%d-%H%M%S")
    backup_path = db_path.with_suffix(f".db.bak-{ts}")
    shutil.copy2(db_path, backup_path)
    return backup_path


def _table_exists(cur: sqlite3.Cursor, name: str) -> bool:
    cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (name,),
    )
    return cur.fetchone() is not None


def _operational_data_counts_line(cur: sqlite3.Cursor) -> str:
    """Строка для лога: сколько строк в ключевых таблицах (если таблица есть)."""
    tables = (
        "athletes",
        "subscriptions",
        "attendances",
        "trainings",
        "global_freezes",
        "global_freeze_applications",
        "athlete_freezes",
    )
    parts: List[str] = []
    for tbl in tables:
        if not _table_exists(cur, tbl):
            continue
        cur.execute(f"SELECT COUNT(*) FROM {tbl}")
        parts.append(f"{tbl}={cur.fetchone()[0]}")
    return ", ".join(parts) if parts else "(нет таблиц)"


def _wipe_operational_data_except_staff(cur: sqlite3.Cursor) -> None:
    """
    Полная очистка данных клуба перед сидом.
    Сохраняются: coaches, sport_types, admins, assistants.
    """
    cur.execute("DELETE FROM global_freeze_applications")
    cur.execute("DELETE FROM attendances")
    cur.execute("DELETE FROM restoration_requests")
    cur.execute("DELETE FROM athlete_freezes")
    cur.execute("DELETE FROM subscriptions")
    cur.execute("DELETE FROM athletes")
    cur.execute("DELETE FROM trainings")
    cur.execute("DELETE FROM global_freezes")


def _pick_full_name(first_names: List[str], last_names: List[str], patronymics: List[str]) -> str:
    return f"{random.choice(last_names)} {random.choice(first_names)} {random.choice(patronymics)}"


def _seed_athletes_and_subscriptions(
    cur: sqlite3.Cursor,
    coach: CoachInfo,
    count: int,
) -> Tuple[int, int]:
    first_names_m = ["Иван", "Павел", "Алексей", "Дмитрий", "Андрей", "Никита", "Егор", "Михаил", "Сергей", "Владимир"]
    first_names_f = ["Анна", "Мария", "Екатерина", "Алина", "Дарья", "Ксения", "Ольга", "Виктория", "Полина", "Софья"]
    last_names_m = ["Иванов", "Петров", "Сидоров", "Кузнецов", "Смирнов", "Попов", "Васильев", "Морозов", "Волков", "Фёдоров"]
    last_names_f = ["Иванова", "Петрова", "Сидорова", "Кузнецова", "Смирнова", "Попова", "Васильева", "Морозова", "Волкова", "Фёдорова"]
    patronymics_m = ["Иванович", "Петрович", "Алексеевич", "Сергеевич", "Дмитриевич", "Андреевич", "Михайлович"]
    patronymics_f = ["Ивановна", "Петровна", "Алексеевна", "Сергеевна", "Дмитриевна", "Андреевна", "Михайловна"]

    medical_pool = [
        "Нет противопоказаний",
        "Нет противопоказаний",
        "Нет противопоказаний",
        "Астма (легкая форма)",
        "Проблемы со спиной (беречь поясницу)",
        "Аллергия (весенняя)",
    ]

    cur.execute("SELECT phone FROM athletes WHERE phone IS NOT NULL")
    used_phones = {row[0] for row in cur.fetchall()}

    # Для реализма часть спортсменов будет "детская" группа
    now = _now()
    adult_start = now.replace(year=now.year - 45)
    adult_end = now.replace(year=now.year - 18)
    child_start = now.replace(year=now.year - 16)
    child_end = now.replace(year=now.year - 6)

    athletes_created = 0
    subs_created = 0

    # Подмешиваем дубликаты ФИО (уникальность у нас по телефону)
    duplicate_name: Optional[str] = None

    for i in range(count):
        is_female = random.random() < 0.25
        is_child = random.random() < 0.35
        age_group = "children" if is_child else "adults"

        if is_female:
            full_name = _pick_full_name(first_names_f, last_names_f, patronymics_f)
        else:
            full_name = _pick_full_name(first_names_m, last_names_m, patronymics_m)

        # С вероятностью 10% повторяем ФИО для проверки дубликатов
        if duplicate_name and random.random() < 0.10:
            full_name = duplicate_name
        elif duplicate_name is None and random.random() < 0.15:
            duplicate_name = full_name

        # Уникальный телефон
        phone = _random_phone_ru()
        while phone in used_phones:
            phone = _random_phone_ru()
        used_phones.add(phone)

        birth_date = _random_date_between(child_start, child_end) if is_child else _random_date_between(adult_start, adult_end)
        medical_info = random.choice(medical_pool)
        created_at = _dt_str(_now())

        # telegram_id оставляем NULL (у большинства спортсменов нет Telegram)
        cur.execute(
            """
            INSERT INTO athletes (
                telegram_id, full_name, phone, birth_date, height, weight, medical_info,
                sport_type, age_group, created_by, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                None,
                full_name,
                phone,
                _dt_str(birth_date),
                None,
                None,
                medical_info,
                coach.sport_type,
                age_group,
                coach.id,
                created_at,
            ),
        )
        athlete_id = cur.lastrowid
        athletes_created += 1

        sub_format = "individual" if random.random() < 0.35 else "group"
        discipline_key = discipline_key_for(coach.sport_type, format=sub_format)
        responsible_coach_id = coach.id

        is_frozen = 0
        frozen_from: Optional[str] = None
        frozen_until: Optional[str] = None
        frozen_days_total = 0
        frozen_training_days_total = 0

        r = random.random()
        if r < 0.22:
            subscription_type = None
            is_active = 0
            trainings_total = None
            trainings_remaining = None
            start_date = None
            end_date = None
        elif r < 0.58:
            subscription_type = "monthly"
            is_active = 1
            trainings_total = 12
            trainings_remaining = 12
            days_ago = random.randint(0, 15)
            coach_selected_date = _now() - timedelta(days=days_ago)
            start_date_dt = _find_nearest_training_date(coach_selected_date, coach.sport_type, age_group)
            end_date_dt = _calculate_12th_training_date(start_date_dt, coach.sport_type, age_group)
            start_date = _dt_str(start_date_dt)
            end_date = _dt_str(end_date_dt)
        elif r < 0.73:
            subscription_type = "single"
            is_active = 0
            trainings_total = 1
            trainings_remaining = 1
            start_date = None
            end_date = None
        elif r < 0.86:
            subscription_type = "monthly"
            is_active = 1
            trainings_total = 12
            trainings_remaining = random.randint(3, 9)
            days_ago = random.randint(5, 25)
            coach_selected_date = _now() - timedelta(days=days_ago)
            start_date_dt = _find_nearest_training_date(coach_selected_date, coach.sport_type, age_group)
            end_date_dt = _calculate_12th_training_date(start_date_dt, coach.sport_type, age_group)
            start_date = _dt_str(start_date_dt)
            end_date = _dt_str(end_date_dt)
        elif r < 0.96:
            subscription_type = "monthly"
            is_active = 1
            trainings_total = 12
            trainings_remaining = 12
            days_ago = random.randint(0, 10)
            coach_selected_date = _now() - timedelta(days=days_ago)
            start_date_dt = _find_nearest_training_date(coach_selected_date, coach.sport_type, age_group)
            end_date_dt = _calculate_12th_training_date(start_date_dt, coach.sport_type, age_group)
            start_date = _dt_str(start_date_dt)
            end_date = _dt_str(end_date_dt)
            is_frozen = 1
            ff = start_date_dt + timedelta(days=random.randint(1, 5))
            fu = ff + timedelta(days=random.randint(3, 10))
            frozen_from = _dt_str(ff)
            frozen_until = _dt_str(fu)
            frozen_days_total = random.randint(3, 10)
            frozen_training_days_total = random.randint(1, 3)
        else:
            subscription_type = "monthly"
            is_active = 1
            trainings_total = 12
            trainings_remaining = random.randint(1, 3)
            days_ago = random.randint(20, 50)
            coach_selected_date = _now() - timedelta(days=days_ago)
            start_date_dt = _find_nearest_training_date(coach_selected_date, coach.sport_type, age_group)
            end_date_dt = _calculate_12th_training_date(start_date_dt, coach.sport_type, age_group)
            start_date = _dt_str(start_date_dt)
            end_date = _dt_str(end_date_dt)

        cur.execute(
            """
            INSERT INTO subscriptions (
                athlete_id, discipline_key, responsible_coach_id, sport_type_id, sport_type, subscription_type,
                start_date, end_date, trainings_total, trainings_remaining,
                is_active, total_restored, restored_this_month, created_at,
                is_frozen, frozen_from, frozen_until, frozen_days_total, frozen_training_days_total
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                athlete_id,
                discipline_key,
                responsible_coach_id,
                coach.sport_type_id,
                coach.sport_type,
                subscription_type,
                start_date,
                end_date,
                trainings_total,
                trainings_remaining,
                is_active,
                0,
                0,
                _dt_str(_now()),
                is_frozen,
                frozen_from,
                frozen_until,
                frozen_days_total,
                frozen_training_days_total,
            ),
        )
        subs_created += 1

    return athletes_created, subs_created


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=25, help="Сколько спортсменов создать")
    parser.add_argument("--seed", type=int, default=None, help="Seed для random (для воспроизводимости)")
    parser.add_argument(
        "--add-only",
        action="store_true",
        help="Не выполнять полную очистку перед генерацией (только добавить к существующим данным)",
    )
    parser.add_argument("--db", type=str, default=None, help="Путь к SQLite БД (например: database/club_dev.db)")
    parser.add_argument(
        "--coach-telegram-id",
        type=int,
        default=None,
        help="Telegram ID тренера из coaches.telegram_id (иначе берётся первый тренер по coaches.id)",
    )
    parser.add_argument(
        "--coaches",
        type=str,
        default=None,
        help="Несколько тренеров: telegram id через запятую; на каждого создаётся --count спортсменов (приоритет над --coach-telegram-id)",
    )
    args = parser.parse_args()

    if args.count <= 0:
        raise SystemExit("--count должен быть > 0")

    if args.seed is not None:
        random.seed(args.seed)

    if args.coaches:
        coach_telegram_ids: List[Optional[int]] = []
        for part in args.coaches.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                coach_telegram_ids.append(int(part))
            except ValueError:
                raise SystemExit(f"Некорректный id в --coaches: {part!r}")
        if not coach_telegram_ids:
            raise SystemExit("--coaches не содержит ни одного id")
    else:
        coach_telegram_ids = [args.coach_telegram_id]

    db_path = _resolve_db_path(args.db)
    os.makedirs(db_path.parent, exist_ok=True)

    backup = _backup_db(db_path)
    print(f"[OK] Backup created: {backup}")
    print(f"[INFO] DB path: {db_path}")

    con = sqlite3.connect(str(db_path))
    try:
        con.execute("PRAGMA foreign_keys = ON")
        cur = con.cursor()

        if not args.add_only:
            print(f"[INFO] Перед сидом: {_operational_data_counts_line(cur)}")
            print(
                "[INFO] Полная очистка операционных данных "
                "(спортсмены, абонементы, посещения, расписание, глобальные заморозки…); "
                "coaches / sport_types / admins / assistants не изменяются."
            )
            try:
                _wipe_operational_data_except_staff(cur)
            except sqlite3.OperationalError as e:
                msg = str(e).lower()
                if "no such table" in msg:
                    print(f"[WARN] Ошибка схемы ({e}). Повтор с foreign_keys=OFF...")
                    con.execute("PRAGMA foreign_keys = OFF")
                    _wipe_operational_data_except_staff(cur)
                else:
                    raise
            print(f"[INFO] После очистки: {_operational_data_counts_line(cur)}")
        else:
            print("[INFO] Режим --add-only: очистка пропущена, данные только дополняются")
            con.execute("PRAGMA foreign_keys = OFF")

        created_athletes = 0
        created_subs = 0
        for tid in coach_telegram_ids:
            coach = _get_coach(cur, tid)
            print(
                f"[INFO] Coach: id={coach.id}, telegram_id={coach.telegram_id}, "
                f"sport_type='{coach.sport_type}', sport_type_id={coach.sport_type_id} → {args.count} спортсменов"
            )
            a, s = _seed_athletes_and_subscriptions(cur, coach, args.count)
            created_athletes += a
            created_subs += s

        con.commit()

        # Итоги
        cur.execute("SELECT COUNT(*) FROM athletes")
        athletes_cnt = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM subscriptions")
        subs_cnt = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM subscriptions WHERE is_active = 1")
        active_subs_cnt = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM attendances")
        att_cnt = cur.fetchone()[0]

        print(f"[OK] Done. Created athletes: {created_athletes}, subscriptions: {created_subs}")
        print(f"[INFO] Current DB: athletes={athletes_cnt}, subscriptions={subs_cnt}, active_subscriptions={active_subs_cnt}, attendances={att_cnt}")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())









