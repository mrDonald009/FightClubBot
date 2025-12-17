"""
Небольшой отчет по содержимому SQLite БД (без emoji в выводе).

Запуск:
  python utils/db_report.py
"""

import sys
import sqlite3


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


def main() -> int:
    con = sqlite3.connect("database/club.db")
    try:
        cur = con.cursor()
        athletes = cur.execute("select count(*) from athletes").fetchone()[0]
        subs = cur.execute("select count(*) from subscriptions").fetchone()[0]
        attendances = cur.execute("select count(*) from attendances").fetchone()[0]

        distinct_phones = cur.execute("select count(distinct phone) from athletes").fetchone()[0]
        null_tg = cur.execute("select count(*) from athletes where telegram_id is null").fetchone()[0]

        monthly = cur.execute("select count(*) from subscriptions where subscription_type='monthly'").fetchone()[0]
        single = cur.execute("select count(*) from subscriptions where subscription_type='single'").fetchone()[0]
        null_type = cur.execute("select count(*) from subscriptions where subscription_type is null").fetchone()[0]

        print(f"athletes={athletes} (distinct_phones={distinct_phones}, null_telegram_id={null_tg})")
        print(f"subscriptions={subs} (monthly={monthly}, single={single}, null_type={null_type})")
        print(f"attendances={attendances}")

        print("\nsample athletes (first 5):")
        for r in cur.execute(
            "select id, full_name, phone, birth_date, sport_type, age_group, created_by "
            "from athletes order by id limit 5"
        ):
            print(r)
        return 0
    finally:
        con.close()


if __name__ == '__main__':
    raise SystemExit(main())


