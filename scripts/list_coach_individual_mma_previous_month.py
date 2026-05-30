#!/usr/bin/env python3
"""
Индивидуальные тренировки MMA у тренера за прошлый календарный месяц (Europe/Moscow).

Примеры:
  python scripts/list_coach_individual_mma_previous_month.py
  python scripts/list_coach_individual_mma_previous_month.py --coach-telegram-id 123456789
  python scripts/list_coach_individual_mma_previous_month.py --coach-id 2 --db database/club.db
  python scripts/list_coach_individual_mma_previous_month.py --year 2026 --month 4
"""
from __future__ import annotations

import argparse
import calendar
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

# корень репозитория в PYTHONPATH
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass


def _apply_db_arg(db_path: Optional[str]) -> None:
    if not db_path:
        return
    p = str(db_path)
    if p.startswith("sqlite:"):
        os.environ["DATABASE_URL"] = p
    else:
        os.environ["DATABASE_URL"] = f"sqlite:///{p}"


def _previous_calendar_month(now: datetime) -> Tuple[int, int]:
    if now.month == 1:
        return now.year - 1, 12
    return now.year, now.month - 1


def _month_bounds_moscow(year: int, month: int, app_tz) -> Tuple[datetime, datetime]:
    start = datetime(year, month, 1, 0, 0, 0, tzinfo=app_tz)
    if month == 12:
        end = datetime(year + 1, 1, 1, 0, 0, 0, tzinfo=app_tz)
    else:
        end = datetime(year, month + 1, 1, 0, 0, 0, tzinfo=app_tz)
    return start, end


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Индивидуальные тренировки MMA тренера за календарный месяц"
    )
    parser.add_argument(
        "--db",
        help="Путь к SQLite (например database/club.db) или полный DATABASE_URL",
    )
    parser.add_argument("--coach-id", type=int, help="id из таблицы coaches")
    parser.add_argument("--coach-telegram-id", type=int, help="Telegram ID тренера")
    parser.add_argument("--year", type=int, help="Год (по умолчанию — прошлый месяц)")
    parser.add_argument("--month", type=int, help="Месяц 1–12 (по умолчанию — прошлый)")
    args = parser.parse_args()

    _apply_db_arg(args.db)

    from sqlalchemy import func, or_
    from sqlalchemy.exc import OperationalError

    from database.db_utils.training_slots import TRAINING_FORMAT_INDIVIDUAL
    from database.models import Athlete, Attendance, Coach, Session, Subscription, Training
    from utils.time_utils import APP_TZ, now_moscow

    def _individual_training_filter():
        return func.lower(func.coalesce(func.trim(Training.training_format), "")) == (
            TRAINING_FORMAT_INDIVIDUAL
        )

    def _resolve_coach(
        session, coach_id: Optional[int], coach_telegram_id: Optional[int]
    ) -> Coach:
        if coach_id is not None:
            coach = session.query(Coach).filter_by(id=coach_id).first()
            if not coach:
                raise SystemExit(f"Тренер с id={coach_id} не найден")
            return coach
        if coach_telegram_id is not None:
            coach = session.query(Coach).filter_by(telegram_id=coach_telegram_id).first()
            if not coach:
                raise SystemExit(f"Тренер с telegram_id={coach_telegram_id} не найден")
            return coach
        coaches = (
            session.query(Coach)
            .filter(
                Coach.is_active.is_(True),
                or_(Coach.sport_type == "MMA", Coach.sport_type.ilike("mma")),
            )
            .order_by(Coach.id)
            .all()
        )
        if not coaches:
            raise SystemExit(
                "Активные тренеры MMA не найдены. Укажите --coach-id или --coach-telegram-id"
            )
        if len(coaches) > 1:
            print("Найдено несколько тренеров MMA, используется первый (укажите --coach-id):")
            for c in coaches:
                name = c.first_name or c.username or "—"
                print(f"  id={c.id} telegram_id={c.telegram_id} {name}")
        return coaches[0]

    def _athlete_name_for_training(session, training: Training) -> str:
        if not training.training_date:
            return "—"
        sub = (
            session.query(Subscription)
            .join(Athlete, Subscription.athlete_id == Athlete.id)
            .filter(
                Subscription.subscription_type == "individual",
                Subscription.sport_type == training.sport_type,
                func.strftime("%Y-%m-%d %H:%M", Subscription.start_date)
                == func.strftime("%Y-%m-%d %H:%M", Training.training_date),
            )
            .first()
        )
        if sub and sub.athlete:
            return sub.athlete.full_name or "athlete_id=%s" % sub.athlete_id
        att = (
            session.query(Attendance)
            .join(Athlete, Attendance.athlete_id == Athlete.id)
            .filter(Attendance.training_id == training.id)
            .first()
        )
        if att and att.athlete:
            return att.athlete.full_name or "athlete_id=%s" % att.athlete_id
        return "—"

    def list_individual_mma_trainings(
        session, coach: Coach, year: int, month: int
    ) -> List[Training]:
        period_start, period_end = _month_bounds_moscow(year, month, APP_TZ)
        return (
            session.query(Training)
            .filter(
                Training.coach_id == coach.id,
                Training.sport_type == "MMA",
                Training.is_cancelled.is_(False),
                Training.training_date >= period_start,
                Training.training_date < period_end,
                _individual_training_filter(),
            )
            .order_by(Training.training_date.asc())
            .all()
        )

    now = now_moscow()
    if args.year and args.month:
        year, month = args.year, args.month
    elif args.year or args.month:
        raise SystemExit("Укажите оба параметра: --year и --month")
    else:
        year, month = _previous_calendar_month(now)

    if month < 1 or month > 12:
        raise SystemExit("month должен быть от 1 до 12")

    month_name = calendar.month_name[month]
    session = Session()
    try:
        coach = _resolve_coach(session, args.coach_id, args.coach_telegram_id)
        try:
            trainings = list_individual_mma_trainings(session, coach, year, month)
        except OperationalError as exc:
            if "training_format" in str(exc).lower():
                raise SystemExit(
                    "В БД нет колонки trainings.training_format. "
                    "Нужна актуальная БД (миграция / club_dev.db с сервера)."
                ) from exc
            raise

        coach_label = coach.first_name or coach.username or "id=%s" % coach.id
        print()
        print("Тренер: %s (id=%s, telegram_id=%s)" % (coach_label, coach.id, coach.telegram_id))
        print("Период: %02d.%d (%s), MMA, индивидуальные" % (month, year, month_name))
        print("DATABASE_URL: %s" % os.getenv("DATABASE_URL", "—"))
        print("Найдено слотов: %d" % len(trainings))
        print("-" * 60)

        if not trainings:
            print("Нет индивидуальных тренировок MMA за этот месяц.")
            return

        for tr in trainings:
            dt = tr.training_date
            dt_str = dt.strftime("%d.%m.%Y %H:%M") if dt else "—"
            athlete = _athlete_name_for_training(session, tr)
            att = session.query(Attendance).filter_by(training_id=tr.id).first()
            if att is None:
                status = "не отмечено"
            elif att.attended:
                status = "был"
            else:
                status = "не был"
            print(
                "  id=%s  %s  спортсмен: %s  посещение: %s"
                % (tr.id, dt_str, athlete, status)
            )
        print("-" * 60)
    finally:
        session.close()


if __name__ == "__main__":
    main()
