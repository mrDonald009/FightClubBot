#!/usr/bin/env python3
"""
Убрать запись спортсмена с заданным telegram_id и создать тренера по тайскому боксу.

Нужно, если человек успел нажать /start до настройки THAI_COACH_TELEGRAM_ID: тогда
ensure_test_coach не создаёт тренера («уже спортсмен»).

Запуск на сервере из каталога проекта (например FightClubBot-dev):

  # просмотр без изменений
  ENV_FILE=.env.dev python3 scripts/promote_to_thai_coach.py --telegram-id 416035374 --dry-run

  # выполнить (если у спортсмена нет абонементов и посещений)
  ENV_FILE=.env.dev python3 scripts/promote_to_thai_coach.py --telegram-id 416035374

  # если есть абонементы/посещения в dev — только осознанно
  ENV_FILE=.env.dev python3 scripts/promote_to_thai_coach.py --telegram-id 416035374 --force

После успешного запуска перезапуск сервиса не обязателен; для проверки можно /start.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    name = os.environ.get("ENV_FILE", ".env")
    for base in (Path.cwd(), ROOT):
        p = base / name
        if p.is_file():
            load_dotenv(p)
            return
    load_dotenv(ROOT / ".env")


def _purge_athlete(session, athlete) -> None:
    from database.models import (
        Attendance,
        GlobalFreezeApplication,
        RestorationRequest,
        Subscription,
    )

    aid = athlete.id
    subs = session.query(Subscription).filter_by(athlete_id=aid).all()
    sub_ids = [s.id for s in subs]

    if sub_ids:
        session.query(GlobalFreezeApplication).filter(
            GlobalFreezeApplication.subscription_id.in_(sub_ids)
        ).delete(synchronize_session=False)
        session.query(Attendance).filter(Attendance.subscription_id.in_(sub_ids)).delete(
            synchronize_session=False
        )
        session.query(RestorationRequest).filter(
            RestorationRequest.subscription_id.in_(sub_ids)
        ).delete(synchronize_session=False)

    session.query(Attendance).filter_by(athlete_id=aid).delete(synchronize_session=False)
    session.query(RestorationRequest).filter_by(athlete_id=aid).delete(
        synchronize_session=False
    )

    for s in subs:
        session.delete(s)
    session.delete(athlete)
    session.flush()


def main() -> int:
    parser = argparse.ArgumentParser(description="Спортсмен → тренер Тайский бокс по telegram_id")
    parser.add_argument("--telegram-id", type=int, required=True)
    parser.add_argument("--dry-run", action="store_true", help="Только показать, что сделали бы")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Удалить даже при наличии абонементов/посещений (опасно для боевых данных)",
    )
    args = parser.parse_args()

    _load_dotenv()

    from database.db_utils import get_user_by_telegram_id, get_user_role
    from database.models import Athlete, Attendance, Session, Subscription
    from services.user_service import UserService

    tid = args.telegram_id
    session = Session()
    try:
        user = get_user_by_telegram_id(session, tid)
        if user is None:
            print(f"Пользователь telegram_id={tid} не найден. Создаём тренера…")
            if args.dry_run:
                print("[dry-run] ensure_test_coach(..., Тайский Бокс)")
                return 0
            UserService.ensure_test_coach(
                session=session,
                telegram_id=tid,
                username="coach_thai",
                first_name="Тренер Тайский Бокс",
                sport_type="Тайский Бокс",
            )
            session.commit()
            print(f"OK: создан тренер Тайский бокс для telegram_id={tid}")
            return 0

        role = get_user_role(user)
        if role == "coach":
            print(f"Уже тренер (telegram_id={tid}). Обновление вида спорта через ensure_test_coach…")
            if args.dry_run:
                print("[dry-run] ensure_test_coach")
                return 0
            UserService.ensure_test_coach(
                session=session,
                telegram_id=tid,
                username="coach_thai",
                first_name="Тренер Тайский Бокс",
                sport_type="Тайский Бокс",
            )
            session.commit()
            print("OK")
            return 0

        if role == "admin":
            print(f"telegram_id={tid} — администратор. Скрипт не трогает админов.", file=sys.stderr)
            return 1

        if role != "athlete":
            print(f"telegram_id={tid} — роль {role}. Ожидался athlete для исправления.", file=sys.stderr)
            return 1

        athlete = user
        n_sub = session.query(Subscription).filter_by(athlete_id=athlete.id).count()
        n_att = session.query(Attendance).filter_by(athlete_id=athlete.id).count()

        print(
            f"Найден спортсмен id={athlete.id} telegram_id={tid!r}, "
            f"абонементов={n_sub}, строк посещений={n_att}"
        )

        if (n_sub > 0 or n_att > 0) and not args.force:
            print(
                "Есть данные по абонементу/посещениям. Повторите с --force, если в dev это можно стереть.",
                file=sys.stderr,
            )
            return 1

        if args.dry_run:
            print("[dry-run] удалить спортсмена и связанные строки, затем ensure_test_coach")
            return 0

        _purge_athlete(session, athlete)
        session.commit()

        UserService.ensure_test_coach(
            session=session,
            telegram_id=tid,
            username="coach_thai",
            first_name="Тренер Тайский Бокс",
            sport_type="Тайский Бокс",
        )
        session.commit()
        print(f"OK: спортсмен удалён, тренер Тайский бокс создан для telegram_id={tid}")
        return 0
    except Exception as e:
        session.rollback()
        print(f"Ошибка: {e}", file=sys.stderr)
        raise
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
