"""Статистика тренера: выбор года и месяца, затем раздел (KPI, выручка, …)."""
from __future__ import annotations

import html
import logging
import re
from datetime import timedelta
from typing import List, Optional

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
from database.db_utils.subscription_tariffs import tariff_preview_monthly_single_for_sport
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

_MONTH_SHORT = (
    "",
    "Янв",
    "Фев",
    "Мар",
    "Апр",
    "Май",
    "Июн",
    "Июл",
    "Авг",
    "Сен",
    "Окт",
    "Ноя",
    "Дек",
)

_NUM_SEP = "\u202f"  # узкий пробел для тысяч

_MIN_YEAR = 2018


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


def _report_header_lines(rep, sport_label: Optional[str]) -> List[str]:
    title_m = _MONTH_NAMES_NOM[rep.month]
    period_h = html.escape(_period_range_human(rep.year, rep.month))
    lines = [
        f"📊 <b>Статистика: {html.escape(title_m)} {rep.year}</b>",
        f"<i>Период: {period_h}</i>",
    ]
    if sport_label:
        lines.append(f"<i>Направление: {html.escape(sport_label)}</i>")
    return lines


def _format_section_html(
    rep,
    sport_label: Optional[str],
    section: str,
    *,
    coach_tariff_monthly_rub: Optional[int] = None,
    coach_tariff_single_rub: Optional[int] = None,
) -> str:
    head = _report_header_lines(rep, sport_label)
    body: List[str] = []
    if section == "kpi":
        body = [
            "",
            "<b>KPI</b>",
            f"• В вашей базе (по направлению): <b>{rep.roster_total}</b>",
            f"• Активных абонементов на конец месяца: <b>{rep.active_at_month_end}</b>",
            f"• Новых спортсменов за период: <b>{rep.new_athletes_in_period}</b>",
        ]
    elif section == "rev":
        monthly_h = (
            "нет активной записи"
            if coach_tariff_monthly_rub is None
            else html.escape(_format_rubles(coach_tariff_monthly_rub))
        )
        single_h = (
            "нет активной записи"
            if coach_tariff_single_rub is None
            else html.escape(_format_rubles(coach_tariff_single_rub))
        )
        body = [
            "",
            "<b>Выручка</b> (таблица оплат, дата учёта — <code>paid_at</code>):",
            "<i>Здесь не «деньги за активных в месяце», а только строки "
            "<code>subscription_payments</code>, у которых <code>paid_at</code> попал в выбранный месяц. "
            "Оплата при активации обычно одна и относится к месяцу старта; в следующих месяцах абонемент "
            "может быть активен, а сумма за период — 0 ₽. Сколько абонементов активно на конец месяца — "
            f"в разделе KPI (<b>{rep.active_at_month_end}</b>).</i>",
            "",
            f"• За период: <b>{html.escape(_format_rubles(rep.revenue_rubles))}</b>",
            f"• Платёжных записей: <b>{rep.payment_records_in_period}</b>",
            "",
            f"• Тарифы для вашего направления в <code>subscription_tariffs</code>: "
            f"месячный — <b>{monthly_h}</b>; разовый — <b>{single_h}</b>",
            "",
            "<i>При активации строка оплаты создаётся только если для вида спорта абонемента есть "
            "активная сумма в <code>subscription_tariffs</code>. Строки в "
            "<code>subscription_payments</code> можно добавлять вручную.</i>",
        ]
        if rep.revenue_rubles == 0 and rep.payment_records_in_period == 0:
            if rep.subscription_starts_in_period == 0:
                tail = (
                    " Откройте месяц первой тренировки после активации (там обычно "
                    "<code>paid_at</code>), или занесите оплату вручную."
                )
                if rep.active_at_month_end > 0:
                    tail += (
                        " Активные абонементы в KPI при этом возможны — это не ошибка."
                    )
                body.extend(
                    [
                        "",
                        "<i>В выбранном месяце нет стартов по дате начала и нет оплат с "
                        "<code>paid_at</code> в этом месяце — нули в сумме ожидаемы." + tail + "</i>",
                    ]
                )
            elif rep.estimated_revenue_if_current_env_rub > 0:
                est_h = html.escape(
                    _format_rubles(rep.estimated_revenue_if_current_env_rub)
                )
                body.extend(
                    [
                        "",
                        "<i>Справочно: по текущим строкам <code>subscription_tariffs</code> старты в месяце дают "
                        f"примерно <b>{est_h}</b>, а в таблице оплат "
                        "записей нет — автозапись не сработала в момент активации (старый код, не было тарифа "
                        "или суммы) или оплату нужно внести вручную.</i>",
                    ]
                )
            else:
                body.extend(
                    [
                        "",
                        "<i>В месяце есть старты, но для их видов спорта и типов абонемента нет подходящих "
                        "активных сумм в <code>subscription_tariffs</code> — автострока оплаты не создавалась.</i>",
                    ]
                )
    elif section == "att":
        body = [
            "",
            "<b>Посещаемость</b> (тренировки не отменены; ваш вид спорта):",
            f"• Отметок «был»: <b>{rep.attendance_present}</b> "
            f"(уникальных спортсменов: <b>{rep.athletes_present_distinct}</b>)",
            f"• Отметок «не был»: <b>{rep.attendance_absent}</b> "
            f"(уникальных спортсменов: <b>{rep.athletes_absent_distinct}</b>)",
        ]
    elif section == "sub":
        body = [
            "",
            "<b>Абонементы</b>:",
            f"• Старт действия в периоде (по дате начала): <b>{rep.subscription_starts_in_period}</b>",
            f"• Новых записей абонемента (по дате создания строки): <b>{rep.new_subscription_rows_in_period}</b>",
        ]
    else:
        body = ["", "❌ Неизвестный раздел"]
    return "\n".join(head + body)


