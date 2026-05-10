"""Отчёт тренера за календарный месяц: посещаемость, база, абонементы, выручка."""
from __future__ import annotations

import html
import logging
import re
from datetime import timedelta
from typing import List, Optional, Tuple

from sqlalchemy.orm import joinedload
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from core.database import get_db_session
from database.db_utils import get_user_by_telegram_id, get_user_role
from database.db_utils.coach_report import (
    build_coach_period_report,
    coach_sport_type_name,
    month_range,
)
from database.models import Coach
from utils.time_utils import now_moscow

logger = logging.getLogger(__name__)

_MONTH_NAMES_NOM = (
    "",
    "Январь",
    "Февраль",
    "Март",
    "Апрель",
    "Май",
    "Июнь",
    "Июль",
    "Август",
    "Сентябрь",
    "Октябрь",
    "Ноябрь",
    "Декабрь",
)

_NUM_SEP = "\u202f"  # узкий пробел для тысяч


def _format_rubles(n: int) -> str:
    s = f"{int(n):,}".replace(",", _NUM_SEP)
    return f"{s} ₽"


def _period_range_human(year: int, month: int) -> str:
    start, end_excl = month_range(year, month)
    last = end_excl - timedelta(days=1)
    return (
        f"{start.day:02d}.{start.month:02d}.{start.year} — "
        f"{last.day:02d}.{last.month:02d}.{last.year}"
    )


def _format_report_html(rep, sport_label: Optional[str]) -> str:
    title_m = _MONTH_NAMES_NOM[rep.month]
    period_h = html.escape(_period_range_human(rep.year, rep.month))
    lines = [
        f"📊 <b>Сводка: {html.escape(title_m)} {rep.year}</b>",
        f"<i>Период: {period_h}</i>",
    ]
    if sport_label:
        lines.append(f"<i>Направление: {html.escape(sport_label)}</i>")
    lines += [
        "",
        f"• В вашей базе (по направлению): <b>{rep.roster_total}</b>",
        f"• Активных абонементов на конец месяца: <b>{rep.active_at_month_end}</b>",
        f"• Новых спортсменов за период: <b>{rep.new_athletes_in_period}</b>",
        "",
        "<b>Посещаемость</b> (тренировки не отменены; ваш вид спорта):",
        f"• Отметок «был»: <b>{rep.attendance_present}</b> "
        f"(уникальных спортсменов: <b>{rep.athletes_present_distinct}</b>)",
        f"• Отметок «не был»: <b>{rep.attendance_absent}</b> "
        f"(уникальных спортсменов: <b>{rep.athletes_absent_distinct}</b>)",
        "",
        "<b>Абонементы</b>:",
        f"• Старт действия в периоде (по дате начала): <b>{rep.subscription_starts_in_period}</b>",
        f"• Новых записей абонемента (по дате создания строки): <b>{rep.new_subscription_rows_in_period}</b>",
        "",
        "<b>Выручка</b> (таблица оплат, дата учёта — <code>paid_at</code>):",
        f"• За период: <b>{html.escape(_format_rubles(rep.revenue_rubles))}</b>",
        f"• Платёжных записей: <b>{rep.payment_records_in_period}</b>",
        "",
        "<i>При активации абонемента сумма может создаваться автоматически, если в .env заданы "
        "<code>SUBSCRIPTION_PRICE_MONTHLY_RUB</code> и/или <code>SUBSCRIPTION_PRICE_SINGLE_RUB</code>. "
        "Дополнительно можно заносить строки в <code>subscription_payments</code> вручную.</i>",
    ]
    return "\n".join(lines)


def _pick_months_for_buttons() -> Tuple[Tuple[int, int], Tuple[int, int]]:
    now = now_moscow()
    y, m = now.year, now.month
    if m == 1:
        py, pm = y - 1, 12
    else:
        py, pm = y, m - 1
    return (y, m), (py, pm)


