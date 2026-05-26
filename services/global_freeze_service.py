"""Сервис массовой заморозки: форматирование UI и вызовы доменной логики."""
import html
from datetime import datetime
from typing import List, Optional

from sqlalchemy.orm import Session

from database.db_utils import (
    apply_global_freeze,
    deactivate_global_freeze_and_migrate,
    list_active_global_freezes_overlapping_range,
)
from database.models import GlobalFreeze
from utils.time_utils import now_moscow


def parse_ui_date(text: str) -> Optional[datetime]:
    """Парсинг даты UI формата ДД.ММ.ГГГГ."""
    try:
        return datetime.strptime(text.strip(), "%d.%m.%Y")
    except Exception:
        return None


def format_current_global_freezes_html(session: Session) -> str:
    """
    Текст для UI: массовые заморозки, действующие «сейчас» (по времени и is_active).
    """
    now = now_moscow()
    rows = (
        session.query(GlobalFreeze)
        .filter(GlobalFreeze.is_active == True)
        .filter(GlobalFreeze.start_date <= now)
        .filter(GlobalFreeze.end_date >= now)
        .order_by(GlobalFreeze.id.asc())
        .all()
    )
    if not rows:
        return "📭 <b>Сейчас действующих массовых заморозок нет.</b>"

    header = (
        "📌 <b>Сейчас действует массовая заморозка:</b>"
        if len(rows) == 1
        else "📌 <b>Сейчас действуют массовые заморозки:</b>"
    )
    lines = [header]
    for g in rows:
        title = html.escape((g.title or "").strip() or "без названия")
        ds = g.start_date.strftime("%d.%m.%Y")
        de = g.end_date.strftime("%d.%m.%Y")
        lines.append(f"• ID <code>{g.id}</code> — <b>{title}</b>")
        lines.append(f"  <i>{ds} — {de}</i>")
    return "\n".join(lines)


def list_active_global_freezes(session: Session) -> List[GlobalFreeze]:
    """Все массовые заморозки с is_active=True (в т.ч. будущие по календарю)."""
    return (
        session.query(GlobalFreeze)
        .filter(GlobalFreeze.is_active == True)
        .order_by(GlobalFreeze.start_date.asc())
        .all()
    )


def format_global_freeze_history_html(session: Session, limit: int = 15) -> str:
    """Короткая история массовых заморозок (активные и неактивные)."""
    rows = (
        session.query(GlobalFreeze)
        .order_by(GlobalFreeze.id.desc())
        .limit(limit)
        .all()
    )
    if not rows:
        return "📭 <b>История массовых заморозок пуста.</b>"

    lines = [f"📚 <b>История массовых заморозок</b> (последние {len(rows)}):"]
    for g in rows:
        status = "🟢 Действует" if g.is_active else "⚪ Отключена"
        title = html.escape((g.title or "").strip() or "без названия")
        ds = g.start_date.strftime("%d.%m.%Y")
        de = g.end_date.strftime("%d.%m.%Y")
        created = g.created_at.strftime("%d.%m.%Y") if g.created_at else "—"
        initiator = str(g.created_by) if g.created_by else "не указан"
        lines.append(f"• <b>{title}</b>")
        lines.append(f"  Создано: {created}")
        lines.append(f"  Период действия: {ds}—{de}")
        lines.append(f"  Текущий статус: {status}")
        lines.append(f"  Инициатор: {initiator}")
    return "\n".join(lines)


def gf_keyboard_button_label_from_parts(gf_id: int, title: str) -> str:
    """Безопасный label: работает с примитивами, не зависит от ORM-сессии."""
    text = (title or "").strip() or "без названия"
    if len(text) > 28:
        text = text[:25] + "…"
    return f"#{gf_id} {text}"


def overlapping_global_freeze_ids(
    session: Session, start_date: datetime, end_date: datetime, max_ids: int = 5
) -> Optional[str]:
    """Список id пересекающихся активных массовых заморозок (или None)."""
    overlapping = list_active_global_freezes_overlapping_range(session, start_date, end_date)
    if not overlapping:
        return None
    ids = ", ".join(str(g.id) for g in overlapping[:max_ids])
    if len(overlapping) > max_ids:
        ids += ", …"
    return ids


def apply_global_freeze_service(
    session: Session,
    start_date: datetime,
    end_date: datetime,
    title: str,
    created_by: int,
) -> dict:
    """Обёртка над доменной функцией применения массовой заморозки."""
    return apply_global_freeze(
        session=session,
        start_date=start_date,
        end_date=end_date,
        title=title,
        created_by=created_by,
    )


def deactivate_global_freeze_service(session: Session, gf_id: int) -> dict:
    """Обёртка над доменной функцией деактивации массовой заморозки."""
    return deactivate_global_freeze_and_migrate(session, gf_id)
