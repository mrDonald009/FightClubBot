"""Загрузка снимка таблицы global_freezes из JSON в SQLAlchemy-сессию."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from database.models import GlobalFreeze

_FIXTURE_DIR = Path(__file__).resolve().parent
DEFAULT_SNAPSHOT_PATH = _FIXTURE_DIR / "global_freezes_snapshot.json"


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    if " " in s and "T" not in s:
        s = s.replace(" ", "T", 1)
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def load_global_freezes_snapshot(session, path: Path | None = None) -> list[GlobalFreeze]:
    """
    Вставляет строки из JSON в сессию (пустая таблица global_freezes).
    Возвращает список ORM-объектов.
    """
    path = path or DEFAULT_SNAPSHOT_PATH
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)
    if payload.get("version") != 1:
        raise ValueError(f"Неподдерживаемая версия снимка: {payload.get('version')}")
    rows = payload.get("rows") or []
    out: list[GlobalFreeze] = []
    for row in rows:
        gf = GlobalFreeze(
            id=row["id"],
            title=row["title"],
            start_date=_parse_dt(row["start_date"]),
            end_date=_parse_dt(row["end_date"]),
            is_active=bool(row.get("is_active", True)),
            created_by=row.get("created_by"),
            created_at=_parse_dt(row["created_at"]) or datetime.utcnow(),
        )
        session.add(gf)
        out.append(gf)
    session.commit()
    return out