def _clamp_year(y: int) -> int:
    now = now_moscow()
    return max(_MIN_YEAR, min(y, now.year))


def statistics_year_month_keyboard(year: int) -> InlineKeyboardMarkup:
    """Сетка месяцев + навигация по году (как в «Мой календарь»)."""
    year = _clamp_year(year)
    rows: List[List[InlineKeyboardButton]] = []
    for r in range(4):
        chunk: List[InlineKeyboardButton] = []
        for c in range(3):
            m = r * 3 + c + 1
            chunk.append(
                InlineKeyboardButton(
                    _MONTH_SHORT[m],
                    callback_data=f"cstm_{year}_{m:02d}",
                )
            )
        rows.append(chunk)
    now = now_moscow()
    nav: List[InlineKeyboardButton] = []
    if year > _MIN_YEAR:
        nav.append(
            InlineKeyboardButton("◀️ Предыдущий", callback_data=f"cstyp_{year}")
        )
    if year < now.year:
        nav.append(
            InlineKeyboardButton("Следующий ▶️", callback_data=f"cstyn_{year}")
        )
    if nav:
        rows.append(nav)
    rows.append(
        [InlineKeyboardButton("🏠 В меню", callback_data="back_to_menu_main")]
    )
    return InlineKeyboardMarkup(rows)


def statistics_year_month_message_html(year: int) -> str:
    year = _clamp_year(year)
    return (
        f"📊 <b>Статистика</b>\n\n"
        f"Год: <b>{html.escape(str(year))}</b>\n"
        f"Выберите <b>месяц</b> (навигация по годам — кнопки внизу, как в «Мой календарь»):"
    )


