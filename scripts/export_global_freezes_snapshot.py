#!/usr/bin/env python3
"""
Экспорт таблицы global_freezes из SQLite в JSON для tests/fixtures/.

После git pull тесты и разработчики используют закоммиченный снимок без доступа к серверу.

На сервере (пример):
  cd /home/cat/FightClubBot-dev
  source .venv/bin/activate
  python scripts/export_global_freezes_snapshot.py \\
    --db database/club_dev.db \\
    -o /tmp/global_freezes_snapshot.json

Скопируйте файл в репозиторий и закоммитьте:
  tests/fixtures/global_freezes_snapshot.json
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Export global_freezes rows to fixture JSON")
    parser.add_argument(
        "--db",
        required=True,
        help="Путь к файлу SQLite (например database/club_dev.db)",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="global_freezes_snapshot.json",
        help="Куда записать JSON (по умолчанию ./global_freezes_snapshot.json)",
    )
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.is_file():
        raise SystemExit(f"Файл БД не найден: {db_path}")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.execute(
        "SELECT id, title, start_date, end_date, is_active, created_at, created_by "
        "FROM global_freezes ORDER BY id ASC"
    )
    rows_out = []
    for r in cur.fetchall():
        d = dict(r)
        d["is_active"] = bool(d["is_active"])
        for key in ("start_date", "end_date", "created_at"):
            if d[key] is not None:
                s = str(d[key])
                if " " in s and "T" not in s:
                    s = s.replace(" ", "T", 1)
                d[key] = s
        rows_out.append(d)
    conn.close()

    payload = {
        "version": 1,
        "table": "global_freezes",
        "description": "Экспорт с сервера; закоммитить в tests/fixtures/global_freezes_snapshot.json",
        "rows": rows_out,
    }
    out_path = Path(args.output)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Записано строк: {len(rows_out)} -> {out_path.resolve()}")


if __name__ == "__main__":
    main()
