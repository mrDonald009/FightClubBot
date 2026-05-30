#!/usr/bin/env python3
"""Аудит ролей staff: дубли coach+admin, пересечения telegram_id, риски при разделении ролей."""
from __future__ import print_function

import argparse
import os
import sqlite3
import sys


def _resolve_db_path(path: str) -> str:
    if path:
        return path
    url = os.getenv("DATABASE_URL", "sqlite:///database/club.db")
    if url.startswith("sqlite:///"):
        p = url.replace("sqlite:///", "", 1)
        if not os.path.isabs(p):
            root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            p = os.path.join(root, p)
        return p
    raise SystemExit("Поддерживается только sqlite:///… (укажите --db путь к файлу)")


def main():
    parser = argparse.ArgumentParser(description="Аудит coaches/admins перед разделением ролей")
    parser.add_argument(
        "--db",
        help="Путь к SQLite (иначе DATABASE_URL или database/club.db)",
    )
    args = parser.parse_args()
    db_path = _resolve_db_path(args.db)
    if not os.path.isfile(db_path):
        print("Файл БД не найден:", db_path, file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    print("=== Аудит staff-ролей ===")
    print("БД:", db_path)
    print()

    cur.execute("SELECT COUNT(*) FROM coaches")
    n_coaches = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM coaches WHERE is_active = 1 OR is_active IS NULL")
    n_coaches_active = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM admins")
    n_admins = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM admins WHERE is_active = 1 OR is_active IS NULL")
    n_admins_active = cur.fetchone()[0]
    print("Тренеров (coaches): {} (активных ~{})".format(n_coaches, n_coaches_active))
    print("Админов (admins): {} (активных ~{})".format(n_admins, n_admins_active))
    print()

    # Дубль coach + admin на одном telegram_id
    cur.execute(
        """
        SELECT c.id AS coach_id, c.telegram_id, c.first_name AS coach_name,
               c.is_active AS coach_active, c.sport_type,
               a.id AS admin_id, a.first_name AS admin_name, a.is_active AS admin_active
        FROM coaches c
        INNER JOIN admins a ON a.telegram_id = c.telegram_id
        ORDER BY c.telegram_id
        """
    )
    dual = cur.fetchall()
    print("--- Дубли: один telegram_id и в coaches, и в admins ---")
    if not dual:
        print("  Нет. При запрете dual role никто не пострадает.")
    else:
        print(
            "  ВНИМАНИЕ: {} записей. Бот резолвит admin раньше coach — меню админа, "
            "но dual запрещён: удалите одну запись.".format(len(dual))
        )
        for r in dual:
            cur.execute(
                "SELECT COUNT(*) FROM athletes WHERE created_by = ?", (r["coach_id"],)
            )
            ath = cur.fetchone()[0]
            cur.execute(
                "SELECT COUNT(*) FROM trainings WHERE coach_id = ?", (r["coach_id"],)
            )
            tr = cur.fetchone()[0]
            print(
                "  tg={} coach_id={} ({}) sport={} athletes={} trainings={} | admin_id={} ({})".format(
                    r["telegram_id"],
                    r["coach_id"],
                    r["coach_name"] or "—",
                    r["sport_type"] or "—",
                    ath,
                    tr,
                    r["admin_id"],
                    r["admin_name"] or "—",
                )
            )
    print()

    # telegram_id только admin (без coach) — останутся админами
    cur.execute(
        """
        SELECT a.id, a.telegram_id, a.first_name, a.username, a.is_active
        FROM admins a
        WHERE NOT EXISTS (SELECT 1 FROM coaches c WHERE c.telegram_id = a.telegram_id)
        ORDER BY a.telegram_id
        """
    )
    admin_only = cur.fetchall()
    print("--- Только admins (без строки в coaches) — OK для роли club admin ---")
    if not admin_only:
        print("  Нет чистых админов (все админы дублируют тренеров или таблица пуста).")
    else:
        for r in admin_only:
            print(
                "  admin_id={} tg={} name={} active={}".format(
                    r["id"], r["telegram_id"], r["first_name"] or "—", r["is_active"]
                )
            )
    print()

    # telegram_id только coach
    cur.execute(
        """
        SELECT c.id, c.telegram_id, c.first_name, c.sport_type, c.is_active
        FROM coaches c
        WHERE NOT EXISTS (SELECT 1 FROM admins a WHERE a.telegram_id = c.telegram_id)
        ORDER BY c.telegram_id
        """
    )
    coach_only = cur.fetchall()
    print("--- Только coaches (без admins) — {} шт.".format(len(coach_only)))
    for r in coach_only[:15]:
        print(
            "  coach_id={} tg={} name={} sport={}".format(
                r["id"], r["telegram_id"], r["first_name"] or "—", r["sport_type"] or "—"
            )
        )
    if len(coach_only) > 15:
        print("  … и ещё {}".format(len(coach_only) - 15))
    print()

    # Пересечение с athletes / assistants (тот же telegram_id)
    for table, label in (("athletes", "спортсмен"), ("assistants", "ассистент")):
        try:
            cur.execute(
                """
                SELECT t.telegram_id, t.id, c.id AS coach_id, a.id AS admin_id
                FROM {tbl} t
                LEFT JOIN coaches c ON c.telegram_id = t.telegram_id
                LEFT JOIN admins a ON a.telegram_id = t.telegram_id
                WHERE t.telegram_id IS NOT NULL
                  AND (c.id IS NOT NULL OR a.id IS NOT NULL)
                """.format(tbl=table)
            )
            rows = cur.fetchall()
        except sqlite3.OperationalError:
            continue
        print("--- {} с тем же telegram_id, что staff ---".format(label.capitalize()))
        if not rows:
            print("  Нет.")
        else:
            for r in rows:
                print(
                    "  tg={} {}_id={} coach_id={} admin_id={}".format(
                        r["telegram_id"],
                        table[:-1],
                        r["id"],
                        r["coach_id"],
                        r["admin_id"],
                    )
                )
        print()

    # Итог
    print("=== Итог для корректировки «один id — одна роль» ===")
    if not dual:
        print("Блокеров нет: можно вводить запрет dual coach+admin без потери доступа.")
        if not admin_only and n_admins > 0:
            print(
                "Замечание: все админы сейчас дублируют тренеров — отдельная роль admin "
                "в боте для них не используется (меню admin не откроется, пока есть coach)."
            )
    else:
        print(
            "Нужно решение по {} dual-записям: оставить coach ИЛИ admin, вторую строку удалить/деактивировать.".format(
                len(dual)
            )
        )
        print("Пока coach не удалён — бот не покажет меню админа для этих tg.")

    conn.close()


if __name__ == "__main__":
    main()