def statistics_section_keyboard(year: int, month: int) -> InlineKeyboardMarkup:
    y, m = year, month

    def sec(code: str, label: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(label, callback_data=f"csts_{y}_{m:02d}_{code}")

    return InlineKeyboardMarkup(
        [
            [sec("kpi", "KPI"), sec("rev", "Выручка")],
            [sec("att", "Посещаемость"), sec("sub", "Абонементы")],
            [
                InlineKeyboardButton(
                    "◀️ К выбору месяца",
                    callback_data=f"csty_{y}",
                )
            ],
            [InlineKeyboardButton("🏠 В меню", callback_data="back_to_menu_main")],
        ]
    )


def statistics_section_message_html(year: int, month: int) -> str:
    title_m = _MONTH_NAMES_NOM[month]
    return (
        f"📊 <b>Статистика: {html.escape(title_m)} {year}</b>\n\n"
        f"Выберите <b>раздел</b>:"
    )


async def coach_report_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Кнопка меню «Статистика»: выбор года и месяца."""
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

    y = _clamp_year(now_moscow().year)
    await update.message.reply_text(
        statistics_year_month_message_html(y),
        reply_markup=statistics_year_month_keyboard(y),
        parse_mode="HTML",
    )


async def coach_statistics_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Все callback вида csty_ / cstyp_ / cstyn_ / cstm_ / csts_."""
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    data = (query.data or "").strip()

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

            # --- выбор года / месяца ---
            m_y = re.match(r"^csty_(\d{4})$", data)
            if m_y:
                y = _clamp_year(int(m_y.group(1)))
                await query.edit_message_text(
                    statistics_year_month_message_html(y),
                    reply_markup=statistics_year_month_keyboard(y),
                    parse_mode="HTML",
                )
                return

            m_yp = re.match(r"^cstyp_(\d{4})$", data)
            if m_yp:
                y = _clamp_year(int(m_yp.group(1)) - 1)
                await query.edit_message_text(
                    statistics_year_month_message_html(y),
                    reply_markup=statistics_year_month_keyboard(y),
                    parse_mode="HTML",
                )
                return

            m_yn = re.match(r"^cstyn_(\d{4})$", data)
            if m_yn:
                y = _clamp_year(int(m_yn.group(1)) + 1)
                await query.edit_message_text(
                    statistics_year_month_message_html(y),
                    reply_markup=statistics_year_month_keyboard(y),
                    parse_mode="HTML",
                )
                return

            m_m = re.match(r"^cstm_(\d{4})_(\d{2})$", data)
            if m_m:
                year, month = int(m_m.group(1)), int(m_m.group(2))
                if month < 1 or month > 12:
                    await query.edit_message_text("❌ Неверный месяц")
                    return
                year = _clamp_year(year)
                if year == now_moscow().year and month > now_moscow().month:
                    await query.answer("Нельзя выбрать будущий месяц", show_alert=True)
                    return
                await query.edit_message_text(
                    statistics_section_message_html(year, month),
                    reply_markup=statistics_section_keyboard(year, month),
                    parse_mode="HTML",
                )
                return

            m_s = re.match(r"^csts_(\d{4})_(\d{2})_(kpi|rev|att|sub)$", data)
            if m_s:
                year, month = int(m_s.group(1)), int(m_s.group(2))
                sec = m_s.group(3)
                if month < 1 or month > 12:
                    await query.edit_message_text("❌ Неверный месяц")
                    return
                year = _clamp_year(year)
                if year == now_moscow().year and month > now_moscow().month:
                    await query.answer("Нельзя выбрать будущий месяц", show_alert=True)
                    return
                rep = build_coach_period_report(session, coach, year, month)
                sport_label = coach_sport_type_name(coach)
                monthly_prev: Optional[int] = None
                single_prev: Optional[int] = None
                if sec == "rev":
                    monthly_prev, single_prev = tariff_preview_monthly_single_for_sport(
                        session, sport_label
                    )
                text = _format_section_html(
                    rep,
                    sport_label,
                    sec,
                    coach_tariff_monthly_rub=monthly_prev,
                    coach_tariff_single_rub=single_prev,
                )
                if len(text) > 4000:
                    text = text[:3900] + "\n\n<i>… обрезано (лимит Telegram).</i>"
                await query.edit_message_text(
                    text,
                    parse_mode="HTML",
                    reply_markup=statistics_section_keyboard(year, month),
                )
                return

    except Exception as e:
        logger.error("coach_statistics_callback: %s", e, exc_info=True)
        await query.edit_message_text("❌ Ошибка при формировании статистики")
        return

    await query.edit_message_text("❌ Неизвестная команда")
