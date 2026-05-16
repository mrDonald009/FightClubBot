"""
Диагностика списаний тренировок по абонементам (без emoji).

Запуск:
  python utils/check_subscription_deduction.py
  python utils/check_subscription_deduction.py --start-date 2025-12-02
"""

import sys
import argparse
import sqlite3
from datetime import datetime, timedelta, date
from utils.time_utils import today_moscow


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


SCHEDULE = {
    "MMA": {"children": [0, 2, 4], "middle": [0, 2, 4], "adults": [0, 2, 4]},
    "Тайский Бокс": {"children": [1, 3, 5], "middle": [1, 3, 5], "adults": [1, 3, 5]},
}


def _parse_dt(s: str) -> datetime:
    # SQLite may store with microseconds
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            pass
    raise ValueError(f"Cannot parse datetime: {s!r}")


def _expected_deducted(sport_type: str, age_group: str, start_dt: datetime, today: date) -> int:
    days = SCHEDULE.get(sport_type, {}).get(age_group)
    if not days:
        return 0
    d = start_dt.date()
    cnt = 0
    while d <= today:
        if d.weekday() in set(days):
            cnt += 1
        d += timedelta(days=1)
    return cnt


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="database/club.db")
    ap.add_argument("--start-date", default=None, help="YYYY-MM-DD filter for subscription.start_date date part")
    ap.add_argument("--sub-id", type=int, default=None, help="Показать детали для конкретного subscription_id")
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    try:
        cur = con.cursor()
        today = today_moscow()
        q = (
            "SELECT s.id, a.full_name, a.sport_type, a.age_group, s.start_date, s.end_date, "
            "s.trainings_total, s.trainings_remaining "
            "FROM subscriptions s JOIN athletes a ON a.id=s.athlete_id "
            "WHERE s.is_active=1 AND s.subscription_type='monthly'"
        )
        rows = cur.execute(q).fetchall()

        if args.start_date:
            rows = [r for r in rows if (r[4] or "").startswith(args.start_date)]

        if args.sub_id:
            sid = args.sub_id
            sub = cur.execute(
                "SELECT s.id, a.full_name, a.sport_type, a.age_group, s.start_date, s.end_date, s.trainings_total, s.trainings_remaining "
                "FROM subscriptions s JOIN athletes a ON a.id=s.athlete_id WHERE s.id=?",
                (sid,),
            ).fetchone()
            print("subscription:", sub)
            att = cur.execute(
                "SELECT a.id, t.training_date, a.attended, COALESCE(a.was_restored, 0), a.created_at "
                "FROM attendances a JOIN trainings t ON t.id=a.training_id WHERE a.subscription_id=? ORDER BY t.training_date",
                (sid,),
            ).fetchall()
            print("attendances_count:", len(att))
            for r in att:
                print(r)
            return 0

        print(f"today_utc={today} active_monthly_count={len(rows)}")
        for (sid, full_name, sport_type, age_group, start_date_s, end_date_s, total, remaining) in rows:
            if not start_date_s:
                continue
            start_dt = _parse_dt(start_date_s)
            exp = _expected_deducted(sport_type, age_group, start_dt, today)
            att_cnt = cur.execute("SELECT COUNT(*) FROM attendances WHERE subscription_id=?", (sid,)).fetchone()[0]
            exp_remaining = (total or 0) - exp if total is not None else None
            print(
                f"sub_id={sid} sport={sport_type} group={age_group} start={start_dt.date()} "
                f"total={total} remaining={remaining} attendances={att_cnt} expected_deducted={exp} expected_remaining={exp_remaining} name={full_name}"
            )
        return 0
    finally:
        con.close()


if __name__ == '__main__':
    raise SystemExit(main())


