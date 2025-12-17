"""
Сброс и генерация тестовых спортсменов/абонементов.

Что делает:
- Делает бэкап database/club.db
- Удаляет всех текущих спортсменов и связанные записи (subscriptions/attendances/restoration_requests)
- Создает новых спортсменов с рандомными (валидными) данными
- Создает каждому спортсмену один абонемент по виду спорта тренера

Запуск:
  python utils/reset_seed_athletes.py --count 25 --seed 42
"""

import sys
import argparse
import os
import random
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Tuple, List


DB_PATH = Path("database/club.db")

# Windows/PowerShell часто падает на emoji/юникод в выводе (cp1251/cp866).
# Переключаем stdout/stderr на UTF-8 и включаем замену символов вместо падения.
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
    return datetime.utcnow()


def _dt_str(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    # SQLite нормально хранит ISO-like строки для DateTime
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _calculate_end_date(start_date: datetime, months: int = 1) -> datetime:
    """Аналог database.db_utils._calculate_end_date, но без импорта проекта."""
    year = start_date.year
    month = start_date.month + months
    day = start_date.day

    while month > 12:
        month -= 12
        year += 1

    # максимальный день в целевом месяце
    import calendar

    max_day = calendar.monthrange(year, month)[1]
    if day > max_day:
        day = max_day

    return datetime(year, month, day, start_date.hour, start_date.minute, start_date.second)


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
    sport_type_id: Optional[int]
    sport_type_name: Optional[str]
    sport_type_legacy: Optional[str]

    @property
    def sport_type(self) -> str:
        return (self.sport_type_name or self.sport_type_legacy or "MMA").strip()


def _get_primary_coach(cur: sqlite3.Cursor) -> CoachInfo:
    cur.execute(
        """
        SELECT c.id, c.sport_type_id, st.name as sport_type_name, c.sport_type as sport_type_legacy
        FROM coaches c
        LEFT JOIN sport_types st ON st.id = c.sport_type_id
        ORDER BY c.id ASC
        LIMIT 1
        """
    )
    row = cur.fetchone()
    if not row:
        raise RuntimeError("В БД нет тренеров (таблица coaches пустая). Сначала создайте тренера.")
    return CoachInfo(id=row[0], sport_type_id=row[1], sport_type_name=row[2], sport_type_legacy=row[3])


def _backup_db() -> Path:
    if not DB_PATH.exists():
        raise FileNotFoundError(f"База не найдена: {DB_PATH}")
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = DB_PATH.with_suffix(f".db.bak-{ts}")
    shutil.copy2(DB_PATH, backup_path)
    return backup_path


def _delete_all_athletes_related(cur: sqlite3.Cursor) -> None:
    # Важно: порядок удаления при включенных foreign_keys
    cur.execute("DELETE FROM attendances")
    cur.execute("DELETE FROM restoration_requests")
    cur.execute("DELETE FROM subscriptions")
    cur.execute("DELETE FROM athletes")


def _pick_full_name(first_names: List[str], last_names: List[str], patronymics: List[str]) -> str:
    return f"{random.choice(last_names)} {random.choice(first_names)} {random.choice(patronymics)}"


def _seed_athletes_and_subscriptions(
    cur: sqlite3.Cursor,
    coach: CoachInfo,
    count: int,
    mode: str,
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

    used_phones = set()

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

        # Абонемент:
        # - inactive_only: как при добавлении спортсмена в боте (неактивен, тип не определен, даты пустые)
        # - mixed: смесь для отладки
        if mode == "inactive_only":
            subscription_type = None
            is_active = 0
            trainings_total = None
            trainings_remaining = None
            start_date = None
            end_date = None
        else:
            r = random.random()
            if r < 0.55:
                subscription_type = None
                is_active = 0
                trainings_total = None
                trainings_remaining = None
                start_date = None
                end_date = None
            elif r < 0.85:
                subscription_type = "monthly"
                is_active = 1
                trainings_total = 12
                trainings_remaining = 12
                start_dt = _now() - timedelta(days=random.randint(0, 10))
                end_dt = _calculate_end_date(start_dt, months=1)
                start_date = _dt_str(start_dt)
                end_date = _dt_str(end_dt)
            else:
                subscription_type = "single"
                is_active = 0
                trainings_total = 1
                trainings_remaining = 1
                start_date = None
                end_date = None

        cur.execute(
            """
            INSERT INTO subscriptions (
                athlete_id, sport_type_id, sport_type, subscription_type,
                start_date, end_date, trainings_total, trainings_remaining,
                is_active, total_restored, restored_this_month, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                athlete_id,
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
            ),
        )
        subs_created += 1

    return athletes_created, subs_created


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=25, help="Сколько спортсменов создать")
    parser.add_argument("--seed", type=int, default=None, help="Seed для random (для воспроизводимости)")
    parser.add_argument(
        "--mode",
        choices=["inactive_only", "mixed"],
        default="inactive_only",
        help="Как создавать абонементы: inactive_only (как при добавлении в боте) или mixed (для отладки)",
    )
    args = parser.parse_args()

    if args.count <= 0:
        raise SystemExit("--count должен быть > 0")

    if args.seed is not None:
        random.seed(args.seed)

    os.makedirs(DB_PATH.parent, exist_ok=True)

    backup = _backup_db()
    print(f"[OK] Backup created: {backup}")

    con = sqlite3.connect(str(DB_PATH))
    try:
        # В проекте была смена схемы (users удалена), а в SQLite внешние ключи могли остаться.
        # Поэтому включаем FK-проверки, но при ошибке откатываемся на OFF для очистки/засева.
        con.execute("PRAGMA foreign_keys = ON")
        cur = con.cursor()

        coach = _get_primary_coach(cur)
        print(f"[INFO] Coach: id={coach.id}, sport_type='{coach.sport_type}', sport_type_id={coach.sport_type_id}")

        try:
            _delete_all_athletes_related(cur)
        except sqlite3.OperationalError as e:
            msg = str(e).lower()
            if "no such table" in msg:
                print(f"[WARN] FK schema mismatch detected ({e}). Retrying with foreign_keys=OFF...")
                con.execute("PRAGMA foreign_keys = OFF")
                _delete_all_athletes_related(cur)
            else:
                raise
        created_athletes, created_subs = _seed_athletes_and_subscriptions(cur, coach, args.count, args.mode)

        con.commit()

        # Итоги
        cur.execute("SELECT COUNT(*) FROM athletes")
        athletes_cnt = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM subscriptions")
        subs_cnt = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM attendances")
        att_cnt = cur.fetchone()[0]

        print(f"[OK] Done. Created athletes: {created_athletes}, subscriptions: {created_subs}")
        print(f"[INFO] Current DB: athletes={athletes_cnt}, subscriptions={subs_cnt}, attendances={att_cnt}")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())


