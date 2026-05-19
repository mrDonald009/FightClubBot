"""Модуль для инициализации и стартовых задач приложения."""
import logging
from datetime import date, datetime, time, timedelta
from typing import List, Set, Tuple

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application
from sqlalchemy import text

from core.config import Config
from core.database import get_db_session
from database.models import Coach
from services.attendance_training_flow import (
    build_today_attendance_slots,
    format_today_trainings_count_ru,
)
from services.user_service import UserService
from services.subscription_service import SubscriptionService
from services.subscription_audit_service import run_subscription_audit, format_audit_report
from utils.age_groups import format_age_group_label
from utils.time_utils import APP_TZ, now_moscow

logger = logging.getLogger(__name__)

# Вид спорта в БД для списка THAI_COACH_TELEGRAM_IDS
THAI_COACH_SPORT_TYPE = "Тайский Бокс"

# Ежедневный дайджест: всегда не позже 09:00; раньше — только если индивидуальная − 1 ч < 09:00.
DEFAULT_COACH_DAILY_SUMMARY_TIME = time(9, 0, 0)


def _ensure_daily_summary_delivery_table(session) -> None:
    """Таблица отправок дайджеста (persist между рестартами)."""
    session.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS coach_daily_summary_delivery (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                summary_date TEXT NOT NULL,
                coach_telegram_id INTEGER NOT NULL,
                sent_at TEXT NOT NULL,
                UNIQUE(summary_date, coach_telegram_id)
            )
            """
        )
    )


def _daily_summary_already_sent(session, *, summary_date: date, coach_telegram_id: int) -> bool:
    row = session.execute(
        text(
            """
            SELECT 1
            FROM coach_daily_summary_delivery
            WHERE summary_date = :summary_date
              AND coach_telegram_id = :coach_telegram_id
            LIMIT 1
            """
        ),
        {
            "summary_date": summary_date.isoformat(),
            "coach_telegram_id": int(coach_telegram_id),
        },
    ).first()
    return row is not None


def _mark_daily_summary_sent(session, *, summary_date: date, coach_telegram_id: int, sent_at: datetime) -> None:
    session.execute(
        text(
            """
            INSERT INTO coach_daily_summary_delivery (summary_date, coach_telegram_id, sent_at)
            VALUES (:summary_date, :coach_telegram_id, :sent_at)
            ON CONFLICT(summary_date, coach_telegram_id) DO NOTHING
            """
        ),
        {
            "summary_date": summary_date.isoformat(),
            "coach_telegram_id": int(coach_telegram_id),
            "sent_at": sent_at.isoformat(timespec="seconds"),
        },
    )


def _slot_summary_line(slot) -> str:
    """Короткая строка тренировки для уведомлений."""
    t_str = slot.training_datetime.strftime("%H:%M")
    if getattr(slot, "is_individual_format", False):
        return f"🕒 {t_str} | {slot.sport_type} — Индивидуальная"
    age_group = format_age_group_label(slot.age_group, short=True)
    return f"🕒 {t_str} | {slot.sport_type} ({age_group}) — Групповая"


def format_coach_daily_summary_message(today: date, slots: List) -> str:
    """Текст ежедневного дайджеста тренеру (расписание на сегодня)."""
    date_label = today.strftime("%d.%m.%Y")
    if not slots:
        return (
            "Доброе утро!\n\n"
            f"Сегодня - {date_label}\n"
            "У Вас нет запланированных тренировок."
        )
    count_label = format_today_trainings_count_ru(len(slots))
    lines = [
        "Доброе утро!",
        "",
        f"Сегодня - {date_label}",
        f"У Вас запланировано {count_label}:",
        "",
    ]
    ordered = sorted(slots, key=lambda s: s.training_datetime)
    for index, slot in enumerate(ordered, start=1):
        t_str = slot.training_datetime.strftime("%H:%M")
        if getattr(slot, "is_individual_format", False):
            detail = f"{slot.sport_type}, индивидуальная"
        else:
            age = format_age_group_label(slot.age_group, short=True)
            detail = f"{slot.sport_type} ({age}), групповая"
        lines.append(f"{index}. {t_str} — {detail}")
    return "\n".join(lines)


def format_coach_training_start_reminder_message(slot) -> str:
    """Текст напоминания тренеру в момент начала пары."""
    return (
        "Тренировка началась!\n\n"
        f"{_slot_summary_line(slot)}\n\n"
        "Пожалуйста, откройте раздел «📝 Отметить посещения» "
        "и выберите присутствующих спортсменов."
    )


def _coach_and_slots_for_today(now_dt: datetime) -> List[Tuple[int, List]]:
    """Вернуть пары (coach_telegram_id, slots_today)."""
    out: List[Tuple[int, List]] = []
    with get_db_session() as session:
        coaches = session.query(Coach).all()
        for coach in coaches:
            if not getattr(coach, "telegram_id", None):
                continue
            slots, _virtual = build_today_attendance_slots(session, coach, now_dt)
            out.append((coach.telegram_id, slots))
    return out


def coach_daily_summary_send_datetime(today: date, slots: List) -> datetime:
    """
    Когда отправить ежедневный дайджест тренеру на today.

    Базово 09:00. При индивидуальных — не позже 09:00; раньше 09:00 только если
    самая ранняя индивидуальная минус 1 ч раньше девяти (10:30 → всё равно 09:00).
    """
    default_send = datetime.combine(today, DEFAULT_COACH_DAILY_SUMMARY_TIME)
    individual_starts = [
        slot.training_datetime
        for slot in slots
        if getattr(slot, "is_individual_format", False)
    ]
    if not individual_starts:
        return default_send
    one_hour_before_earliest = min(individual_starts) - timedelta(hours=1)
    return min(default_send, one_hour_before_earliest)


async def _daily_coach_schedule_summary_job(context) -> None:
    """Ежедневный дайджест тренеру: сколько тренировок на сегодня."""
    now_dt = now_moscow()
    today = now_dt.date()
    sent_keys: Set[str] = context.application.bot_data.setdefault(
        "coach_daily_summary_sent_keys", set()
    )

    try:
        coach_rows = _coach_and_slots_for_today(now_dt)
        with get_db_session() as session:
            _ensure_daily_summary_delivery_table(session)
            for coach_telegram_id, slots in coach_rows:
                key = f"{today.isoformat()}:{coach_telegram_id}"
                if key in sent_keys:
                    continue
                if _daily_summary_already_sent(
                    session, summary_date=today, coach_telegram_id=coach_telegram_id
                ):
                    sent_keys.add(key)
                    continue
                send_at = coach_daily_summary_send_datetime(today, slots)
                if now_dt < send_at:
                    continue
                msg = format_coach_daily_summary_message(today, slots)
                await context.bot.send_message(
                    chat_id=coach_telegram_id,
                    text=msg,
                )
                _mark_daily_summary_sent(
                    session,
                    summary_date=today,
                    coach_telegram_id=coach_telegram_id,
                    sent_at=now_dt,
                )
                sent_keys.add(key)
        # Чистим кэш от старых дат.
        context.application.bot_data["coach_daily_summary_sent_keys"] = {
            k for k in sent_keys if k.startswith(today.isoformat())
        }
    except Exception as e:  # pragma: no cover
        logger.error("❌ Ошибка daily coach summary: %s", e, exc_info=True)


async def _coach_training_start_reminder_job(context) -> None:
    """Напоминание тренеру в момент старта занятия."""
    now_dt = now_moscow()
    # Окно 1 мин назад и 1 мин вперед, чтобы не пропускать событие из-за дрейфа таймера.
    left = now_dt - timedelta(minutes=1)
    right = now_dt + timedelta(minutes=1)

    sent_keys: Set[str] = context.application.bot_data.setdefault(
        "coach_start_reminder_sent_keys", set()
    )
    today_prefix = now_dt.date().isoformat()

    try:
        coach_rows = _coach_and_slots_for_today(now_dt)
        for coach_telegram_id, slots in coach_rows:
            for slot in slots:
                start_dt = slot.training_datetime
                if not (left <= start_dt <= right):
                    continue
                slot_key = (
                    f"{today_prefix}:{coach_telegram_id}:{slot.sport_type}:{slot.age_group}:"
                    f"{start_dt.strftime('%H:%M')}:{int(getattr(slot, 'is_individual_format', False))}"
                )
                if slot_key in sent_keys:
                    continue

                text = format_coach_training_start_reminder_message(slot)
                keyboard = InlineKeyboardMarkup(
                    [[InlineKeyboardButton("📝 Открыть «Отметить посещения»", callback_data="attendance_training_list")]]
                )
                await context.bot.send_message(
                    chat_id=coach_telegram_id,
                    text=text,
                    reply_markup=keyboard,
                )
                sent_keys.add(slot_key)

        # Чистим ключи только текущей датой, чтобы не разрасталось.
        context.application.bot_data["coach_start_reminder_sent_keys"] = {
            k for k in sent_keys if k.startswith(today_prefix)
        }
    except Exception as e:  # pragma: no cover
        logger.error("❌ Ошибка coach training start reminder: %s", e, exc_info=True)


def ensure_admin_user(config: Config) -> None:
    """Создать/проверить запись администратора по ADMIN_TELEGRAM_ID."""
    admin_id = getattr(config, "ADMIN_TELEGRAM_ID", None)
    if admin_id is None:
        return
    try:
        with get_db_session() as session:
            admin = UserService.ensure_admin(
                session=session,
                telegram_id=admin_id,
                username="admin",
                first_name="Администратор",
            )
            if admin:
                logger.info(f"✅ Администратор проверен/создан: {admin_id}")
    except Exception as e:
        logger.error(f"❌ Ошибка при проверке администратора: {e}", exc_info=True)


def ensure_coaches_from_merged_env(config: Config) -> None:
    """
    Автосоздание/обновление тренеров из env.

    - merged_thai_coach_telegram_ids (THAI_COACH_TELEGRAM_IDS + THAI_COACH_TELEGRAM_ID) —
      вид спорта всегда «Тайский Бокс».
    - MMA_COACH_TELEGRAM_IDS — всегда MMA; обрабатывается после тайского списка,
      чтобы перезаписать вид спорта, если id ошибочно указан в обоих местах.
    """
    ids = getattr(config, "merged_thai_coach_telegram_ids", None) or []
    mma_ids = getattr(config, "MMA_COACH_TELEGRAM_IDS", None) or []
    if not ids and not mma_ids:
        logger.info(
            "Тренеры: тайский список и MMA_COACH_TELEGRAM_IDS пусты — пропуск"
        )
        return

    admin_id = getattr(config, "ADMIN_TELEGRAM_ID", None)
    first_name_thai = "Тренер Тайский Бокс"

    def _ensure_one(tid: int, sport: str, first_name: str) -> None:
        if admin_id is not None and tid == admin_id:
            logger.error(
                "telegram_id=%s совпадает с ADMIN_TELEGRAM_ID — пропуск автосоздания тренера",
                tid,
            )
            return
        try:
            with get_db_session() as session:
                UserService.ensure_test_coach(
                    session=session,
                    telegram_id=tid,
                    username=f"coach_{tid}",
                    first_name=first_name,
                    sport_type=sport,
                )
            logger.info("✅ Тренер проверен/создан: %s (%s)", tid, sport)
        except ValueError as e:
            logger.warning("Тренер telegram_id=%s не создан: %s", tid, e)
        except Exception as e:
            logger.error(
                "❌ Ошибка при создании тренера telegram_id=%s: %s",
                tid,
                e,
                exc_info=True,
            )

    processed: Set[int] = set()
    for tid in ids:
        if tid in processed:
            continue
        processed.add(tid)
        _ensure_one(tid, THAI_COACH_SPORT_TYPE, first_name_thai)

    for tid in mma_ids:
        if tid in processed:
            logger.info(
                "telegram_id=%s также в списке тайских тренеров — выставляем MMA",
                tid,
            )
        _ensure_one(tid, "MMA", "Тренер ММА")


def check_subscriptions_on_startup() -> int:
    """
    Проверить абонементы при запуске бота.
    
    Returns:
        Количество обновленных абонементов
    """
    try:
        updated_count = SubscriptionService.check_and_update_subscriptions()
        if updated_count > 0:
            logger.info(f"🔄 При запуске обновлено {updated_count} абонементов")
        return updated_count
    except ImportError as e:
        logger.warning(f"⚠️ Не удалось загрузить SubscriptionChecker: {e}")
        logger.info("⚠️ Проверка абонементов будет выполнена при открытии карточек")
        return 0
    except Exception as e:
        logger.error(f"❌ Ошибка при проверке абонементов при запуске: {e}", exc_info=True)
        return 0


def initialize_app(config: Config) -> None:
    """
    Выполнить все инициализационные задачи при запуске.
    
    Args:
        config: Конфигурация приложения
    """
    logger.info("🔧 Выполнение инициализационных задач...")
    
    ensure_admin_user(config)
    ensure_coaches_from_merged_env(config)

    # Проверяем абонементы
    check_subscriptions_on_startup()

    logger.info("✅ Инициализация завершена")


async def _daily_subscription_audit_job(context) -> None:
    """Ежесуточный read-only аудит абонементов с отправкой отчета админу."""
    config = context.application.bot_data.get("config")
    admin_id = getattr(config, "ADMIN_TELEGRAM_ID", None) if config else None

    try:
        with get_db_session() as session:
            report = run_subscription_audit(session)
        report_text = format_audit_report(report)

        logger.info(
            "🩺 Daily audit: checked=%s issues=%s",
            report.get("total_subscriptions", 0),
            report.get("issues_total", 0),
        )

        if admin_id:
            await context.bot.send_message(chat_id=admin_id, text=report_text, parse_mode="HTML")
    except Exception as e:  # pragma: no cover
        logger.error(f"❌ Ошибка daily-аудита абонементов: {e}", exc_info=True)


def setup_scheduled_jobs(application: Application, config: Config) -> None:
    """Настроить плановые задачи приложения."""
    application.bot_data["config"] = config
    if not application.job_queue:
        logger.warning("⚠️ JobQueue недоступен: ежедневный аудит не запланирован")
        return

    run_time = time(hour=8, minute=0, tzinfo=APP_TZ)
    application.job_queue.run_daily(
        _daily_subscription_audit_job,
        time=run_time,
        name="daily_subscription_audit",
    )
    logger.info("🗓️ Запланирован ежедневный аудит абонементов (08:00 APP_TIMEZONE)")

    application.job_queue.run_repeating(
        _daily_coach_schedule_summary_job,
        interval=timedelta(minutes=1),
        first=timedelta(seconds=40),
        name="daily_coach_schedule_summary",
    )
    logger.info(
        "🗓️ Запланирован ежедневный дайджест тренерам "
        "(09:00; раньше только если индивидуальная − 1 ч < 09:00, APP_TIMEZONE)"
    )

    application.job_queue.run_repeating(
        _coach_training_start_reminder_job,
        interval=timedelta(minutes=1),
        first=timedelta(seconds=50),
        name="coach_training_start_reminder",
    )
    logger.info("🗓️ Запланированы напоминания тренерам о старте занятий (каждую минуту)")