def _rolling_past_months(now, count: int = 12) -> List[Tuple[int, int, str]]:
    """Список (год, месяц, короткая подпись) от текущего месяца назад."""
    y, m = now.year, now.month
    out: List[Tuple[int, int, str]] = []
    for _ in range(count):
        out.append((y, m, f"{m:02d}/{str(y)[2:]}"))
        if m == 1:
            y, m = y - 1, 12
        else:
            m -= 1
    return out


def coach_report_month_keyboard() -> InlineKeyboardMarkup:
    """Текущий / прошлый + последние 12 месяцев (кнопки MM/YY)."""
    now = now_moscow()
    (cy, cm), (py, pm) = _pick_months_for_buttons()
    rows = [
        [
            InlineKeyboardButton(f"Текущий ({cm:02d}.{cy})", callback_data="cprpt_cur"),
            InlineKeyboardButton(f"Прошлый ({pm:02d}.{py})", callback_data="cprpt_prev"),
        ]
    ]
    chunk: List[InlineKeyboardButton] = []
    for y, m, label in _rolling_past_months(now, 12):
        chunk.append(InlineKeyboardButton(label, callback_data=f"cprpt_{y}_{m}"))
        if len(chunk) == 3:
            rows.append(chunk)
            chunk = []
    if chunk:
        rows.append(chunk)
    return InlineKeyboardMarkup(rows)


def _parse_report_callback(data: str) -> Optional[Tuple[int, int]]:
    if data == "cprpt_cur":
        n = now_moscow()
        return n.year, n.month
    if data == "cprpt_prev":
        n = now_moscow()
        y, m = n.year, n.month
        if m == 1:
            return y - 1, 12
        return y, m - 1
    m = re.match(r"^cprpt_(\d{4})_(\d{1,2})$", (data or "").strip())
    if not m:
        return None
    year, month = int(m.group(1)), int(m.group(2))
    if month < 1 or month > 12 or year < 2000 or year > 2100:
        return None
    return year, month


async def coach_report_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Кнопка меню: выбор месяца для отчёта."""
    user_id = update.effective_user.id
    try:
        with get_db_session() as session:
            user = get_user_by_telegram_id(session, user_id)
            if not user or get_user_role(user) != "coach":
                await update.message.reply_text("❌ Доступно только тренерам")
                return
    except Exception as e:
        logger.error("coach_report_entry rights: %s", e, exc_info=True)
        await update.message.reply_text("❌ Ошибка при проверке доступа")
        return

    await update.message.reply_text(
        "📊 Выберите месяц для сводки по <b>вашим</b> спортсменам, посещениям и оплатам:",
        reply_markup=coach_report_month_keyboard(),
        parse_mode="HTML",
    )


async def coach_report_period_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    data = (query.data or "").strip()

    parsed = _parse_report_callback(data)
    if parsed is None:
        await query.edit_message_text("❌ Неизвестный период")
        return
    year, month = parsed

    try:
        with get_db_session() as session:
            user = get_user_by_telegram_id(session, user_id)
            if not user or get_user_role(user) != "coach":
                await query.edit_message_text("❌ Доступно только тренерам")
                return
            coach = (
                session.query(Coach)
                .options(joinedload(Coach.sport_type_rel))
                .filter_by(id=user.id)
                .one_or_none()
            )
            if not coach:
                await query.edit_message_text("❌ Профиль тренера не найден")
                return
            rep = build_coach_period_report(session, coach, year, month)
            sport_label = coach_sport_type_name(coach)
    except Exception as e:
        logger.error("coach_report_period_callback: %s", e, exc_info=True)
        await query.edit_message_text("❌ Ошибка при формировании отчёта")
        return

    text = _format_report_html(rep, sport_label)
    if len(text) > 4000:
        text = text[:3900] + "\n\n<i>… обрезано (лимит Telegram).</i>"

    await query.edit_message_text(
        text, parse_mode="HTML", reply_markup=coach_report_month_keyboard()
    )
