"""Отчёт тренера за календарный месяц: посещаемость, база, абонементы."""
from __future__ import annotations

import html
import logging

from sqlalchemy.orm import joinedload
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from core.database import get_db_session
from database.db_utils import get_user_by_telegram_id, get_user_role
from database.db_utils.coach_report import build_coach_period_report
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


def _format_report_html(rep) -> str:
    title_m = _MONTH_NAMES_NOM[rep.month]
    lines = [
        f"📊 <b>Сводка: {html.escape(title_m)} {rep.year}</b>",
        "",
        f"• В вашей базе (по направлению): <b>{rep.roster_total}</b>",
        f"• Активных абонементов на конец месяца: <b>{rep.active_at_month_end}</b>",
        f"• Новых спортсменов за период: <b>{rep.new_athletes_in_period}</b>",
        "",
        "<b>Посещаемость</b> (отметки по тренировкам в вашем виде спорта):",
        f"• Был: <b>{rep.attendance_present}</b>",
        f"• Не был: <b>{rep.attendance_absent}</b>",
        "",
        "<b>Абонементы</b> (в БД нет сумм оплат):",
        f"• Старт действия в периоде (по дате начала): <b>{rep.subscription_starts_in_period}</b>",
        f"• Новых записей абонемента (по дате создания строки): <b>{rep.new_subscription_rows_in_period}</b>",
        "",
        "<i>Денежная выручка в модели не хранится — при необходимости ведите её отдельно или расширьте схему.</i>",
    ]
    return "\n".join(lines)


def _pick_months_for_buttons():
    now = now_moscow()
    y, m = now.year, now.month
    if m == 1:
        py, pm = y - 1, 12
    else:
        py, pm = y, m - 1
    return (y, m), (py, pm)


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

    (cy, cm), (py, pm) = _pick_months_for_buttons()
    cur_label = f"Текущий ({cm:02d}.{cy})"
    prev_label = f"Прошлый ({pm:02d}.{py})"
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(cur_label, callback_data="cprpt_cur"),
                InlineKeyboardButton(prev_label, callback_data="cprpt_prev"),
            ]
        ]
    )
    await update.message.reply_text(
        "📊 Выберите месяц для сводки по <b>вашим</b> спортсменам и отметкам посещений:",
        reply_markup=keyboard,
        parse_mode="HTML",
    )


async def coach_report_period_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    data = (query.data or "").strip()

    if data == "cprpt_cur":
        now = now_moscow()
        year, month = now.year, now.month
    elif data == "cprpt_prev":
        now = now_moscow()
        y, m = now.year, now.month
        if m == 1:
            year, month = y - 1, 12
        else:
            year, month = y, m - 1
    else:
        await query.edit_message_text("❌ Неизвестный период")
        return

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
    except Exception as e:
        logger.error("coach_report_period_callback: %s", e, exc_info=True)
        await query.edit_message_text("❌ Ошибка при формировании отчёта")
        return

    text = _format_report_html(rep)
    if len(text) > 4000:
        text = text[:3900] + "\n\n<i>… обрезано (лимит Telegram).</i>"

    (cy, cm), (py, pm) = _pick_months_for_buttons()
    cur_label = f"Текущий ({cm:02d}.{cy})"
    prev_label = f"Прошлый ({pm:02d}.{py})"
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(cur_label, callback_data="cprpt_cur"),
                InlineKeyboardButton(prev_label, callback_data="cprpt_prev"),
            ]
        ]
    )

    await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)

