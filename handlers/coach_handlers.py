import logging
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.error import BadRequest
from telegram.ext import ContextTypes, ConversationHandler
from database.models import Coach, Athlete, Subscription, Training, Attendance, GlobalFreeze
from core.database import get_db_session
from database.db_utils import (
    get_user_by_telegram_id,
    get_user_role,
    create_athlete,
)
import database.db_utils as db_utils_pkg
from database.db_utils.training_slots import (
    INDIVIDUAL_TRAINING_AGE_GROUP_STORED,
    TRAINING_FORMAT_INDIVIDUAL,
    dedupe_individual_trainings_by_slot,
    find_group_training_on_calendar_day,
    individual_slot_has_links,
    individual_slot_conflicts,
    individual_slot_training_ids,
    iter_allowed_individual_starts,
)
from database.db_utils.subscription_activation_payment import (
    record_payment_on_subscription_activation,
)
from typing import List, Optional, Tuple, Union
from utils.age_groups import AGE_GROUP_CODES, format_age_group_label, normalize_age_group
from utils.coach_sport import coach_sport_type_name
from utils.discipline_keys import discipline_key_for
from utils.training_manager import TrainingManager
from utils.subscription_resolve import subscription_for_coach_sport
from utils.attendance_display import attendance_icon_for_slot
from utils.training_slot_display import (
    format_athlete_age_suffix,
    format_coach_calendar_slot_bullet,
    format_training_slot_body,
)
from utils.time_utils import (
    now_moscow,
    ACTIVATION_GRACE_AFTER_START,
    individual_training_end_time,
    training_end_time,
)
from keyboards.coach_kb import get_coach_main_menu
from datetime import date, datetime, timedelta
from sqlalchemy import and_, exists, func, or_
from sqlalchemy.orm import joinedload
import re
import calendar
import html


logger = logging.getLogger(__name__)

CAL_BOOK_ADD_ICON = "➕"

# Лимит длины текста сообщения Telegram (с запасом под суффикс обрезки)
TELEGRAM_MESSAGE_SAFE_LEN = 3900

_MONTH_NAMES_RU = (
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


def _coach_scheduled_weekdays(sport_type_name: Optional[str]) -> set:
    """Дни недели (0=Пн … 6=Вс) с групповыми занятиями по расписанию вида спорта."""
    scheduled = set()
    sport = (sport_type_name or "").strip()
    if not sport:
        return scheduled
    schedule_dict = TrainingManager.TRAINING_SCHEDULE.get(sport, {})
    for age_group in AGE_GROUP_CODES:
        schedule = schedule_dict.get(age_group)
        if schedule and schedule.get("days"):
            scheduled.update(schedule["days"])
    return scheduled


def truncate_for_telegram_message(text: str, limit: int = TELEGRAM_MESSAGE_SAFE_LEN) -> str:
    """Укорачивает текст, стараясь не рвать посередине строки."""
    if len(text) <= limit:
        return text
    suffix = "\n\n<i>… сообщение обрезано (лимит Telegram).</i>"
    cut = max(0, limit - len(suffix))
    chunk = text[:cut]
    last_nl = chunk.rfind("\n")
    if last_nl > cut // 2:
        chunk = chunk[:last_nl]
    return chunk + suffix


def _coach_calendar_message_header(*, current_year: int, current_month: int) -> str:
    title = _MONTH_NAMES_RU[current_month]
    return (
        "📅 <b>Мой календарь</b>\n\n"
        f"{html.escape(title)} {current_year}"
    )


def _surname_initials(full_name: str) -> str:
    """Формат Фамилия И.О. для компактного списка."""
    parts = [p for p in (full_name or "").split() if p]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    initials = "".join(f"{p[0]}." for p in parts[1:] if p)
    return f"{parts[0]} {initials}".strip()


# Пагинация списка спортсменов (лимит Telegram на callback_data — 64 байта, префикс alpg_)
ATHLETE_LIST_PAGE_SIZE = 20
_ATHLETE_LIST_FILTER_CODES = {
    "active_children": "ac",
    "active_middle": "am",
    "active_adults": "aa",
    "inactive_children": "ic",
    "inactive_middle": "im",
    "inactive_adults": "ia",
    "all": "al",
    "children": "ch",
    "middle": "md",
    "adults": "ad",
    "inactive": "in",
}
_ATHLETE_LIST_CODE_TO_FILTER = {v: k for k, v in _ATHLETE_LIST_FILTER_CODES.items()}


def encode_athlete_list_page(filter_key: str, page: int) -> str:
    code = _ATHLETE_LIST_FILTER_CODES.get(filter_key, "al")
    return f"alpg_{code}_{page}"


def decode_athlete_list_page(callback_data: str) -> Optional[Tuple[str, int]]:
    m = re.match(r"^alpg_([a-z]{2})_(\d+)$", callback_data or "")
    if not m:
        return None
    code, page_s = m.group(1), m.group(2)
    fk = _ATHLETE_LIST_CODE_TO_FILTER.get(code)
    if fk is None:
        return None
    return fk, int(page_s)


def load_athletes_for_list(session, user) -> Tuple[List[Athlete], str]:
    """
    Спортсмены для экранов «Список спортсменов» с eager-loading `athlete.subscriptions`
    (избегает N+1 при `subscription_for_coach_sport` / фильтрах). Только тренер — свой список.
    """
    if isinstance(user, Coach):
        q = (
            session.query(Athlete)
            .options(joinedload(Athlete.subscriptions))
            .filter_by(created_by=user.id)
        )
        sport_type_name = None
        if user.sport_type_rel:
            sport_type_name = user.sport_type_rel.name
        elif user.sport_type:
            sport_type_name = user.sport_type
        if sport_type_name:
            # Профиль спортсмена ИЛИ любой абонемент с этим видом спорта (мульти-направления).
            has_sub_for_sport = exists().where(
                Subscription.athlete_id == Athlete.id,
                or_(
                    Subscription.sport_type == sport_type_name,
                    and_(
                        or_(
                            Subscription.sport_type.is_(None),
                            Subscription.sport_type == "",
                        ),
                        Athlete.sport_type == sport_type_name,
                    ),
                ),
            )
            q = q.filter(
                or_(Athlete.sport_type == sport_type_name, has_sub_for_sport)
            )
        athletes = q.all()
        header = "🏃‍♂️ <b>Список спортсменов</b>\n\n"
    else:
        athletes = []
        header = "🏃‍♂️ <b>Список спортсменов</b>\n\n"
    return athletes, header


# Состояния для добавления спортсмена
(
    ATHLETE_FULL_NAME,
    ATHLETE_PHONE,
    ATHLETE_BIRTH_DATE,
    ATHLETE_MEDICAL,
    ATHLETE_SPORT_TYPE,
    ATHLETE_AGE_GROUP,
    ATHLETE_SUBSCRIPTION,   # выбор типа абонемента: Месячный / Разовый
    ATHLETE_TRAINING_DATE   # выбор первой даты тренировки (inline-календарь)
) = range(8)

# Список кнопок меню для проверки прерывания
MENU_BUTTONS = [
    "👥 Добавить спортсмена",
    "📋 Список спортсменов",
    "📝 Отметить посещения",
    "📅 Мой календарь",
    "🌍 Массовая заморозка",
    "📊 Статистика",
]


def is_phone_number(text):
    """Проверяет, является ли текст номером телефона"""
    # Удаляем все пробелы и лишние символы для проверки
    cleaned = re.sub(r'[^\d]', '', text)

    # Проверяем паттерны номеров телефонов
    phone_patterns = [
        r'^\d{3}-\d{3}-\d{2}-\d{2}$',  # 925-123-45-67
        r'^\d{10,11}$',  # 9251234567 или 79251234567
        r'^\d{1}[- ]?\d{3}[- ]?\d{3}[- ]?\d{2}[- ]?\d{2}$',  # 7-925-123-45-67
    ]

    # Если строка состоит в основном из цифр и соответствует одному из паттернов
    if len(cleaned) >= 10 and any(re.match(pattern, text) for pattern in phone_patterns):
        return True

    # Дополнительная проверка: если больше половины символов - цифры
    digit_count = sum(c.isdigit() for c in text)
    if digit_count >= len(text) * 0.5 and digit_count >= 10:
        return True

    return False


def is_valid_name_format(name):
    """Проверяет корректность формата ФИО"""
    # Разрешаем кириллицу, пробелы, дефисы и апострофы
    name_pattern = r"^[а-яА-ЯёЁ\s'-]+$"

    if not re.match(name_pattern, name):
        return False

    # Проверяем, что есть хотя бы 2 слова (имя и фамилия)
    words = name.split()
    if len(words) < 2:
        return False

    # Проверяем, что каждое слово содержит буквы
    for word in words:
        if not any(c.isalpha() for c in word):
            return False

    return True


def normalize_full_name(text: str) -> str:
    """Нормализует ФИО: убирает лишние пробелы."""
    return re.sub(r"\s+", " ", (text or "").strip())


def validate_full_name_strict(full_name: str):
    """
    Более строгая проверка ФИО для шага ввода.
    Возвращает: (ok: bool, normalized: str, error: str|None)
    """
    normalized = normalize_full_name(full_name)

    if not normalized or len(normalized) < 2:
        return False, normalized, "ФИО слишком короткое."

    # Явная проверка на латиницу (частая ошибка раскладки)
    if re.search(r"[A-Za-z]", normalized):
        return False, normalized, "Обнаружена латиница. Введите ФИО кириллицей."

    # Ограничение на длину, чтобы не принимать «полотно текста»
    if len(normalized) > 80:
        return False, normalized, "ФИО слишком длинное. Введите только ФИО без лишнего текста."

    # Базовая проверка символов + минимум 2 слова
    if not is_valid_name_format(normalized):
        return False, normalized, "Некорректные символы в ФИО."

    words = normalized.split()

    # Защита от «слишком много слов»
    if len(words) > 5:
        return False, normalized, "Слишком много слов. Введите только Фамилию и Имя (и при необходимости Отчество)."

    # Проверки на структуру слов (без нач./конеч. дефисов/апострофов и без двойных знаков)
    for w in words:
        if w[0] in "-'" or w[-1] in "-'":
            return False, normalized, "Слова не должны начинаться или заканчиваться дефисом/апострофом."
        if "--" in w or "''" in w or "-'" in w or "'-" in w:
            return False, normalized, "Некорректное использование дефисов/апострофов."

        letters_only = [c for c in w if c.isalpha()]
        if len(letters_only) < 2:
            return False, normalized, "Каждая часть ФИО должна содержать минимум 2 буквы."

    return True, normalized, None


def has_digits(text):
    """Проверяет, есть ли в тексте цифры"""
    return any(char.isdigit() for char in text)


async def coach_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Главное меню тренера"""
    user_id = update.effective_user.id
    print(f"🏠 ПОЛЬЗОВАТЕЛЬ {user_id} ОТКРЫЛ МЕНЮ ТРЕНЕРА")

    try:
        with get_db_session() as session:
            user = get_user_by_telegram_id(session, user_id)

            if not user or get_user_role(user) != "coach":
                print(f"❌ У ПОЛЬЗОВАТЕЛЬ {user_id} НЕТ ДОСТУПА К МЕНЮ ТРЕНЕРА")
                await update.message.reply_text("❌ У вас нет доступа к этому меню")
                return

            reply_markup = get_coach_main_menu()

            await update.message.reply_text(
                "🏋️‍♂️ Меню тренера:\n\n"
                "Выберите действие.",
                reply_markup=reply_markup
            )

    except Exception as e:
        print(f"❌ ОШИБКА В МЕНЮ ТРЕНЕРА: {e}")
        await update.message.reply_text("❌ Произошла ошибка")


async def add_athlete_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало процесса добавления спортсмена"""
    user_id = update.effective_user.id
    logger.debug("add_athlete_start user_id=%s", user_id)

    # Очищаем данные предыдущего процесса
    context.user_data.clear()

    try:
        with get_db_session() as session:
            user = get_user_by_telegram_id(session, user_id)
            logger.debug("add_athlete_start user_id=%s found=%s type=%s", user_id, bool(user), type(user).__name__ if user else None)

            if not user:
                logger.info("add_athlete_start denied: user not in DB user_id=%s", user_id)
                await update.message.reply_text("❌ Пользователь не найден в базе данных. Используйте /start для регистрации.")
                return ConversationHandler.END

            user_role = get_user_role(user)
            logger.debug("add_athlete_start user_id=%s role=%s", user_id, user_role)

            if user_role != "coach":
                logger.info("add_athlete_start denied: wrong role user_id=%s role=%s", user_id, user_role)
                await update.message.reply_text(
                    "❌ Добавление спортсменов доступно только тренерам. Администратор использует своё меню."
                )
                return ConversationHandler.END

            if not isinstance(user, Coach):
                await update.message.reply_text("❌ У вас нет прав для добавления спортсменов")
                return ConversationHandler.END

            sport_type_name = None
            if user.sport_type_rel:
                sport_type_name = user.sport_type_rel.name
            elif user.sport_type:
                sport_type_name = user.sport_type
            if sport_type_name:
                context.user_data['sport_type'] = sport_type_name
                context.user_data['coach_id'] = user.id
                logger.info("add_athlete_start ok user_id=%s sport=%s", user_id, sport_type_name)

                await update.message.reply_text(
                    f"👤 <b>Добавить спортсмена</b>\n\n"
                    f"Вид спорта: <b>{sport_type_name}</b>\n\n"
                    "Введите ФИО спортсмена:",
                    parse_mode='HTML'
                )
                return ATHLETE_FULL_NAME
            await update.message.reply_text(
                "❌ У вас не указана спортивная специализация в профиле. Обратитесь к администратору."
            )
            return ConversationHandler.END

    except Exception as e:
        logger.exception("add_athlete_start error user_id=%s", user_id)
        await update.message.reply_text("❌ Произошла ошибка")
        return ConversationHandler.END


async def add_athlete_full_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка ФИО спортсмена с валидацией"""
    user_id = update.effective_user.id
    user_text = update.message.text
    logger.debug("add_athlete_full_name user_id=%s", user_id)

    # Проверяем, не является ли ввод кнопкой меню
    if user_text in MENU_BUTTONS:
        logger.info("add_athlete interrupted at full_name user_id=%s (menu)", user_id)
        await cancel_athlete_creation(update, context)
        return ConversationHandler.END

    full_name = normalize_full_name(user_text)

    # ВАЛИДАЦИЯ ФИО - проверяем, что это не номер телефона
    if is_phone_number(full_name):
        logger.debug("add_athlete_full_name user_id=%s rejected as phone-like", user_id)
        await update.message.reply_text(
            "❌ <b>Обнаружен номер телефона!</b>\n\n"
            "Вы ввели номер телефона вместо ФИО.\n"
            "Пожалуйста, введите <b>ФИО спортсмена</b> (только буквы):\n\n"
            "<i>Пример: Иванов Иван Иванович</i>",
            parse_mode='HTML'
        )
        return ATHLETE_FULL_NAME

    # Дополнительная строгая валидация ФИО (структура/пробелы/мусор)
    ok, normalized, error = validate_full_name_strict(full_name)
    if not ok:
        await update.message.reply_text(
            "❌ <b>Некорректное ФИО!</b>\n\n"
            f"Причина: <b>{html.escape(error or 'Проверьте ввод')}</b>\n\n"
            "Требования:\n"
            "• Минимум 2 слова (Фамилия Имя)\n"
            "• Только кириллица, пробелы, дефис и апостроф\n"
            "• Без цифр и лишнего текста\n\n"
            "<i>Пример: Иванов Иван Иванович</i>",
            parse_mode='HTML'
        )
        return ATHLETE_FULL_NAME

    # ВАЖНО: одинаковые ФИО допускаются. Уникальность проверяем по номеру телефона на следующем шаге.

    context.user_data['full_name'] = normalized
    logger.debug("add_athlete_full_name user_id=%s ok -> phone step", user_id)

    await update.message.reply_text(
        "📞 Теперь введите номер телефона спортсмена в формате:\n"
        "<b>XXX-XXX-XX-XX</b>\n\n"
        "<i>Пример: 925-123-45-67</i>",
        parse_mode='HTML'
    )
    return ATHLETE_PHONE


async def add_athlete_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка телефона спортсмена"""
    user_id = update.effective_user.id
    user_text = update.message.text
    logger.debug("add_athlete_phone user_id=%s", user_id)

    # Проверяем, не является ли ввод кнопкой меню
    if user_text in MENU_BUTTONS:
        logger.info("add_athlete interrupted at phone user_id=%s (menu)", user_id)
        await cancel_athlete_creation(update, context)
        return ConversationHandler.END

    # Проверяем, что пользователь не ввел ФИО вместо телефона
    if not has_digits(user_text) or is_valid_name_format(user_text):
        logger.debug("add_athlete_phone user_id=%s rejected as name-like", user_id)
        await update.message.reply_text(
            "❌ <b>Это похоже на ФИО, а не на телефон!</b>\n\n"
            "Пожалуйста, введите <b>номер телефона</b> в формате:\n"
            "<b>XXX-XXX-XX-XX</b>\n\n"
            "<i>Пример: 925-123-45-67</i>",
            parse_mode='HTML'
        )
        return ATHLETE_PHONE

    # Удаляем все нецифровые символы кроме дефисов
    cleaned_input = re.sub(r'[^\d-]', '', user_text)

    # Проверяем формат: XXX-XXX-XX-XX (9 цифр с дефисами)
    phone_pattern = r'^\d{3}-\d{3}-\d{2}-\d{2}$'

    if not re.match(phone_pattern, cleaned_input):
        logger.debug("add_athlete_phone user_id=%s invalid format", user_id)
        error_message = """❌ <b>Неверный формат телефона!</b>

📞 Правильный формат: <b>XXX-XXX-XX-XX</b>
Пример: <code>925-123-45-67</code>

Пожалуйста, введите телефон в правильном формате:"""

        await update.message.reply_text(error_message, parse_mode='HTML')
        return ATHLETE_PHONE

    # Если формат правильный - сохраняем полный номер
    full_phone = f"+7-{cleaned_input}"
    
    # Проверяем на дубликаты телефона
    with get_db_session() as session:
        existing_athlete = session.query(Athlete).filter_by(phone=full_phone).first()
        if existing_athlete:
            await update.message.reply_text(
                f"❌ <b>Спортсмен с таким телефоном уже существует!</b>\n\n"
                f"Телефон: <b>{html.escape(full_phone)}</b>\n"
                f"ФИО: {html.escape(existing_athlete.full_name)}\n\n"
                f"Пожалуйста, введите другой телефон или отмените добавление командой /cancel",
                parse_mode='HTML'
            )
            return ATHLETE_PHONE
        
        # Проверяем комбинацию ФИО + телефон (если ФИО уже было введено)
        if 'full_name' in context.user_data:
            existing_athlete = session.query(Athlete).filter_by(
                full_name=context.user_data['full_name'],
                phone=full_phone
            ).first()
            if existing_athlete:
                await update.message.reply_text(
                    f"❌ <b>Спортсмен с такими данными уже существует!</b>\n\n"
                    f"ФИО: <b>{html.escape(context.user_data['full_name'])}</b>\n"
                    f"Телефон: <b>{html.escape(full_phone)}</b>\n\n"
                    f"Пожалуйста, проверьте данные или отмените добавление командой /cancel",
                    parse_mode='HTML'
                )
                return ATHLETE_PHONE
    
    context.user_data['phone'] = full_phone
    logger.debug("add_athlete_phone user_id=%s ok -> birth_date step", user_id)

    await update.message.reply_text(
        "🎂 Введите дату рождения спортсмена в формате <b>ДД.ММ.ГГГГ</b>\n\n"
        "<i>Пример: 31.12.2010</i>",
        parse_mode="HTML"
    )
    return ATHLETE_BIRTH_DATE


async def add_athlete_birth_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка даты рождения спортсмена"""
    user_id = update.effective_user.id
    user_text = (update.message.text or "").strip()
    logger.debug("add_athlete_birth_date user_id=%s", user_id)

    # Проверяем, не является ли ввод кнопкой меню
    if user_text in MENU_BUTTONS:
        logger.info("add_athlete interrupted at birth_date user_id=%s (menu)", user_id)
        await cancel_athlete_creation(update, context)
        return ConversationHandler.END

    # Только формат ДД.ММ.ГГГГ (вариант "нет" не допускается)
    normalized = re.sub(r"[/-]", ".", user_text)
    if not re.match(r"^\d{1,2}\.\d{1,2}\.\d{4}$", normalized):
        await update.message.reply_text(
            "❌ <b>Неверный формат даты!</b>\n\n"
            "Введите дату рождения в формате <b>ДД.ММ.ГГГГ</b>\n"
            "<i>Пример: 19.06.1991</i>",
            parse_mode="HTML"
        )
        return ATHLETE_BIRTH_DATE

    day_s, month_s, year_s = normalized.split(".")
    day, month, year = int(day_s), int(month_s), int(year_s)

    if not (1 <= month <= 12):
        await update.message.reply_text(
            f"❌ Неверная дата: месяц <b>{month}</b> должен быть от <b>1</b> до <b>12</b>.\n"
            "Введите дату рождения в формате <b>ДД.ММ.ГГГГ</b>.",
            parse_mode="HTML"
        )
        return ATHLETE_BIRTH_DATE

    if not (1 <= day <= 31):
        await update.message.reply_text(
            f"❌ Неверная дата: день <b>{day}</b> должен быть от <b>1</b> до <b>31</b>.\n"
            "Введите дату рождения в формате <b>ДД.ММ.ГГГГ</b>.",
            parse_mode="HTML"
        )
        return ATHLETE_BIRTH_DATE

    try:
        bd = datetime(year, month, day)
    except ValueError:
        await update.message.reply_text(
            "❌ Такой даты не существует (проверьте день и месяц).\n"
            "Введите дату рождения в формате <b>ДД.ММ.ГГГГ</b>.",
            parse_mode="HTML"
        )
        return ATHLETE_BIRTH_DATE

    now = now_moscow()
    if bd > now:
        await update.message.reply_text("❌ Дата рождения не может быть в будущем. Введите корректную дату:")
        return ATHLETE_BIRTH_DATE

    if bd.year < 1900:
        await update.message.reply_text("❌ Слишком ранний год. Введите корректную дату рождения:")
        return ATHLETE_BIRTH_DATE

    context.user_data["birth_date"] = bd
    logger.debug("add_athlete_birth_date user_id=%s ok -> medical", user_id)

    await update.message.reply_text(
        "🏥 Введите медицинские противопоказания (или напишите <b>нет</b>, если отсутствуют):",
        parse_mode="HTML"
    )
    return ATHLETE_MEDICAL


async def add_athlete_medical(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка медицинской информации"""
    user_id = update.effective_user.id
    user_text = update.message.text.strip()
    logger.debug("add_athlete_medical user_id=%s", user_id)

    # Проверяем, не является ли ввод кнопкой меню
    if user_text in MENU_BUTTONS:
        logger.info("add_athlete interrupted at medical user_id=%s (menu)", user_id)
        await cancel_athlete_creation(update, context)
        return ConversationHandler.END

    # Обрабатываем кнопку "нет"
    if user_text.lower() == "нет":
        medical_info = "Нет противопоказаний"
    else:
        medical_info = user_text

    context.user_data['medical_info'] = medical_info
    logger.debug("add_athlete_medical user_id=%s ok -> age_group", user_id)

    keyboard = [
        [KeyboardButton("Детская"), KeyboardButton("Средняя")],
        [KeyboardButton("Взрослая")],
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text(
        "👦👨 Выберите возрастную группу:",
        reply_markup=reply_markup
    )
    return ATHLETE_AGE_GROUP


async def add_athlete_age_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка возрастной группы"""
    user_id = update.effective_user.id
    user_text = update.message.text
    logger.debug("add_athlete_age_group user_id=%s", user_id)

    # Проверяем, не является ли ввод кнопкой меню
    if user_text in MENU_BUTTONS:
        logger.info("add_athlete interrupted at age_group user_id=%s (menu)", user_id)
        await cancel_athlete_creation(update, context)
        return ConversationHandler.END

    # Только кнопки «Детская» / «Средняя» / «Взрослая»
    from utils.age_groups import BUTTON_LABELS, parse_age_group_button

    if user_text not in BUTTON_LABELS:
        await update.message.reply_text(
            "❌ Выберите возрастную группу кнопкой: <b>Детская</b>, <b>Средняя</b> или <b>Взрослая</b>.",
            parse_mode="HTML"
        )
        return ATHLETE_AGE_GROUP

    age_group_ru = user_text
    age_group = parse_age_group_button(age_group_ru)
    context.user_data['age_group'] = age_group
    logger.debug("add_athlete_age_group user_id=%s group=%s -> subscription", user_id, age_group)

    keyboard = [
        [KeyboardButton("Месячный"), KeyboardButton("Разовый")],
        [KeyboardButton("Индивидуальный")],
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text(
        "🎫 Выберите тип абонемента:",
        reply_markup=reply_markup
    )
    return ATHLETE_SUBSCRIPTION


def get_available_training_dates(sport_type, age_group, month=None, year=None, max_months=2):
    """Получить доступные даты тренировок для текущего и следующих месяцев по расписанию"""
    now = now_moscow()
    if month is None:
        month = now.month
        year = now.year
    
    schedule = TrainingManager.TRAINING_SCHEDULE.get(sport_type, {}).get(age_group)
    if not schedule:
        return []
    
    days = schedule['days']
    
    available_dates = []
    
    # Проверяем текущий и следующие месяцы (до max_months месяцев вперед)
    for month_offset in range(max_months):
        check_year = year
        check_month = month + month_offset
        
        # Обработка перехода через год
        while check_month > 12:
            check_month -= 12
            check_year += 1
        
        # Получаем первый и последний день месяца
        first_day = datetime(check_year, check_month, 1)
        last_day_num = calendar.monthrange(check_year, check_month)[1]
        last_day = datetime(check_year, check_month, last_day_num, 23, 59, 59)
        
        current_day = first_day
        
        # Проходим по всем дням месяца
        while current_day <= last_day:
            if current_day.weekday() in days:
                # Создаем datetime с правильным временем
                hour, minute = TrainingManager.get_hour_minute_for_weekday(schedule, current_day.weekday())
                training_datetime = current_day.replace(hour=hour, minute=minute, second=0, microsecond=0)
                # Слот ещё доступен до (начало + запас), см. ACTIVATION_GRACE_AFTER_START_MINUTES
                if now <= training_datetime + ACTIVATION_GRACE_AFTER_START:
                    available_dates.append(training_datetime)
            current_day += timedelta(days=1)
        
        # Если уже нашли достаточно дат, можно остановиться
        # Но пока ищем во всех месяцах для полноты
    
    # Сортируем даты по возрастанию
    available_dates.sort()
    
    return available_dates


def create_date_keyboard(sport_type, age_group, max_dates=20):
    """Создать клавиатуру с доступными датами тренировок"""
    dates = get_available_training_dates(sport_type, age_group)
    
    if not dates:
        return None
    
    # Ограничиваем количество отображаемых дат
    dates = dates[:max_dates]
    
    keyboard = []
    # Разбиваем даты на строки по 2 кнопки
    for i in range(0, len(dates), 2):
        row = []
        for j in range(2):
            if i + j < len(dates):
                date = dates[i + j]
                # Форматируем дату: "ДД.ММ (День недели) HH:MM"
                day_names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
                day_name = day_names[date.weekday()]
                btn_text = f"📅 {date.strftime('%d.%m')} ({day_name}) {date.strftime('%H:%M')}"
                callback_data = f"select_training_date_{date.strftime('%Y-%m-%d-%H-%M')}"
                row.append(InlineKeyboardButton(btn_text, callback_data=callback_data))
        if row:
            keyboard.append(row)
    
    return InlineKeyboardMarkup(keyboard) if keyboard else None


def create_add_athlete_training_calendar(sport_type, age_group, month=None, year=None):
    """Календарь выбора первой тренировки при добавлении спортсмена."""
    now = now_moscow()
    today = now.date()
    current_month = month or now.month
    current_year = year or now.year

    schedule = TrainingManager.TRAINING_SCHEDULE.get(sport_type, {}).get(age_group)
    if not schedule:
        return None
    training_days = set(schedule.get("days", []))

    cal = calendar.monthcalendar(current_year, current_month)
    keyboard = []

    day_names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    keyboard.append([InlineKeyboardButton(f"{d}.", callback_data="addath_ignore") for d in day_names])

    weeks_to_show = cal[:5]
    while len(weeks_to_show) < 5:
        weeks_to_show.append([0, 0, 0, 0, 0, 0, 0])

    for week in weeks_to_show:
        row = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(" ", callback_data="addath_ignore"))
                continue

            date_obj = datetime(current_year, current_month, day).date()
            weekday = date_obj.weekday()
            has_scheduled_training = weekday in training_days
            enabled = has_scheduled_training and date_obj >= today

            if date_obj == today:
                btn_text = f"[{day:2d}]"
            elif has_scheduled_training:
                btn_text = f"({day:2d})"
            else:
                btn_text = f"{day:2d}"

            callback_data = f"addath_date_{current_year}_{current_month}_{day}" if enabled else "addath_ignore"
            row.append(InlineKeyboardButton(btn_text, callback_data=callback_data))
        keyboard.append(row)

    prev_month = current_month - 1
    prev_year = current_year
    if prev_month < 1:
        prev_month = 12
        prev_year -= 1

    next_month = current_month + 1
    next_year = current_year
    if next_month > 12:
        next_month = 1
        next_year += 1

    keyboard.append([
        InlineKeyboardButton("◀️ Предыдущий", callback_data=f"addath_cal_{prev_year}_{prev_month}"),
        InlineKeyboardButton("Следующий ▶️", callback_data=f"addath_cal_{next_year}_{next_month}")
    ])

    if current_month != now.month or current_year != now.year:
        keyboard.append([
            InlineKeyboardButton("📅 Сегодня", callback_data=f"addath_cal_{now.year}_{now.month}")
        ])

    keyboard.append([InlineKeyboardButton("🏠 В меню", callback_data="back_to_menu_main")])

    return InlineKeyboardMarkup(keyboard)


def create_add_athlete_individual_calendar(month=None, year=None):
    """Календарь первой даты для индивидуального абонемента (любой будущий день)."""
    now = now_moscow()
    today = now.date()
    current_month = month or now.month
    current_year = year or now.year
    cal = calendar.monthcalendar(current_year, current_month)
    keyboard = []
    day_names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    keyboard.append([InlineKeyboardButton(f"{d}.", callback_data="addath_ignore") for d in day_names])
    weeks_to_show = cal[:5]
    while len(weeks_to_show) < 5:
        weeks_to_show.append([0, 0, 0, 0, 0, 0, 0])
    for week in weeks_to_show:
        row = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(" ", callback_data="addath_ignore"))
                continue
            date_obj = datetime(current_year, current_month, day).date()
            enabled = date_obj >= today
            if date_obj == today:
                btn_text = f"[{day:2d}]"
            elif enabled:
                btn_text = f"({day:2d})"
            else:
                btn_text = f"{day:2d}"
            cb = (
                f"addath_date_{current_year}_{current_month}_{day}"
                if enabled
                else "addath_ignore"
            )
            row.append(InlineKeyboardButton(btn_text, callback_data=cb))
        keyboard.append(row)
    prev_month = current_month - 1
    prev_year = current_year
    if prev_month < 1:
        prev_month = 12
        prev_year -= 1
    next_month = current_month + 1
    next_year = current_year
    if next_month > 12:
        next_month = 1
        next_year += 1
    keyboard.append(
        [
            InlineKeyboardButton(
                "◀️ Предыдущий",
                callback_data=f"addath_cal_{prev_year}_{prev_month}",
            ),
            InlineKeyboardButton(
                "Следующий ▶️",
                callback_data=f"addath_cal_{next_year}_{next_month}",
            ),
        ]
    )
    if current_month != now.month or current_year != now.year:
        keyboard.append(
            [
                InlineKeyboardButton(
                    "📅 Сегодня",
                    callback_data=f"addath_cal_{now.year}_{now.month}",
                )
            ]
        )
    keyboard.append([InlineKeyboardButton("🏠 В меню", callback_data="back_to_menu_main")])
    return InlineKeyboardMarkup(keyboard)


def _coach_add_athlete_individual_time_keyboard(
    session, coach_id: int, sport_type: str, year: int, month: int, day: int
) -> Optional[InlineKeyboardMarkup]:
    d = date(year, month, day)
    starts = iter_allowed_individual_starts(
        session, coach_id, sport_type, d, now_cutoff=now_moscow()
    )
    if not starts:
        return None
    rows = []
    row = []
    for st in starts:
        row.append(
            InlineKeyboardButton(
                st.strftime("%H:%M"),
                callback_data=f"addath_it_{db_utils_pkg.training_datetime_compact(st)}",
            )
        )
        if len(row) >= 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append(
        [
            InlineKeyboardButton(
                "🔙 К дате",
                callback_data=f"addath_cal_{year}_{month}",
            )
        ]
    )
    return InlineKeyboardMarkup(rows)


async def add_athlete_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка типа абонемента → переход к выбору первой даты тренировки."""
    user_id = update.effective_user.id
    user_text = update.message.text
    logger.debug("add_athlete_subscription user_id=%s", user_id)

    if user_text in MENU_BUTTONS:
        logger.info("add_athlete interrupted at subscription user_id=%s (menu)", user_id)
        await cancel_athlete_creation(update, context)
        return ConversationHandler.END

    if user_text not in ("Месячный", "Разовый", "Индивидуальный"):
        await update.message.reply_text(
            "❌ Выберите тип абонемента кнопкой: <b>Месячный</b>, <b>Разовый</b> или <b>Индивидуальный</b>.",
            parse_mode="HTML"
        )
        return ATHLETE_SUBSCRIPTION

    subscription_type_ru = user_text
    if subscription_type_ru == "Месячный":
        subscription_type = "monthly"
    elif subscription_type_ru == "Разовый":
        subscription_type = "single"
    else:
        subscription_type = "individual"
    context.user_data['subscription_type'] = subscription_type
    context.user_data['subscription_type_ru'] = subscription_type_ru
    logger.info("add_athlete_subscription user_id=%s type=%s -> calendar", user_id, subscription_type)

    sport_type = context.user_data['sport_type']
    age_group = context.user_data['age_group']
    if subscription_type == "individual":
        date_kb = create_add_athlete_individual_calendar()
    else:
        date_kb = create_add_athlete_training_calendar(sport_type, age_group)
    if not date_kb:
        await update.message.reply_text(
            "❌ Нет доступных дат тренировок по расписанию для этой группы. Обратитесь к администратору.",
            reply_markup=get_coach_main_menu()
        )
        return ConversationHandler.END

    await update.message.reply_text(
        "📅 Выберите <b>дату первой тренировки</b> по абонементу"
        + (" (затем — время слота)" if subscription_type == "individual" else "")
        + ":",
        parse_mode="HTML",
        reply_markup=date_kb
    )
    return ATHLETE_TRAINING_DATE


async def handle_add_athlete_calendar_nav(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Навигация календаря выбора первой тренировки при добавлении спортсмена."""
    query = update.callback_query
    await query.answer()

    if "subscription_type" not in context.user_data:
        await query.edit_message_text("Сессия добавления спортсмена завершена. Используйте меню «👥 Добавить спортсмена».")
        return ConversationHandler.END

    parts = (query.data or "").split("_")
    if len(parts) != 4:
        await query.answer("❌ Ошибка календаря")
        return ATHLETE_TRAINING_DATE

    year = int(parts[2])
    month = int(parts[3])
    sport_type = context.user_data.get("sport_type")
    age_group = context.user_data.get("age_group")
    if context.user_data.get("subscription_type") == "individual":
        reply_markup = create_add_athlete_individual_calendar(month=month, year=year)
    else:
        reply_markup = create_add_athlete_training_calendar(
            sport_type, age_group, month=month, year=year
        )
    if not reply_markup:
        await query.edit_message_text("❌ Не удалось построить календарь. Обратитесь к администратору.")
        return ConversationHandler.END

    await query.edit_message_reply_markup(reply_markup=reply_markup)
    return ATHLETE_TRAINING_DATE


async def handle_add_athlete_calendar_date_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка выбора дня в календаре добавления спортсмена."""
    query = update.callback_query

    if "subscription_type" not in context.user_data:
        await query.answer()
        await query.edit_message_text("Сессия добавления спортсмена завершена. Используйте меню «👥 Добавить спортсмена».")
        return ConversationHandler.END

    parts = (query.data or "").split("_")
    if len(parts) != 5:
        await query.answer("❌ Ошибка даты")
        return ATHLETE_TRAINING_DATE

    year = int(parts[2])
    month = int(parts[3])
    day = int(parts[4])
    sport_type = context.user_data.get("sport_type")
    age_group = context.user_data.get("age_group")

    if context.user_data.get("subscription_type") == "individual":
        coach_id = context.user_data.get("coach_id")
        if not coach_id:
            await query.answer()
            await query.edit_message_text("❌ Не найден coach_id в сессии добавления.")
            return ConversationHandler.END
        with get_db_session() as session:
            time_kb = _coach_add_athlete_individual_time_keyboard(
                session, coach_id, sport_type, year, month, day
            )
        await query.answer()
        if not time_kb:
            await query.edit_message_text(
                "❌ В этот день нет свободного слота. Выберите другую дату.",
                reply_markup=create_add_athlete_individual_calendar(month=month, year=year),
            )
            return ATHLETE_TRAINING_DATE
        await query.edit_message_text(
            "⏰ Выберите <b>время начала</b> индивидуальной тренировки (1 ч):",
            parse_mode="HTML",
            reply_markup=time_kb,
        )
        return ATHLETE_TRAINING_DATE

    schedule = TrainingManager.TRAINING_SCHEDULE.get(sport_type, {}).get(age_group)
    if not schedule:
        await query.answer()
        await query.edit_message_text("❌ Расписание для этой группы не найдено. Обратитесь к администратору.")
        return ConversationHandler.END

    coach_selected_date = datetime(year, month, day, 0, 0, 0)
    await query.answer()
    return await _finalize_add_athlete_from_selected_date(query, context, coach_selected_date)


async def handle_add_athlete_individual_time_pick(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    """Выбор времени индивидуальной первой тренировки при добавлении спортсмена."""
    query = update.callback_query
    await query.answer()
    if "subscription_type" not in context.user_data:
        await query.edit_message_text(
            "Сессия добавления спортсмена завершена. Используйте меню «👥 Добавить спортсмена»."
        )
        return ConversationHandler.END
    m = re.match(r"^addath_it_(\d{12})$", (query.data or "").strip())
    if not m:
        await query.edit_message_text("❌ Некорректная кнопка.")
        return ATHLETE_TRAINING_DATE
    start_dt = db_utils_pkg.parse_training_datetime_compact(m.group(1))
    if not start_dt:
        await query.edit_message_text("❌ Некорректное время.")
        return ATHLETE_TRAINING_DATE
    now = now_moscow()
    if now > start_dt + ACTIVATION_GRACE_AFTER_START:
        grace_min = int(ACTIVATION_GRACE_AFTER_START.total_seconds() // 60)
        with get_db_session() as session:
            coach_id = context.user_data.get("coach_id")
            sport_type = context.user_data.get("sport_type")
            if not coach_id or not sport_type:
                await query.edit_message_text("❌ Недостаточно данных в сессии.")
                return ConversationHandler.END
            await query.edit_message_text(
                "❌ Время для выбора этого слота истекло "
                f"(запас после начала {grace_min} мин). "
                "Выберите другое время.",
                reply_markup=_coach_add_athlete_individual_time_keyboard(
                    session,
                    coach_id,
                    sport_type,
                    start_dt.year,
                    start_dt.month,
                    start_dt.day,
                ),
            )
        return ATHLETE_TRAINING_DATE
    coach_id = context.user_data.get("coach_id")
    sport_type = context.user_data.get("sport_type")
    if not coach_id or not sport_type:
        await query.edit_message_text("❌ Недостаточно данных в сессии.")
        return ConversationHandler.END
    with get_db_session() as session:
        if db_utils_pkg.is_training_in_global_freeze(session, start_dt):
            await query.edit_message_text(
                "❌ Выбранное время в периоде массовой заморозки. Выберите другое время.",
                reply_markup=_coach_add_athlete_individual_time_keyboard(
                    session,
                    coach_id,
                    sport_type,
                    start_dt.year,
                    start_dt.month,
                    start_dt.day,
                ),
            )
            return ATHLETE_TRAINING_DATE
        if individual_slot_conflicts(session, coach_id, sport_type, start_dt):
            await query.edit_message_text(
                "❌ Слот занят или пересекается с другой тренировкой. Выберите другое время.",
                reply_markup=_coach_add_athlete_individual_time_keyboard(
                    session,
                    coach_id,
                    sport_type,
                    start_dt.year,
                    start_dt.month,
                    start_dt.day,
                ),
            )
            return ATHLETE_TRAINING_DATE
    return await _finalize_add_athlete_from_selected_date(
        query, context, start_dt.replace(hour=0, minute=0, second=0, microsecond=0),
        exact_start=start_dt,
        skip_freeze_confirm=True,
    )


async def handle_add_athlete_calendar_ignore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Игнорировать клики по неактивным ячейкам календаря."""
    query = update.callback_query
    await query.answer()
    return ATHLETE_TRAINING_DATE


def _parse_shift_confirm_callback_data(data: str):
    """Извлечь дату из addath_shift_confirm_YYYYMMDDHHMM."""
    if not data:
        return None
    prefix = "addath_shift_confirm_"
    if not data.startswith(prefix):
        return None
    suffix = data[len(prefix) :]
    return db_utils_pkg.parse_training_datetime_compact(suffix)


async def _finalize_add_athlete_from_selected_date(
    query,
    context,
    coach_selected_date: datetime,
    *,
    skip_freeze_confirm: bool = False,
    exact_start: Optional[datetime] = None,
):
    """Единая логика завершения добавления спортсмена по выбранной дате."""
    sport_type = context.user_data['sport_type']
    age_group = context.user_data['age_group']
    subscription_type = context.user_data['subscription_type']

    if exact_start is not None:
        start_date = exact_start
    else:
        start_date = db_utils_pkg._find_nearest_training_date(
            coach_selected_date.replace(hour=0, minute=0, second=0, microsecond=0),
            sport_type,
            age_group,
        )
    try:
        with get_db_session() as session:
            # Если первая тренировка попала в активную массовую заморозку,
            # просим подтверждение с автоматическим сдвигом.
            if (
                exact_start is None
                and db_utils_pkg.is_training_in_global_freeze(session, start_date)
                and not skip_freeze_confirm
            ):
                freeze = session.query(GlobalFreeze).filter(
                    GlobalFreeze.is_active == True,
                    GlobalFreeze.start_date <= start_date,
                    GlobalFreeze.end_date >= start_date
                ).order_by(GlobalFreeze.end_date.desc()).first()

                shifted_start = db_utils_pkg.find_next_non_frozen_training_date(
                    session,
                    (freeze.end_date + timedelta(seconds=1)) if freeze else (start_date + timedelta(days=1)),
                    sport_type,
                    age_group,
                )
                context.user_data["pending_shifted_start_date"] = shifted_start.isoformat()

                confirm_cb = f"addath_shift_confirm_{db_utils_pkg.training_datetime_compact(shifted_start)}"
                keyboard = InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("✅ Подтвердить сдвиг", callback_data=confirm_cb),
                        InlineKeyboardButton("❌ Выбрать другую дату", callback_data="addath_shift_cancel"),
                    ]
                ])
                freeze_label = (
                    f"{freeze.start_date.strftime('%d.%m.%Y')} — {freeze.end_date.strftime('%d.%m.%Y')}"
                    if freeze else "активной массовой заморозки"
                )
                await query.edit_message_text(
                    "⚠️ Выбранная первая тренировка попадает в период массовой заморозки.\n\n"
                    f"Период заморозки: <b>{freeze_label}</b>\n"
                    f"Предлагаемая новая дата старта: <b>{shifted_start.strftime('%d.%m.%Y %H:%M')}</b>\n\n"
                    "Подтвердить сдвиг и продолжить создание?",
                    parse_mode="HTML",
                    reply_markup=keyboard,
                )
                return ATHLETE_TRAINING_DATE

            if subscription_type == "monthly":
                end_date = db_utils_pkg._calculate_12th_training_date(start_date, sport_type, age_group)
            elif subscription_type == "individual":
                end_date = db_utils_pkg.individual_training_end_time(start_date)
            else:
                end_date = db_utils_pkg.training_end_time(start_date)

            from utils.discipline_keys import discipline_key_for

            sub_fmt = "individual" if subscription_type == "individual" else "group"
            dk = discipline_key_for(sport_type, format=sub_fmt)

            athlete = create_athlete(
                session=session,
                telegram_id=None,
                full_name=context.user_data['full_name'],
                phone=context.user_data['phone'],
                birth_date=context.user_data.get('birth_date'),
                medical_info=context.user_data['medical_info'],
                sport_type=sport_type,
                age_group=age_group,
                created_by=context.user_data.get("coach_id"),
                commit=False,
            )
            subscription = db_utils_pkg.create_subscription(
                session=session,
                athlete_id=athlete.id,
                subscription_type=subscription_type,
                sport_type=sport_type,
                discipline_key=dk,
                subscription_format=sub_fmt,
                commit=False,
            )
            subscription.start_date = start_date
            subscription.end_date = end_date
            subscription.is_active = True

            if subscription_type == "monthly":
                db_utils_pkg._create_and_deduct_scheduled_trainings(session, subscription, athlete, start_date, end_date)
            elif subscription_type == "individual":
                coach_id = athlete.created_by or context.user_data.get("coach_id")
                training = (
                    session.query(Training)
                    .filter(
                        Training.sport_type == sport_type,
                        Training.training_date == start_date,
                        Training.is_cancelled.is_(False),
                        Training.training_format == TRAINING_FORMAT_INDIVIDUAL,
                        Training.coach_id == coach_id,
                    )
                    .order_by(Training.id.asc())
                    .first()
                )
                if not training:
                    training = Training(
                        sport_type=sport_type,
                        age_group=INDIVIDUAL_TRAINING_AGE_GROUP_STORED,
                        training_date=start_date,
                        is_cancelled=False,
                        coach_id=coach_id,
                        training_format=TRAINING_FORMAT_INDIVIDUAL,
                    )
                    session.add(training)
                    session.flush()
                elif coach_id and not getattr(training, "coach_id", None):
                    training.coach_id = coach_id
                    session.flush()
            else:
                coach_id = athlete.created_by or context.user_data.get('coach_id')
                training = session.query(Training).filter_by(
                    sport_type=sport_type,
                    age_group=age_group,
                    training_date=start_date,
                    is_cancelled=False
                ).first()
                if not training:
                    training = find_group_training_on_calendar_day(
                        session,
                        sport_type=sport_type,
                        age_group=age_group,
                        day=start_date.date(),
                        coach_id=coach_id,
                    )
                if not training:
                    training = Training(
                        sport_type=sport_type,
                        age_group=age_group,
                        training_date=start_date,
                        is_cancelled=False,
                        coach_id=coach_id
                    )
                    session.add(training)
                    session.flush()
                elif coach_id and not getattr(training, "coach_id", None):
                    training.coach_id = coach_id
                    session.flush()

            # Защитная синхронизация после установки дат/типа.
            db_utils_pkg.sync_subscription_trainings_remaining(session, subscription)

            record_payment_on_subscription_activation(
                session,
                subscription,
                start_date,
                recorded_by_telegram_id=query.from_user.id,
            )

            session.commit()

            from utils.age_groups import format_age_group_label

            age_group_display = format_age_group_label(athlete.age_group)
            subscription_type_ru = context.user_data['subscription_type_ru']
            context.user_data.clear()

            logger.info(
                "add_athlete done athlete_id=%s name=%s subscription=%s first_training=%s",
                athlete.id,
                athlete.full_name,
                subscription_type_ru,
                start_date,
            )

            birth_date_display = (
                athlete.birth_date.strftime('%d.%m.%Y')
                if getattr(athlete, "birth_date", None) else "Не указана"
            )
            success_text = (
                f"✅ Спортсмен успешно добавлен!\n\n"
                f"📝 ФИО: {athlete.full_name}\n"
                f"📞 Телефон: {athlete.phone}\n"
                f"🎂 Дата рождения: {birth_date_display}\n"
                f"🥊 Вид спорта: {athlete.sport_type}\n"
                f"👥 Группа: {age_group_display}\n"
                f"🎫 Абонемент: {subscription_type_ru}\n"
                f"📅 Первая тренировка: {start_date.strftime('%d.%m.%Y %H:%M')}\n"
                f"🏥 Мед. информация: {athlete.medical_info}\n"
                f"💪 Осталось тренировок: {subscription.trainings_remaining}"
            )
            # Отправляем единое итоговое сообщение сразу с главным меню:
            # так не нужны служебные "тихие" сообщения и не появляется лишний вывод.
            await query.message.reply_text(
                success_text,
                reply_markup=get_coach_main_menu()
            )
            try:
                await query.message.delete()
            except Exception:
                pass
    except Exception as e:
        logger.error("add_athlete finalize failed: %s", e, exc_info=True)
        await query.edit_message_text("❌ Ошибка при добавлении спортсмена")

    return ConversationHandler.END


async def handle_add_athlete_shift_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Подтверждение автосдвига первой тренировки за пределы массовой заморозки."""
    query = update.callback_query
    await query.answer()

    # Дата в callback_data — чтобы подтверждение работало при потере user_data (несколько воркеров,
    # перезапуск процесса и т.д.). user_data оставляем как запасной путь для старых сообщений.
    data = (query.data or "").strip()
    shifted_from_cb = _parse_shift_confirm_callback_data(data)
    pending_raw = context.user_data.get("pending_shifted_start_date")

    if shifted_from_cb is not None and pending_raw:
        try:
            pending_dt = datetime.fromisoformat(pending_raw)
        except ValueError:
            pending_dt = None
        if pending_dt is not None:
            p = pending_dt.replace(tzinfo=None) if pending_dt.tzinfo else pending_dt
            p = p.replace(second=0, microsecond=0)
            c = shifted_from_cb.replace(second=0, microsecond=0)
            if p != c:
                logger.warning(
                    "add_athlete shift_confirm mismatch user_id=%s cb=%s pending=%s",
                    update.effective_user.id,
                    c,
                    p,
                )
                await query.edit_message_text(
                    "❌ Кнопка не соответствует текущему сценарию. Выберите дату в календаре снова."
                )
                return ATHLETE_TRAINING_DATE

    shifted_start = shifted_from_cb
    if shifted_start is None and pending_raw:
        try:
            shifted_start = datetime.fromisoformat(pending_raw)
        except ValueError:
            shifted_start = None
    if shifted_start is None:
        await query.edit_message_text("❌ Данные сессии утеряны. Выберите дату снова в календаре.")
        return ATHLETE_TRAINING_DATE

    context.user_data.pop("pending_shifted_start_date", None)
    return await _finalize_add_athlete_from_selected_date(
        query,
        context,
        shifted_start,
        skip_freeze_confirm=True,
    )


async def handle_add_athlete_shift_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отмена автосдвига: вернуть тренера к выбору даты в календаре."""
    query = update.callback_query
    await query.answer()

    context.user_data.pop("pending_shifted_start_date", None)
    sport_type = context.user_data.get("sport_type")
    age_group = context.user_data.get("age_group")
    calendar_kb = create_add_athlete_training_calendar(sport_type, age_group)
    if not calendar_kb:
        await query.edit_message_text("❌ Календарь недоступен. Попробуйте снова.")
        return ConversationHandler.END

    await query.edit_message_text(
        "📅 Выберите <b>первую дату тренировки</b> по абонементу:",
        parse_mode="HTML",
        reply_markup=calendar_kb,
    )
    return ATHLETE_TRAINING_DATE


async def handle_training_date_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выбор первой даты тренировки → создание спортсмена + абонемент с датами (месячный или разовый)."""
    query = update.callback_query
    await query.answer()

    if "subscription_type" not in context.user_data:
        await query.edit_message_text("Сессия добавления спортсмена завершена. Используйте меню «👥 Добавить спортсмена».")
        return ConversationHandler.END

    # Парсим дату из callback_data: select_training_date_YYYY-MM-DD-HH-MM
    date_str = query.data.replace("select_training_date_", "")
    try:
        year, month, day, hour, minute = map(int, date_str.split("-"))
        coach_selected_date = datetime(year, month, day, hour, minute)
    except Exception as e:
        logger.warning("handle_training_date_selection parse error: %s", e)
        await query.edit_message_text("❌ Ошибка при обработке выбранной даты")
        return ConversationHandler.END

    return await _finalize_add_athlete_from_selected_date(query, context, coach_selected_date)


async def athletes_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать меню выбора категории для списка спортсменов тренера"""
    user_id = update.effective_user.id
    logger.debug("athletes_list user_id=%s", user_id)

    try:
        with get_db_session() as session:
            user = get_user_by_telegram_id(session, user_id)
            logger.debug("athletes_list user_id=%s found=%s type=%s", user_id, bool(user), type(user).__name__ if user else None)

            if not user or get_user_role(user) != 'coach':
                if update.callback_query:
                    await update.callback_query.answer("❌ У вас нет доступа")
                else:
                    await update.message.reply_text("❌ У вас нет доступа к этому меню")
                return

            athletes, message_header = load_athletes_for_list(session, user)

            if not athletes:
                if update.callback_query:
                    await update.callback_query.answer()
                    await update.callback_query.edit_message_text(
                        "📭 У вас пока нет спортсменов.\n\n"
                        "Добавьте первого спортсмена через меню '👥 Добавить спортсмена'"
                    )
                else:
                    await update.message.reply_text(
                        "📭 У вас пока нет спортсменов.\n\n"
                        "Добавьте первого спортсмена через меню '👥 Добавить спортсмена'"
                    )
                return

            # Считаем статистику по категориям
            from utils.subscription_checker import SubscriptionChecker

            def is_active_status(status: str) -> bool:
                # expiring_soon всё еще считаем активным
                return status in ("active", "expiring_soon")

            total_athletes = len(athletes)
        
            # Подсчет активных/неактивных с разбивкой на детей/взрослых
            from utils.age_groups import AGE_GROUP_ADULTS, AGE_GROUP_CODES

            active_by_group = {g: 0 for g in AGE_GROUP_CODES}
            inactive_by_group = {g: 0 for g in AGE_GROUP_CODES}

            coach_sport = coach_sport_type_name(user)

            for a in athletes:
                sub = subscription_for_coach_sport(a, coach_sport)
                status = (
                    SubscriptionChecker.get_subscription_status(sub) if sub else "no_subscription"
                )
                is_active = is_active_status(status)
                grp = (a.age_group or "").strip() or AGE_GROUP_ADULTS
                if grp not in active_by_group:
                    grp = AGE_GROUP_ADULTS
                if is_active:
                    active_by_group[grp] += 1
                else:
                    inactive_by_group[grp] += 1

            active_total = sum(active_by_group.values())
            inactive_total = sum(inactive_by_group.values())

            message = message_header
            message += "<b>Выберите категорию:</b>"

            # Меню категорий - сначала активные/неактивные
            keyboard = [
                [
                    InlineKeyboardButton(f"✅ Активные ({active_total})", callback_data="athletes_active"),
                    InlineKeyboardButton(f"❌ Неактивные ({inactive_total})", callback_data="athletes_inactive"),
                ],
                [
                    InlineKeyboardButton(f"📋 Все ({total_athletes})", callback_data="athletes_all"),
                ],
                [InlineKeyboardButton("🏠 В меню", callback_data="back_to_menu_main")],
            ]

            reply_markup = InlineKeyboardMarkup(keyboard)

            # Отправляем или редактируем сообщение
            if update.callback_query:
                await update.callback_query.answer()
                await update.callback_query.edit_message_text(
                    message,
                    reply_markup=reply_markup,
                    parse_mode='HTML'
                )
            else:
                await update.message.reply_text(
                    message,
                    reply_markup=reply_markup,
                    parse_mode='HTML'
                )

    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        print(f"❌ ОШИБКА ПРИ ПОЛУЧЕНИИ СПИСКА СПОРТСМЕНОВ: {e}")
        print(f"❌ ТРАССИРОВКА: {error_trace}")
        logger.error(f"❌ ОШИБКА ПРИ ПОЛУЧЕНИИ СПИСКА СПОРТСМЕНОВ: {e}", exc_info=True)
        error_msg = "❌ Ошибка при загрузке списка спортсменов"
        if update.callback_query:
            await update.callback_query.answer(error_msg)
        else:
            await update.message.reply_text(error_msg)


async def athletes_list_filtered(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать список спортсменов по выбранному фильтру или подменю"""
    query = update.callback_query
    filter_key = (query.data or "").replace("athletes_", "").strip()

    # Если выбран active или inactive, показываем подменю с детьми/взрослыми
    if filter_key == "active":
        await query.answer()
        await show_active_inactive_submenu(update, context, "active")
        return
    if filter_key == "inactive":
        await query.answer()
        await show_active_inactive_submenu(update, context, "inactive")
        return
    if filter_key in (
        "active_children",
        "active_middle",
        "active_adults",
        "inactive_children",
        "inactive_middle",
        "inactive_adults",
        "all",
        "children",
        "middle",
        "adults",
        "inactive",
    ):
        await query.answer()
        await show_athletes_list_by_filter(update, context, filter_key)
        return

    await query.answer("❌ Неизвестный фильтр", show_alert=True)


async def show_active_inactive_submenu(update: Update, context: ContextTypes.DEFAULT_TYPE, status_type: str):
    """Показать подменю с детьми/взрослыми для активных или неактивных"""
    user_id = update.effective_user.id
    try:
        with get_db_session() as session:
            user = get_user_by_telegram_id(session, user_id)

            if not user or get_user_role(user) != 'coach':
                if update.callback_query:
                    await update.callback_query.answer("❌ У вас нет доступа")
                return

            athletes, message_header = load_athletes_for_list(session, user)

            from utils.subscription_checker import SubscriptionChecker

            def is_active_status(status: str) -> bool:
                return status in ("active", "expiring_soon")

            # Подсчет детей и взрослых в выбранной категории
            from utils.age_groups import AGE_GROUP_ADULTS, AGE_GROUP_CODES, SHORT_LABELS

            counts = {g: 0 for g in AGE_GROUP_CODES}

            coach_sport = coach_sport_type_name(user)

            for a in athletes:
                sub = subscription_for_coach_sport(a, coach_sport)
                status = (
                    SubscriptionChecker.get_subscription_status(sub) if sub else "no_subscription"
                )
                is_active = is_active_status(status)
                grp = (a.age_group or "").strip() or AGE_GROUP_ADULTS
                if grp not in counts:
                    grp = AGE_GROUP_ADULTS

                if status_type == "active" and is_active:
                    counts[grp] += 1
                elif status_type == "inactive" and not is_active:
                    counts[grp] += 1

            status_label = "✅ <b>Активные</b>" if status_type == "active" else "❌ <b>Неактивные</b>"
            message = message_header + status_label + "\n\n"
            message += "<b>Выберите возрастную группу:</b>"

            group_buttons = [
                InlineKeyboardButton(
                    f"{SHORT_LABELS[g]} ({counts[g]})",
                    callback_data=f"athletes_{status_type}_{g}",
                )
                for g in AGE_GROUP_CODES
            ]
            keyboard = [
                group_buttons,
                [
                    InlineKeyboardButton("🔙 К категориям", callback_data="athletes_categories"),
                    InlineKeyboardButton("🏠 В меню", callback_data="back_to_menu_main"),
                ],
            ]

            reply_markup = InlineKeyboardMarkup(keyboard)

            if update.callback_query:
                await update.callback_query.edit_message_text(
                    message,
                    reply_markup=reply_markup,
                    parse_mode='HTML'
                )
    except Exception as e:
        print(f"❌ ОШИБКА ПРИ ПОКАЗЕ ПОДМЕНЮ: {e}")
        if update.callback_query:
            await update.callback_query.answer("❌ Ошибка при загрузке меню")


async def athletes_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Вернуться к экрану выбора категорий"""
    # сбрасываем последний фильтр и страницу, чтобы «к списку» из карточки возвращал в категории
    context.user_data.pop("athletes_list_filter", None)
    context.user_data.pop("athletes_list_page", None)
    await athletes_list(update, context)


async def athletes_list_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Перелистывание страниц списка спортсменов (callback alpg_*)."""
    query = update.callback_query
    parsed = decode_athlete_list_page(query.data or "")
    if not parsed:
        await query.answer("❌ Некорректная страница", show_alert=True)
        return
    await query.answer()
    filter_key, page = parsed
    await show_athletes_list_by_filter(update, context, filter_key, page=page)


async def show_athletes_list_by_filter(
    update: Update, context: ContextTypes.DEFAULT_TYPE, filter_key: str, page: int = 0
):
    """Отрисовать список спортсменов в зависимости от фильтра (all/children/adults/inactive)."""
    user_id = update.effective_user.id
    try:
        with get_db_session() as session:
            user = get_user_by_telegram_id(session, user_id)

            if not user or get_user_role(user) != 'coach':
                if update.callback_query:
                    await update.callback_query.answer("❌ У вас нет доступа")
                else:
                    await update.message.reply_text("❌ У вас нет доступа к этому меню")
                return

            # Запоминаем фильтр, чтобы возврат «📋 К списку» из карточки работал ожидаемо
            context.user_data["athletes_list_filter"] = filter_key

            athletes, header_base = load_athletes_for_list(session, user)

            from utils.subscription_checker import SubscriptionChecker

            def is_active_status(status: str) -> bool:
                return status in ("active", "expiring_soon")

            coach_sport = coach_sport_type_name(user)

            def athlete_status(a: Athlete) -> str:
                sub = subscription_for_coach_sport(a, coach_sport)
                return (
                    SubscriptionChecker.get_subscription_status(sub) if sub else "no_subscription"
                )

            # Фильтрация
            from utils.age_groups import format_age_group_label

            def _matches_group(athlete: Athlete, group_code: str) -> bool:
                return (athlete.age_group or "").strip() == group_code

            if filter_key == "active_children":
                filtered = [a for a in athletes if _matches_group(a, "children") and is_active_status(athlete_status(a))]
                filter_title = "✅ <b>Активные — детская группа</b>\n\n"
            elif filter_key == "active_middle":
                filtered = [a for a in athletes if _matches_group(a, "middle") and is_active_status(athlete_status(a))]
                filter_title = "✅ <b>Активные — средняя группа</b>\n\n"
            elif filter_key == "active_adults":
                filtered = [a for a in athletes if _matches_group(a, "adults") and is_active_status(athlete_status(a))]
                filter_title = "✅ <b>Активные — взрослая группа</b>\n\n"
            elif filter_key == "inactive_children":
                filtered = [a for a in athletes if _matches_group(a, "children") and not is_active_status(athlete_status(a))]
                filter_title = "❌ <b>Неактивные — детская группа</b>\n\n"
            elif filter_key == "inactive_middle":
                filtered = [a for a in athletes if _matches_group(a, "middle") and not is_active_status(athlete_status(a))]
                filter_title = "❌ <b>Неактивные — средняя группа</b>\n\n"
            elif filter_key == "inactive_adults":
                filtered = [a for a in athletes if _matches_group(a, "adults") and not is_active_status(athlete_status(a))]
                filter_title = "❌ <b>Неактивные — взрослая группа</b>\n\n"
            elif filter_key == "all":
                filtered = list(athletes)
                filter_title = "📋 <b>Все спортсмены</b>\n\n"
            else:
                # Старые фильтры для обратной совместимости
                if filter_key == "children":
                    filtered = [a for a in athletes if _matches_group(a, "children")]
                    filter_title = "👶 <b>Детская группа</b>\n\n"
                elif filter_key == "middle":
                    filtered = [a for a in athletes if _matches_group(a, "middle")]
                    filter_title = "🧒 <b>Средняя группа</b>\n\n"
                elif filter_key == "adults":
                    filtered = [a for a in athletes if _matches_group(a, "adults")]
                    filter_title = "👨‍🦰 <b>Взрослая группа</b>\n\n"
                elif filter_key == "inactive":
                    filtered = [a for a in athletes if not is_active_status(athlete_status(a))]
                    filter_title = "❌ <b>Неактивные абонементы / нет абонемента</b>\n\n"
                else:
                    filtered = list(athletes)
                    filter_title = "📋 <b>Все спортсмены</b>\n\n"

            filtered.sort(key=lambda a: (a.full_name or "").strip().lower())

            if not filtered:
                message = header_base + filter_title + "📭 В этой категории пока нет спортсменов."
                keyboard = [
                    [InlineKeyboardButton("🔙 К категориям", callback_data="athletes_categories")],
                    [InlineKeyboardButton("🏠 В меню", callback_data="back_to_menu_main")],
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                if update.callback_query:
                    await update.callback_query.edit_message_text(message, reply_markup=reply_markup, parse_mode="HTML")
                else:
                    await update.message.reply_text(message, reply_markup=reply_markup, parse_mode="HTML")
                return

            # Определяем вид спорта для заголовка (берем первый найденный)
            sport_type = None
            for a in filtered:
                if a.sport_type:
                    sport_type = a.sport_type
                    break
        
            # Добавляем вид спорта в заголовок, если он есть
            if sport_type:
                filter_title = filter_title.replace("</b>", f" - {sport_type}</b>")

            total_count = len(filtered)
            total_pages = max(1, (total_count + ATHLETE_LIST_PAGE_SIZE - 1) // ATHLETE_LIST_PAGE_SIZE)
            page = max(0, min(int(page), total_pages - 1))
            context.user_data["athletes_list_page"] = page
            start = page * ATHLETE_LIST_PAGE_SIZE
            page_slice = filtered[start : start + ATHLETE_LIST_PAGE_SIZE]

            # Формируем текст
            message = header_base + filter_title
            if total_pages > 1:
                message += f"\n📄 Страница <b>{page + 1}</b> из <b>{total_pages}</b> · всего <b>{total_count}</b>\n"

            # Клавиатура спортсменов (текущая страница, по 2 в ряд)
            keyboard = []
            for i in range(0, len(page_slice), 2):
                row = []
                for j in range(2):
                    if i + j < len(page_slice):
                        a = page_slice[i + j]

                        status = athlete_status(a)
                        icons = []
                        if status == "active":
                            icons.append("✅")
                        elif status == "expiring_soon":
                            icons.append("🟡")
                        elif status == "expired":
                            icons.append("🔴")
                        else:
                            icons.append("❌")

                        name = a.full_name.strip() if a.full_name else ""
                        if len(name) > 12:
                            name = name[:10] + "..."
                    
                        button_text = f"{''.join(icons)} {name}"

                        row.append(InlineKeyboardButton(button_text, callback_data=f"athlete_{a.id}"))
                if row:
                    keyboard.append(row)

            if total_pages > 1:
                nav_row = []
                if page > 0:
                    nav_row.append(
                        InlineKeyboardButton(
                            "◀️ Назад",
                            callback_data=encode_athlete_list_page(filter_key, page - 1),
                        )
                    )
                remaining = total_count - (page + 1) * ATHLETE_LIST_PAGE_SIZE
                if page < total_pages - 1:
                    label = f"▶️ Ещё ({remaining})" if remaining <= 99 else "▶️ Ещё"
                    nav_row.append(
                        InlineKeyboardButton(
                            label,
                            callback_data=encode_athlete_list_page(filter_key, page + 1),
                        )
                    )
                keyboard.append(nav_row)

            keyboard.append([InlineKeyboardButton("🔙 К категориям", callback_data="athletes_categories")])
            keyboard.append([InlineKeyboardButton("🏠 В меню", callback_data="back_to_menu_main")])

            reply_markup = InlineKeyboardMarkup(keyboard)

            if update.callback_query:
                await update.callback_query.edit_message_text(message, reply_markup=reply_markup, parse_mode="HTML")
            else:
                await update.message.reply_text(message, reply_markup=reply_markup, parse_mode="HTML")

    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        print(f"❌ ОШИБКА ПРИ ПОКАЗЕ СПИСКА СПОРТСМЕНОВ (ФИЛЬТР={filter_key}): {e}")
        print(f"❌ ТРАССИРОВКА: {error_trace}")
        logger.error(f"❌ ОШИБКА ПРИ ПОКАЗЕ СПИСКА СПОРТСМЕНОВ (ФИЛЬТР={filter_key}): {e}", exc_info=True)
        if update.callback_query:
            await update.callback_query.answer("❌ Ошибка при загрузке списка")
        else:
            await update.message.reply_text("❌ Ошибка при загрузке списка спортсменов")

async def handle_show_more_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать информацию о количестве спортсменов"""
    query = update.callback_query
    await query.answer("В текущей версии отображаются первые 20 спортсменов")


async def handle_back_to_menu_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Возврат в главное меню из списка"""
    query = update.callback_query
    await query.answer("Возвращаемся в меню...")

    # Отправляем новое сообщение с меню тренера
    await query.message.reply_text(
        "🏋️‍♂️ Меню тренера:\n\n"
        "Выберите действие.",
        reply_markup=get_coach_main_menu()
    )

async def cancel_athlete_creation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отмена процесса добавления спортсмена"""
    user_id = update.effective_user.id
    logger.info("add_athlete cancelled user_id=%s", user_id)

    # Очищаем данные процесса
    context.user_data.clear()

    await update.message.reply_text(
        "❌ Добавление спортсмена отменено.\n\n"
        "Выберите действие в меню.",
        reply_markup=get_coach_main_menu()
    )

    return ConversationHandler.END


async def cancel_global_freeze(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отмена процесса массовой заморозки."""
    user_id = update.effective_user.id
    print(f"🚫 ПОЛЬЗОВАТЕЛЬ {user_id} ОТМЕНИЛ МАССОВУЮ ЗАМОРОЗКУ")

    # Очищаем только то, что относится к диалогу массовой заморозки.
    # Остальные пользовательские данные (если были) сохраняем.
    for k in ("gf_start_date", "gf_end_date", "gf_title"):
        context.user_data.pop(k, None)

    await update.message.reply_text(
        "❌ Операция массовой заморозки отменена.\n\n"
        "Выберите действие в меню.",
        reply_markup=get_coach_main_menu(),
    )
    return ConversationHandler.END


async def start_training(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Шаг 1: список тренировок на сегодня для отметки посещений."""
    from services.attendance_training_flow import build_today_attendance_slots

    user_id = update.effective_user.id
    query = update.callback_query
    if query:
        await query.answer()

    logger.info("📝 Запрос на отметку посещений от пользователя %s", user_id)
    try:
        with get_db_session() as session:
            user = get_user_by_telegram_id(session, user_id)

            if not user or get_user_role(user) != 'coach':
                if query:
                    await query.edit_message_text("❌ У вас нет доступа к этому меню")
                else:
                    await update.message.reply_text("❌ У вас нет доступа к этому меню")
                return

            now = now_moscow()
            slot_rows, virtual_slots = build_today_attendance_slots(session, user, now)
            context.user_data["attendance_virtual_slots"] = virtual_slots

            def _trainings_count_label(count: int) -> str:
                n = abs(int(count))
                if n % 10 == 1 and n % 100 != 11:
                    word = "тренировка"
                elif n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
                    word = "тренировки"
                else:
                    word = "тренировок"
                return f"{n} {word}"

            today_str = now.strftime("%d.%m.%Y")
            # Из меню (ReplyKeyboard) заголовок уже в сообщении пользователя — не дублируем.
            message_parts: List[str] = []
            if query:
                message_parts.append("📝 <b>Отметить посещения</b>\n\n")
            if slot_rows:
                planned_label = _trainings_count_label(len(slot_rows))
                message_parts.append(
                    f"Сегодня: <b>{today_str}</b> — У Вас запланировано: "
                    f"<b>{planned_label}</b>.\n"
                    "Выберите тренировку.\n\n"
                )
            else:
                message_parts.append(
                    f"Сегодня — <b>{today_str}</b>\n"
                    "У Вас нет запланированных тренировок.\n\n"
                )
            message = "".join(message_parts)

            keyboard = []
            for slot in slot_rows:
                from utils.age_groups import format_age_group_label

                slot_body = format_training_slot_body(
                    slot.sport_type,
                    age_group=slot.age_group,
                    is_individual=slot.is_individual_format,
                )
                slot_start = slot.training_datetime
                slot_end = (
                    individual_training_end_time(slot_start)
                    if slot.is_individual_format
                    else training_end_time(slot_start)
                )
                is_live = slot_start <= now <= slot_end
                prefix = "" if is_live else "🔒 "
                time_str = slot.training_datetime.strftime("%H:%M")
                button_text = f"{prefix}🕒 {time_str} - {slot_body}"
                if is_live:
                    callback_data = (
                        f"select_mark_training_virtual_{slot.virtual_token}"
                        if slot.is_virtual
                        else f"select_mark_training_{slot.training_id}"
                    )
                else:
                    callback_data = "attendance_slot_locked"
                keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])

            keyboard.append([InlineKeyboardButton("🏠 В меню", callback_data="back_to_menu_main")])

            reply_markup = InlineKeyboardMarkup(keyboard)
            if query:
                try:
                    await query.edit_message_text(
                        message, reply_markup=reply_markup, parse_mode="HTML"
                    )
                except BadRequest as br:
                    if "message is not modified" in str(br).lower():
                        hint = (
                            "У Вас нет запланированных тренировок на сегодня."
                            if not slot_rows
                            else "Список без изменений."
                        )
                        await query.answer(hint, show_alert=False)
                        return
                    raise
            else:
                await update.message.reply_text(
                    message, reply_markup=reply_markup, parse_mode="HTML"
                )

    except Exception as e:
        logger.error("❌ Ошибка в start_training: %s", e, exc_info=True)
        if query:
            await query.edit_message_text("❌ Ошибка при загрузке тренировок")
        else:
            await update.message.reply_text("❌ Ошибка при загрузке тренировок")


async def handle_attendance_training_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Снова показать список тренировок на сегодня (например «🔙 К тренировкам на сегодня»)."""
    await start_training(update, context)


async def show_coach_calendar(update: Update, context: ContextTypes.DEFAULT_TYPE, month: int = None, year: int = None):
    """Показать календарь тренировок тренера с промаркированными днями и навигацией"""
    user_id = update.effective_user.id
    logger.debug("show_coach_calendar: user_id=%s", user_id)

    try:
        with get_db_session() as session:
            user = get_user_by_telegram_id(session, user_id)

            if not user or get_user_role(user) != 'coach':
                if update.callback_query:
                    await update.callback_query.answer("❌ У вас нет доступа")
                else:
                    await update.message.reply_text("❌ У вас нет доступа к этому меню")
                return

            if isinstance(user, Coach):
                user = (
                    session.query(Coach)
                    .options(joinedload(Coach.sport_type_rel))
                    .filter_by(id=user.id)
                    .first()
                )
                if not user:
                    if update.callback_query:
                        await update.callback_query.answer("❌ Пользователь не найден")
                    else:
                        await update.message.reply_text("❌ Пользователь не найден")
                    return

            # Получаем текущую дату или используем переданные параметры
            now = now_moscow()
            today = now.date()

            if month is None:
                current_month = now.month
            else:
                current_month = month

            if year is None:
                current_year = now.year
            else:
                current_year = year

            sport_type_name = coach_sport_type_name(user)
            if get_user_role(user) == "coach" and not sport_type_name:
                if update.callback_query:
                    await update.callback_query.answer("❌ У вас не указан вид спорта")
                else:
                    await update.message.reply_text(
                        "❌ У вас не указан вид спорта. Обратитесь к администратору.",
                        parse_mode='HTML'
                    )
                return

            # Получаем тренировки для отображаемого месяца
            month_start = datetime(current_year, current_month, 1)
            if current_month == 12:
                month_end = datetime(current_year + 1, 1, 1)
            else:
                month_end = datetime(current_year, current_month + 1, 1)

            query = session.query(Training).filter(
                Training.coach_id == user.id,
                Training.training_date >= month_start,
                Training.training_date < month_end,
                Training.is_cancelled == False
            )
            if sport_type_name:
                query = query.filter(Training.sport_type == sport_type_name)
            trainings = query.order_by(Training.training_date.asc()).all()
            trainings = dedupe_individual_trainings_by_slot(trainings)
            trainings = [
                t
                for t in trainings
                if (
                    (getattr(t, "training_format", None) or "").strip().lower()
                    != TRAINING_FORMAT_INDIVIDUAL
                )
                or individual_slot_has_links(session, t, coach_id=user.id)
            ]

            # Группируем тренировки по датам
            trainings_by_date = {}
            for training in trainings:
                date_key = training.training_date.date()
                if date_key not in trainings_by_date:
                    trainings_by_date[date_key] = []
                trainings_by_date[date_key].append(training)

            # Дни недели с тренировками по расписанию (для тренера с видом спорта)
            scheduled_days = _coach_scheduled_weekdays(sport_type_name)

            message = _coach_calendar_message_header(
                current_year=current_year,
                current_month=current_month,
            )

            # Календарь (monthcalendar: недели с понедельника)
            cal = calendar.monthcalendar(current_year, current_month)
            keyboard = []

            day_names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
            day_names_buttons = [
                InlineKeyboardButton(f"{day_name}.", callback_data="cal_empty") for day_name in day_names
            ]
            keyboard.append(day_names_buttons)

            weeks_to_show = list(cal)
            while len(weeks_to_show) < 5:
                weeks_to_show.append([0, 0, 0, 0, 0, 0, 0])

            for week in weeks_to_show:
                week_buttons = []
                for day in week:
                    if day == 0:
                        week_buttons.append(InlineKeyboardButton(" ", callback_data="cal_empty"))
                    else:
                        date_obj = datetime(current_year, current_month, day).date()
                        weekday = date_obj.weekday()
                        has_scheduled_training = weekday in scheduled_days
                        has_db_training = date_obj in trainings_by_date

                        if date_obj == today:
                            btn_text = f"[{day:2d}]"
                        elif has_db_training:
                            btn_text = f"+{day:2d}"
                        elif has_scheduled_training:
                            btn_text = f"{day}•"
                        else:
                            btn_text = f"{day:2d}"

                        callback_data = f"cal_date_{current_year}_{current_month}_{day}"
                        week_buttons.append(InlineKeyboardButton(btn_text, callback_data=callback_data))

                keyboard.append(week_buttons)
        
            # Кнопки навигации по месяцам
            prev_month = current_month - 1
            prev_year = current_year
            if prev_month < 1:
                prev_month = 12
                prev_year -= 1
            
            next_month = current_month + 1
            next_year = current_year
            if next_month > 12:
                next_month = 1
                next_year += 1
        
            keyboard.append([
                InlineKeyboardButton("◀️ Предыдущий", callback_data=f"calendar_{prev_year}_{prev_month}"),
                InlineKeyboardButton("Следующий ▶️", callback_data=f"calendar_{next_year}_{next_month}")
            ])
        
            # Кнопка "Сегодня"
            if current_month != now.month or current_year != now.year:
                keyboard.append([
                    InlineKeyboardButton("📅 Сегодня", callback_data=f"calendar_{now.year}_{now.month}")
                ])
        
            # Кнопка возврата в меню
            keyboard.append([
                InlineKeyboardButton("🏠 В меню", callback_data="back_to_menu_main")
            ])
        
            reply_markup = InlineKeyboardMarkup(keyboard)

            if update.callback_query:
                await update.callback_query.answer()
                # При редактировании явно обновляем и текст, и клавиатуру
                # Обновляем текст и клавиатуру одновременно, чтобы размер не менялся
                await update.callback_query.edit_message_text(
                    message,
                    reply_markup=reply_markup,
                    parse_mode='HTML'
                )
            else:
                await update.message.reply_text(
                    message,
                    reply_markup=reply_markup,
                    parse_mode='HTML'
                )

    except Exception as e:
        logger.error("Ошибка при получении календаря тренера: %s", e, exc_info=True)
        error_msg = "❌ Ошибка при загрузке календаря"
        if update.callback_query:
            await update.callback_query.answer(error_msg)
        else:
            await update.message.reply_text(error_msg)


async def handle_calendar_navigation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик навигации по календарю"""
    query = update.callback_query
    await query.answer()
    
    # Парсим callback_data: calendar_YYYY_MM
    parts = query.data.split("_")
    if len(parts) == 3:
        try:
            year = int(parts[1])
            month = int(parts[2])
            await show_coach_calendar(update, context, month=month, year=year)
        except ValueError:
            await query.answer("❌ Ошибка при обработке запроса")
    else:
        await query.answer("❌ Неверный формат запроса")


async def handle_calendar_empty_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик клика по пустой кнопке календаря (неактивная)"""
    query = update.callback_query
    await query.answer()  # Просто отвечаем, ничего не делаем


async def handle_calendar_date_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик клика по дате в календаре - показывает тренировки на эту дату"""
    query = update.callback_query
    user_id = update.effective_user.id
    logger.info("Клик по дате в календаре: %s от пользователя %s", query.data, user_id)

    try:
        with get_db_session() as session:
            parts = query.data.split("_")
            if len(parts) != 5:
                logger.error("Неверный формат callback_data: %s, частей: %s", query.data, len(parts))
                await query.answer("❌ Ошибка при обработке даты")
                return

            year = int(parts[2])
            month = int(parts[3])
            day = int(parts[4])

            selected_date = datetime(year, month, day).date()
            date_start = datetime.combine(selected_date, datetime.min.time())
            date_end = datetime.combine(selected_date, datetime.max.time())

            user = get_user_by_telegram_id(session, user_id)
            if not user or get_user_role(user) != 'coach':
                await query.answer("❌ У вас нет доступа")
                return

            if isinstance(user, Coach):
                user = (
                    session.query(Coach)
                    .options(joinedload(Coach.sport_type_rel))
                    .filter_by(id=user.id)
                    .first()
                )
                if not user:
                    await query.answer("❌ Пользователь не найден")
                    return

            now = now_moscow()
            from services.attendance_training_flow import build_attendance_slots_for_day
            from utils.age_groups import format_age_group_label

            slot_rows, _virtual_slots = build_attendance_slots_for_day(session, user, selected_date)
            db_training_ids = [s.training_id for s in slot_rows if (not s.is_virtual and s.training_id)]
            db_trainings = (
                session.query(Training)
                .filter(Training.id.in_(db_training_ids))
                .all()
                if db_training_ids
                else []
            )
            training_by_id = {t.id: t for t in db_trainings}

            visible_slots = []
            for slot in slot_rows:
                if slot.is_individual_format:
                    training = training_by_id.get(slot.training_id)
                    if not training:
                        continue
                    if not individual_slot_has_links(session, training, coach_id=user.id):
                        continue
                visible_slots.append(slot)

            date_str = selected_date.strftime("%d.%m.%Y")
            message = f"<b>📅 {date_str}</b>\n\n"

            if visible_slots:
                rendered_slots = 0
                for slot in visible_slots:
                    training = training_by_id.get(slot.training_id)
                    time_str = slot.training_datetime.strftime("%H:%M")
                    is_individual_slot = bool(slot.is_individual_format)
                    training_date_only = slot.training_datetime.date()

                    athlete_lines = []
                    athlete_count = 0
                    if is_individual_slot:
                        subs_q = (
                            session.query(Subscription)
                            .join(Athlete, Subscription.athlete_id == Athlete.id)
                            .filter(
                                Subscription.sport_type == slot.sport_type,
                                func.date(Subscription.start_date) <= training_date_only,
                                func.date(Subscription.end_date) >= training_date_only,
                            )
                        )
                        slot_key = slot.training_datetime.strftime("%Y-%m-%d %H:%M")
                        subs_q = subs_q.filter(
                            Subscription.subscription_type == "individual",
                            func.strftime("%Y-%m-%d %H:%M", Subscription.start_date) == slot_key,
                        )
                        subs = subs_q.all()
                        if subs:
                            athlete_count = len(subs)
                            athlete_ids = [sub.athlete_id for sub in subs]
                            athletes_map = {
                                a.id: a
                                for a in session.query(Athlete).filter(Athlete.id.in_(athlete_ids)).all()
                            }
                            sub_ids = [s.id for s in subs]
                            slot_training_ids = individual_slot_training_ids(session, training)
                            att_by_sub = {
                                a.subscription_id: a
                                for a in session.query(Attendance).filter(
                                    Attendance.training_id.in_(slot_training_ids),
                                    Attendance.subscription_id.in_(sub_ids),
                                ).all()
                            }
                            for sub in subs[:10]:
                                ath = athletes_map.get(sub.athlete_id)
                                if not ath:
                                    continue
                                att = att_by_sub.get(sub.id)
                                status_icon = attendance_icon_for_slot(
                                    att, slot.training_datetime, now=now
                                )
                                age_suffix = ""
                                age_suffix = format_athlete_age_suffix(
                                    getattr(ath, "age_group", None)
                                )
                                athlete_lines.append(
                                    f"    {status_icon} {html.escape(_surname_initials(ath.full_name))}"
                                    f"{html.escape(age_suffix)}\n"
                                )
                            if len(subs) > 10:
                                athlete_lines.append(f"    ... и еще {len(subs) - 10}\n")
                        else:
                            # Fallback: если абонемент не найден, но есть Attendance по слоту.
                            slot_training_ids = individual_slot_training_ids(session, training)
                            slot_atts = (
                                session.query(Attendance)
                                .filter(Attendance.training_id.in_(slot_training_ids))
                                .order_by(Attendance.created_at.asc())
                                .all()
                            )
                            if slot_atts:
                                by_athlete = {}
                                for att in slot_atts:
                                    by_athlete[att.athlete_id] = att
                                fallback_atts = list(by_athlete.values())
                                athlete_count = len(fallback_atts)
                                athlete_ids = [att.athlete_id for att in fallback_atts]
                                athletes_map = {
                                    a.id: a
                                    for a in session.query(Athlete).filter(Athlete.id.in_(athlete_ids)).all()
                                }
                                for att in fallback_atts[:10]:
                                    ath = athletes_map.get(att.athlete_id)
                                    if not ath:
                                        continue
                                    status_icon = attendance_icon_for_slot(
                                        att, slot.training_datetime, now=now
                                    )
                                    age_suffix = format_athlete_age_suffix(
                                        getattr(ath, "age_group", None)
                                    )
                                    athlete_lines.append(
                                        f"    {status_icon} {html.escape(_surname_initials(ath.full_name))}"
                                        f"{html.escape(age_suffix)}\n"
                                    )
                                if len(fallback_atts) > 10:
                                    athlete_lines.append(
                                        f"    ... и еще {len(fallback_atts) - 10}\n"
                                    )
                    else:
                        sub_ath_rows = (
                            session.query(Subscription, Athlete)
                            .join(Athlete, Subscription.athlete_id == Athlete.id)
                            .filter(
                                Subscription.is_active == True,
                                Subscription.sport_type == slot.sport_type,
                                func.date(Subscription.start_date) <= selected_date,
                                func.date(Subscription.end_date) >= selected_date,
                                Athlete.age_group == slot.age_group,
                            )
                            .filter(
                                or_(
                                    Subscription.subscription_type.is_(None),
                                    Subscription.subscription_type != "individual",
                                )
                            )
                        )
                        pair_list = sub_ath_rows.all()
                        athlete_count = len(pair_list)
                        if pair_list:
                            aid_list = list({a.id for _s, a in pair_list})
                            sub_ids = list({s.id for s, _a in pair_list})
                            if training:
                                slot_training_ids = individual_slot_training_ids(session, training)
                                atts = (
                                    session.query(Attendance)
                                    .filter(
                                        Attendance.training_id.in_(slot_training_ids),
                                        Attendance.subscription_id.in_(sub_ids),
                                        Attendance.athlete_id.in_(aid_list),
                                    )
                                    .all()
                                )
                            else:
                                atts = (
                                    session.query(Attendance)
                                    .join(Training, Attendance.training_id == Training.id)
                                    .filter(
                                        Training.coach_id == user.id,
                                        Training.sport_type == slot.sport_type,
                                        Training.age_group == slot.age_group,
                                        func.date(Training.training_date) == selected_date,
                                        Attendance.subscription_id.in_(sub_ids),
                                        Attendance.athlete_id.in_(aid_list),
                                    )
                                    .all()
                                )
                            att_by_pair = {}
                            for att in atts:
                                key = (att.athlete_id, att.subscription_id)
                                if key not in att_by_pair:
                                    att_by_pair[key] = att

                            for subscription, athlete in pair_list[:10]:
                                attendance = att_by_pair.get((athlete.id, subscription.id))
                                status_icon = attendance_icon_for_slot(
                                    attendance, slot.training_datetime, now=now
                                )
                                athlete_lines.append(
                                    f"    {status_icon} {html.escape(_surname_initials(athlete.full_name))}\n"
                                )
                            if len(pair_list) > 10:
                                athlete_lines.append(f"    ... и еще {len(pair_list) - 10}\n")

                    rendered_slots += 1
                    if rendered_slots == 1:
                        message += "<b>Тренировки со спортсменами:</b>\n\n"
                    message += format_coach_calendar_slot_bullet(
                        time_str,
                        slot.sport_type,
                        age_group=slot.age_group,
                        is_individual=is_individual_slot,
                    )
                    message += f"  <b>Спортсменов: {athlete_count}</b>\n"
                    for line in athlete_lines:
                        message += line
                    message += "\n"

                message = message.replace(
                    "<b>📅 " + date_str + "</b>\n\n",
                    f"<b>📅 {date_str}</b>\n\n<b>Тренировок: {rendered_slots}</b>\n\n",
                    1,
                )
            else:
                message += "На эту дату тренировок не запланировано.\n\n"

            sport_type_name = coach_sport_type_name(user)
            is_group_training_day = (
                selected_date.weekday() in _coach_scheduled_weekdays(sport_type_name)
            )

            keyboard = []
            if is_group_training_day:
                keyboard.append(
                    [
                        InlineKeyboardButton(
                            f"{CAL_BOOK_ADD_ICON} Групповая",
                            callback_data=f"cal_grp_book_{year}_{month}_{day}",
                        ),
                        InlineKeyboardButton(
                            f"{CAL_BOOK_ADD_ICON} Разовая",
                            callback_data=f"cal_sgl_book_{year}_{month}_{day}",
                        ),
                    ]
                )
            keyboard.append(
                [
                    InlineKeyboardButton(
                        f"{CAL_BOOK_ADD_ICON} Индивидуальная",
                        callback_data=f"cal_ind_book_{year}_{month}_{day}",
                    ),
                ]
            )
            keyboard.append(
                [
                    InlineKeyboardButton(
                        "🔙 К календарю", callback_data=f"calendar_{year}_{month}"
                    )
                ],
            )
            reply_markup = InlineKeyboardMarkup(keyboard)

            await query.answer()
            await query.edit_message_text(
                truncate_for_telegram_message(message),
                reply_markup=reply_markup,
                parse_mode='HTML'
            )

    except Exception as e:
        logger.error("Ошибка при обработке клика по дате календаря: %s", e, exc_info=True)
        try:
            await query.answer("❌ Ошибка при загрузке данных")
        except Exception:
            pass


CAL_IND_ATHLETE_PAGE_SIZE = 12


def _individual_booking_schema_error(session) -> Optional[str]:
    """None — схема OK; иначе текст ошибки для тренера."""
    from handlers.card_handlers import (
        _has_legacy_unique_athlete_constraint,
        _supports_individual_subscription_type,
        _supports_multi_individual_bookings,
    )

    if not _supports_individual_subscription_type(session):
        return (
            "❌ Схема БД не поддерживает индивидуальные абонементы.\n"
            "Выполните миграцию БД и перезапустите бота."
        )
    if not _supports_multi_individual_bookings(session):
        if _has_legacy_unique_athlete_constraint(session):
            return (
                "❌ Схема БД в legacy-режиме (1 спортсмен = 1 абонемент).\n"
                "Выполните миграцию БД."
            )
        return (
            "❌ Схема БД не поддерживает несколько индивидуальных броней.\n"
            "Выполните миграцию БД и перезапустите бота."
        )
    return None


def _build_cal_individual_time_keyboard(
    session,
    coach_id: int,
    sport_type: str,
    year: int,
    month: int,
    day: int,
) -> Optional[InlineKeyboardMarkup]:
    """Свободные старты individual в день (из календаря тренера)."""
    d = date(year, month, day)
    starts = iter_allowed_individual_starts(
        session, coach_id, sport_type, d, now_cutoff=now_moscow()
    )
    if not starts:
        return None
    rows = []
    row = []
    for st in starts:
        compact = db_utils_pkg.training_datetime_compact(st)
        row.append(
            InlineKeyboardButton(
                st.strftime("%H:%M"),
                callback_data=f"cal_ind_ts_{compact}",
            )
        )
        if len(row) >= 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append(
        [
            InlineKeyboardButton(
                "🔙 К дню",
                callback_data=f"cal_date_{year}_{month}_{day}",
            )
        ]
    )
    return InlineKeyboardMarkup(rows)


def _build_cal_individual_athlete_keyboard(
    athletes: List[Athlete],
    slot_compact: str,
    year: int,
    month: int,
    day: int,
    *,
    page: int = 0,
) -> InlineKeyboardMarkup:
    total = len(athletes)
    page_size = CAL_IND_ATHLETE_PAGE_SIZE
    max_page = max(0, (total - 1) // page_size) if total else 0
    page = max(0, min(page, max_page))
    start = page * page_size
    chunk = athletes[start : start + page_size]

    rows = []
    for athlete in chunk:
        label = _surname_initials(athlete.full_name or "—")
        if len(label) > 36:
            label = label[:34] + ".."
        rows.append(
            [
                InlineKeyboardButton(
                    label,
                    callback_data=f"cal_ind_a_{athlete.id}_{slot_compact}",
                )
            ]
        )

    nav = []
    if total > page_size:
        if page > 0:
            nav.append(
                InlineKeyboardButton(
                    "◀️",
                    callback_data=f"cal_ind_pg_{page - 1}_{slot_compact}",
                )
            )
        nav.append(
            InlineKeyboardButton(
                f"{page + 1}/{max_page + 1}",
                callback_data="cal_ind_pg_info",
            )
        )
        if page < max_page:
            nav.append(
                InlineKeyboardButton(
                    "▶️",
                    callback_data=f"cal_ind_pg_{page + 1}_{slot_compact}",
                )
            )
        if nav:
            rows.append(nav)

    rows.append(
        [
            InlineKeyboardButton(
                "🔙 К времени",
                callback_data=f"cal_ind_book_{year}_{month}_{day}",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                "🔙 К календарю",
                callback_data=f"calendar_{year}_{month}",
            )
        ]
    )
    return InlineKeyboardMarkup(rows)


async def _render_cal_individual_athlete_picker(
    query,
    session,
    user: Coach,
    start_date: datetime,
    *,
    page: int = 0,
) -> None:
    athletes, _header = load_athletes_for_list(session, user)
    athletes.sort(key=lambda a: (a.full_name or "").strip().lower())
    slot_compact = db_utils_pkg.training_datetime_compact(start_date)
    y, m, d = start_date.year, start_date.month, start_date.day

    if not athletes:
        await query.edit_message_text(
            f"📅 <b>{start_date.strftime('%d.%m.%Y %H:%M')}</b>\n\n"
            "У вас пока нет спортсменов для записи.\n"
            "Добавьте спортсмена через меню «👥 Добавить спортсмена».",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🔙 К дню",
                            callback_data=f"cal_date_{y}_{m}_{d}",
                        )
                    ]
                ]
            ),
            parse_mode="HTML",
        )
        return

    markup = _build_cal_individual_athlete_keyboard(
        athletes,
        slot_compact,
        y,
        m,
        d,
        page=page,
    )
    await query.edit_message_text(
        f"{CAL_BOOK_ADD_ICON} <b>Индивидуальная тренировка</b>\n\n"
        f"📅 {start_date.strftime('%d.%m.%Y %H:%M')}\n\n"
        "Выберите спортсмена:",
        reply_markup=markup,
        parse_mode="HTML",
    )


async def handle_calendar_individual_book_start(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    """Календарь: выбор времени для новой individual-записи (cal_ind_book_Y_M_D)."""
    query = update.callback_query
    await query.answer()
    m = re.match(r"^cal_ind_book_(\d{4})_(\d{1,2})_(\d{1,2})$", (query.data or "").strip())
    if not m:
        await query.edit_message_text("❌ Некорректный запрос.")
        return

    year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    user_id = query.from_user.id

    try:
        with get_db_session() as session:
            user = get_user_by_telegram_id(session, user_id)
            if not user or get_user_role(user) != "coach":
                await query.edit_message_text("❌ Доступно только тренерам")
                return
            if isinstance(user, Coach):
                user = (
                    session.query(Coach)
                    .options(joinedload(Coach.sport_type_rel))
                    .filter_by(id=user.id)
                    .first()
                )
            if not user:
                await query.edit_message_text("❌ Пользователь не найден")
                return

            schema_err = _individual_booking_schema_error(session)
            if schema_err:
                await query.edit_message_text(schema_err)
                return

            sport_type = coach_sport_type_name(user)
            if not sport_type:
                await query.edit_message_text(
                    "❌ У вас не указан вид спорта. Обратитесь к администратору."
                )
                return

            noon = datetime(year, month, day, 12, 0, 0)
            if db_utils_pkg.is_training_in_global_freeze(session, noon):
                await query.edit_message_text(
                    "❌ Этот день в периоде массовой заморозки. Выберите другую дату."
                )
                return

            time_kb = _build_cal_individual_time_keyboard(
                session, user.id, sport_type, year, month, day
            )
            if not time_kb:
                await query.edit_message_text(
                    f"📅 <b>{day:02d}.{month:02d}.{year}</b>\n\n"
                    "Нет свободных слотов на этот день "
                    "(все заняты или время уже прошло).",
                    reply_markup=InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton(
                                    "🔙 К дню",
                                    callback_data=f"cal_date_{year}_{month}_{day}",
                                )
                            ]
                        ]
                    ),
                    parse_mode="HTML",
                )
                return

            await query.edit_message_text(
                f"{CAL_BOOK_ADD_ICON} <b>Индивидуальная тренировка</b>\n\n"
                f"📅 {day:02d}.{month:02d}.{year}\n\n"
                "Выберите время начала:",
                reply_markup=time_kb,
                parse_mode="HTML",
            )
    except Exception as e:
        logger.error("cal_ind_book_start: %s", e, exc_info=True)
        await query.edit_message_text("❌ Ошибка при загрузке слотов")


async def handle_calendar_individual_time_pick(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    """Календарь: время выбрано → список спортсменов (cal_ind_ts_YYYYMMDDHHMM)."""
    query = update.callback_query
    await query.answer()
    m = re.match(r"^cal_ind_ts_(\d{12})$", (query.data or "").strip())
    if not m:
        await query.edit_message_text("❌ Некорректная кнопка времени.")
        return

    start_date = db_utils_pkg.parse_training_datetime_compact(m.group(1))
    if not start_date:
        await query.edit_message_text("❌ Некорректное время.")
        return

    user_id = query.from_user.id
    now = now_moscow()
    if now > start_date + ACTIVATION_GRACE_AFTER_START:
        grace_min = int(ACTIVATION_GRACE_AFTER_START.total_seconds() // 60)
        await query.edit_message_text(
            f"❌ Время для записи на этот слот истекло (запас {grace_min} мин)."
        )
        return

    try:
        with get_db_session() as session:
            user = get_user_by_telegram_id(session, user_id)
            if not user or get_user_role(user) != "coach":
                await query.edit_message_text("❌ Доступно только тренерам")
                return
            if isinstance(user, Coach):
                user = (
                    session.query(Coach)
                    .options(joinedload(Coach.sport_type_rel))
                    .filter_by(id=user.id)
                    .first()
                )
            if not user:
                await query.edit_message_text("❌ Пользователь не найден")
                return

            sport_type = coach_sport_type_name(user)
            if not sport_type:
                await query.edit_message_text("❌ У вас не указан вид спорта.")
                return

            if db_utils_pkg.is_training_in_global_freeze(session, start_date):
                await query.edit_message_text(
                    "❌ Выбранное время в периоде массовой заморозки."
                )
                return
            if individual_slot_conflicts(session, user.id, sport_type, start_date):
                await query.edit_message_text(
                    "❌ Слот занят или пересекается с другой тренировкой. "
                    "Выберите другое время.",
                    reply_markup=_build_cal_individual_time_keyboard(
                        session,
                        user.id,
                        sport_type,
                        start_date.year,
                        start_date.month,
                        start_date.day,
                    ),
                )
                return

            await _render_cal_individual_athlete_picker(
                query, session, user, start_date, page=0
            )
    except Exception as e:
        logger.error("cal_ind_ts: %s", e, exc_info=True)
        await query.edit_message_text("❌ Ошибка при выборе времени")


async def handle_calendar_individual_athlete_page(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    """Пагинация списка спортсменов при записи на individual."""
    query = update.callback_query
    if (query.data or "").strip() == "cal_ind_pg_info":
        await query.answer("Листайте спортсменов кнопками ◀️ и ▶️.")
        return

    await query.answer()
    m = re.match(r"^cal_ind_pg_(\d+)_(\d{12})$", (query.data or "").strip())
    if not m:
        return

    page = int(m.group(1))
    start_date = db_utils_pkg.parse_training_datetime_compact(m.group(2))
    if not start_date:
        return

    try:
        with get_db_session() as session:
            user = get_user_by_telegram_id(session, query.from_user.id)
            if not user or get_user_role(user) != "coach":
                await query.edit_message_text("❌ Доступно только тренерам")
                return
            if isinstance(user, Coach):
                user = session.query(Coach).filter_by(id=user.id).first()
            if not user:
                return
            await _render_cal_individual_athlete_picker(
                query, session, user, start_date, page=page
            )
    except Exception as e:
        logger.error("cal_ind_pg: %s", e, exc_info=True)


async def handle_calendar_individual_athlete_pick(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    """Календарь: спортсмен выбран → создать и активировать individual (cal_ind_a_{id}_{ts})."""
    query = update.callback_query
    await query.answer()
    m = re.match(r"^cal_ind_a_(\d+)_(\d{12})$", (query.data or "").strip())
    if not m:
        await query.edit_message_text("❌ Некорректный выбор спортсмена.")
        return

    athlete_id = int(m.group(1))
    start_date = db_utils_pkg.parse_training_datetime_compact(m.group(2))
    if not start_date:
        await query.edit_message_text("❌ Некорректное время.")
        return

    try:
        with get_db_session() as session:
            user = get_user_by_telegram_id(session, query.from_user.id)
            if not user or get_user_role(user) not in ("coach", "admin"):
                await query.edit_message_text("❌ У вас нет доступа")
                return

            athlete = session.query(Athlete).filter_by(id=athlete_id).first()
            if not athlete:
                await query.edit_message_text("❌ Спортсмен не найден")
                return
            if isinstance(user, Coach) and athlete.created_by != user.id:
                await query.edit_message_text("❌ Это не ваш спортсмен")
                return

            coach_id = user.id if isinstance(user, Coach) else athlete.created_by
            sport_type = coach_sport_type_name(user) if isinstance(user, Coach) else athlete.sport_type
            if not sport_type:
                sport_type = (athlete.sport_type or "").strip()
            if not sport_type or not coach_id:
                await query.edit_message_text("❌ Не удалось определить вид спорта или тренера.")
                return

            schema_err = _individual_booking_schema_error(session)
            if schema_err:
                await query.edit_message_text(schema_err)
                return

            now = now_moscow()
            if now > start_date + ACTIVATION_GRACE_AFTER_START:
                await query.edit_message_text("❌ Время для записи на этот слот истекло.")
                return
            if db_utils_pkg.is_training_in_global_freeze(session, start_date):
                await query.edit_message_text("❌ Время в периоде массовой заморозки.")
                return
            if individual_slot_conflicts(session, coach_id, sport_type, start_date):
                await query.edit_message_text(
                    "❌ Слот уже занят. Выберите другое время.",
                    reply_markup=_build_cal_individual_time_keyboard(
                        session,
                        coach_id,
                        sport_type,
                        start_date.year,
                        start_date.month,
                        start_date.day,
                    ),
                )
                return

            dup = (
                session.query(Subscription.id)
                .filter(
                    Subscription.athlete_id == athlete.id,
                    Subscription.subscription_type == "individual",
                    Subscription.is_active.is_(True),
                    Subscription.start_date == start_date,
                )
                .first()
            )
            if dup:
                await query.edit_message_text(
                    "❌ У спортсмена уже есть запись на это время.",
                    reply_markup=_build_cal_individual_time_keyboard(
                        session,
                        coach_id,
                        sport_type,
                        start_date.year,
                        start_date.month,
                        start_date.day,
                    ),
                )
                return

            from handlers.card_handlers import (
                _finalize_subscription_activation,
                prepare_individual_subscription_for_activation,
            )

            subscription = prepare_individual_subscription_for_activation(
                session,
                athlete,
                sport_type,
                responsible_coach_id=coach_id,
            )
            session.flush()
            await _finalize_subscription_activation(
                update, context, query, session, subscription, athlete, start_date
            )
    except Exception as e:
        logger.error("cal_ind_a: %s", e, exc_info=True)
        await query.edit_message_text("❌ Ошибка при записи на индивидуальную тренировку")


CAL_GROUP_AGE_CODE = {
    "children": "c",
    "middle": "m",
    "adults": "a",
}
CAL_GROUP_AGE_FROM_CODE = {v: k for k, v in CAL_GROUP_AGE_CODE.items()}

CAL_GROUP_BOOK_KINDS = {
    "grp": {
        "title": "Групповая тренировка",
        "subscription_type": "monthly",
    },
    "sgl": {
        "title": "Разовая тренировка",
        "subscription_type": "single",
    },
}


def _cal_group_slot_token(start_date: datetime, age_group: str) -> str:
    ag = normalize_age_group(age_group) or age_group
    code = CAL_GROUP_AGE_CODE.get(ag, "a")
    return f"{db_utils_pkg.training_datetime_compact(start_date)}_{code}"


def _parse_cal_group_slot_token(token: str) -> Tuple[Optional[datetime], Optional[str]]:
    m = re.match(r"^(\d{12})_([cma])$", (token or "").strip())
    if not m:
        return None, None
    start_date = db_utils_pkg.parse_training_datetime_compact(m.group(1))
    age_group = CAL_GROUP_AGE_FROM_CODE.get(m.group(2))
    if not start_date or not age_group:
        return None, None
    return start_date, age_group


def _calendar_load_coach(session, user_id: int) -> Tuple[Optional[Coach], Optional[str]]:
    user = get_user_by_telegram_id(session, user_id)
    if not user or get_user_role(user) != "coach":
        return None, "❌ Доступно только тренерам"
    if isinstance(user, Coach):
        user = (
            session.query(Coach)
            .options(joinedload(Coach.sport_type_rel))
            .filter_by(id=user.id)
            .first()
        )
    if not user:
        return None, "❌ Пользователь не найден"
    return user, None


def _iter_group_slots_for_calendar_day(
    session,
    coach: Coach,
    sport_type: str,
    day: date,
) -> List:
    from services.attendance_training_flow import build_attendance_slots_for_day

    rows, _virtual = build_attendance_slots_for_day(session, coach, day)
    now = now_moscow()
    sport = (sport_type or "").strip()
    out = []
    for slot in rows:
        if slot.is_individual_format:
            continue
        if (slot.sport_type or "").strip() != sport:
            continue
        if now > slot.training_datetime + ACTIVATION_GRACE_AFTER_START:
            continue
        out.append(slot)
    out.sort(key=lambda s: s.training_datetime)
    return out


def _build_cal_group_time_keyboard(
    session,
    coach: Coach,
    sport_type: str,
    year: int,
    month: int,
    day: int,
    *,
    kind: str,
) -> Optional[InlineKeyboardMarkup]:
    slots = _iter_group_slots_for_calendar_day(
        session, coach, sport_type, date(year, month, day)
    )
    if not slots:
        return None
    rows = []
    row = []
    for slot in slots:
        ag_label = format_age_group_label(slot.age_group, short=True)
        label = f"{slot.training_datetime.strftime('%H:%M')} {ag_label[:3]}"
        token = _cal_group_slot_token(slot.training_datetime, slot.age_group)
        row.append(
            InlineKeyboardButton(
                label,
                callback_data=f"cal_{kind}_ts_{token}",
            )
        )
        if len(row) >= 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append(
        [
            InlineKeyboardButton(
                "🔙 К дню",
                callback_data=f"cal_date_{year}_{month}_{day}",
            )
        ]
    )
    return InlineKeyboardMarkup(rows)


def _athletes_eligible_for_group_booking(
    session,
    coach: Coach,
    *,
    age_group: str,
    sport_type: str,
) -> List[Athlete]:
    athletes, _header = load_athletes_for_list(session, coach)
    ag_norm = normalize_age_group(age_group) or age_group
    dk = discipline_key_for(sport_type, format="group")
    eligible = []
    for athlete in athletes:
        if normalize_age_group(athlete.age_group) != ag_norm:
            continue
        active = (
            session.query(Subscription.id)
            .filter_by(athlete_id=athlete.id, discipline_key=dk, is_active=True)
            .first()
        )
        if active:
            continue
        eligible.append(athlete)
    eligible.sort(key=lambda a: (a.full_name or "").strip().lower())
    return eligible


def _build_cal_group_athlete_keyboard(
    athletes: List[Athlete],
    slot_token: str,
    year: int,
    month: int,
    day: int,
    *,
    kind: str,
    page: int = 0,
) -> InlineKeyboardMarkup:
    total = len(athletes)
    page_size = CAL_IND_ATHLETE_PAGE_SIZE
    max_page = max(0, (total - 1) // page_size) if total else 0
    page = max(0, min(page, max_page))
    start = page * page_size
    chunk = athletes[start : start + page_size]

    rows = []
    for athlete in chunk:
        label = _surname_initials(athlete.full_name or "—")
        if len(label) > 36:
            label = label[:34] + ".."
        rows.append(
            [
                InlineKeyboardButton(
                    label,
                    callback_data=f"cal_{kind}_a_{athlete.id}_{slot_token}",
                )
            ]
        )

    nav = []
    if total > page_size:
        if page > 0:
            nav.append(
                InlineKeyboardButton(
                    "◀️",
                    callback_data=f"cal_{kind}_pg_{page - 1}_{slot_token}",
                )
            )
        nav.append(
            InlineKeyboardButton(
                f"{page + 1}/{max_page + 1}",
                callback_data=f"cal_{kind}_pg_info",
            )
        )
        if page < max_page:
            nav.append(
                InlineKeyboardButton(
                    "▶️",
                    callback_data=f"cal_{kind}_pg_{page + 1}_{slot_token}",
                )
            )
        if nav:
            rows.append(nav)

    rows.append(
        [
            InlineKeyboardButton(
                "🔙 К времени",
                callback_data=f"cal_{kind}_book_{year}_{month}_{day}",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                "🔙 К календарю",
                callback_data=f"calendar_{year}_{month}",
            )
        ]
    )
    return InlineKeyboardMarkup(rows)


async def _render_cal_group_athlete_picker(
    query,
    session,
    user: Coach,
    start_date: datetime,
    age_group: str,
    *,
    kind: str,
    page: int = 0,
) -> None:
    cfg = CAL_GROUP_BOOK_KINDS[kind]
    sport_type = coach_sport_type_name(user) or ""
    athletes = _athletes_eligible_for_group_booking(
        session,
        user,
        age_group=age_group,
        sport_type=sport_type,
    )
    slot_token = _cal_group_slot_token(start_date, age_group)
    y, m, d = start_date.year, start_date.month, start_date.day
    ag_label = format_age_group_label(age_group, short=True)

    if not athletes:
        await query.edit_message_text(
            f"📅 <b>{start_date.strftime('%d.%m.%Y %H:%M')}</b> · {ag_label}\n\n"
            "Нет подходящих спортсменов для записи "
            "(возрастная группа или уже есть активный групповой абонемент).",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🔙 К времени",
                            callback_data=f"cal_{kind}_book_{y}_{m}_{d}",
                        )
                    ]
                ]
            ),
            parse_mode="HTML",
        )
        return

    markup = _build_cal_group_athlete_keyboard(
        athletes,
        slot_token,
        y,
        m,
        d,
        kind=kind,
        page=page,
    )
    await query.edit_message_text(
        f"{CAL_BOOK_ADD_ICON} <b>{cfg['title']}</b>\n\n"
        f"📅 {start_date.strftime('%d.%m.%Y %H:%M')} · {ag_label}\n\n"
        "Выберите спортсмена:",
        reply_markup=markup,
        parse_mode="HTML",
    )


async def handle_calendar_group_book_start(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    """Календарь: выбор группового/разового слота (cal_grp_book_ / cal_sgl_book_)."""
    query = update.callback_query
    await query.answer()
    m = re.match(
        r"^cal_(grp|sgl)_book_(\d{4})_(\d{1,2})_(\d{1,2})$",
        (query.data or "").strip(),
    )
    if not m:
        await query.edit_message_text("❌ Некорректный запрос.")
        return

    kind = m.group(1)
    year, month, day = int(m.group(2)), int(m.group(3)), int(m.group(4))
    cfg = CAL_GROUP_BOOK_KINDS.get(kind)
    if not cfg:
        await query.edit_message_text("❌ Некорректный тип записи.")
        return

    try:
        with get_db_session() as session:
            user, err = _calendar_load_coach(session, query.from_user.id)
            if err:
                await query.edit_message_text(err)
                return

            sport_type = coach_sport_type_name(user)
            if not sport_type:
                await query.edit_message_text(
                    "❌ У вас не указан вид спорта. Обратитесь к администратору."
                )
                return

            if date(year, month, day).weekday() not in _coach_scheduled_weekdays(sport_type):
                await query.edit_message_text(
                    f"📅 <b>{day:02d}.{month:02d}.{year}</b>\n\n"
                    "Групповые и разовые тренировки доступны только в дни "
                    "занятий по расписанию вашего вида спорта.",
                    reply_markup=InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton(
                                    "🔙 К дню",
                                    callback_data=f"cal_date_{year}_{month}_{day}",
                                )
                            ]
                        ]
                    ),
                    parse_mode="HTML",
                )
                return

            noon = datetime(year, month, day, 12, 0, 0)
            if db_utils_pkg.is_training_in_global_freeze(session, noon):
                await query.edit_message_text(
                    "❌ Этот день в периоде массовой заморозки. Выберите другую дату."
                )
                return

            time_kb = _build_cal_group_time_keyboard(
                session, user, sport_type, year, month, day, kind=kind
            )
            if not time_kb:
                await query.edit_message_text(
                    f"📅 <b>{day:02d}.{month:02d}.{year}</b>\n\n"
                    "Нет доступных групповых слотов на этот день "
                    "(нет занятий по расписанию или время уже прошло).",
                    reply_markup=InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton(
                                    "🔙 К дню",
                                    callback_data=f"cal_date_{year}_{month}_{day}",
                                )
                            ]
                        ]
                    ),
                    parse_mode="HTML",
                )
                return

            await query.edit_message_text(
                f"{CAL_BOOK_ADD_ICON} <b>{cfg['title']}</b>\n\n"
                f"📅 {day:02d}.{month:02d}.{year}\n\n"
                "Выберите групповой слот:",
                reply_markup=time_kb,
                parse_mode="HTML",
            )
    except Exception as e:
        logger.error("cal_%s_book_start: %s", kind, e, exc_info=True)
        await query.edit_message_text("❌ Ошибка при загрузке слотов")


async def handle_calendar_group_time_pick(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    """Календарь: групповой слот выбран → список спортсменов."""
    query = update.callback_query
    await query.answer()
    m = re.match(r"^cal_(grp|sgl)_ts_(\d{12}_[cma])$", (query.data or "").strip())
    if not m:
        await query.edit_message_text("❌ Некорректная кнопка слота.")
        return

    kind = m.group(1)
    start_date, age_group = _parse_cal_group_slot_token(m.group(2))
    if not start_date or not age_group:
        await query.edit_message_text("❌ Некорректный слот.")
        return

    now = now_moscow()
    if now > start_date + ACTIVATION_GRACE_AFTER_START:
        grace_min = int(ACTIVATION_GRACE_AFTER_START.total_seconds() // 60)
        await query.edit_message_text(
            f"❌ Время для записи на этот слот истекло (запас {grace_min} мин)."
        )
        return

    try:
        with get_db_session() as session:
            user, err = _calendar_load_coach(session, query.from_user.id)
            if err:
                await query.edit_message_text(err)
                return

            sport_type = coach_sport_type_name(user)
            if not sport_type:
                await query.edit_message_text("❌ У вас не указан вид спорта.")
                return

            if db_utils_pkg.is_training_in_global_freeze(session, start_date):
                await query.edit_message_text(
                    "❌ Выбранное время в периоде массовой заморозки."
                )
                return

            slots = _iter_group_slots_for_calendar_day(
                session, user, sport_type, start_date.date()
            )
            slot_ok = any(
                s.training_datetime == start_date
                and normalize_age_group(s.age_group) == normalize_age_group(age_group)
                for s in slots
            )
            if not slot_ok:
                await query.edit_message_text(
                    "❌ Слот недоступен. Выберите другое время.",
                    reply_markup=_build_cal_group_time_keyboard(
                        session,
                        user,
                        sport_type,
                        start_date.year,
                        start_date.month,
                        start_date.day,
                        kind=kind,
                    ),
                )
                return

            await _render_cal_group_athlete_picker(
                query,
                session,
                user,
                start_date,
                age_group,
                kind=kind,
                page=0,
            )
    except Exception as e:
        logger.error("cal_%s_ts: %s", kind, e, exc_info=True)
        await query.edit_message_text("❌ Ошибка при выборе слота")


async def handle_calendar_group_athlete_page(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    """Пагинация списка спортсменов при записи на групповую/разовую."""
    query = update.callback_query
    if (query.data or "").strip() in ("cal_grp_pg_info", "cal_sgl_pg_info"):
        await query.answer("Листайте спортсменов кнопками ◀️ и ▶️.")
        return

    await query.answer()
    m = re.match(r"^cal_(grp|sgl)_pg_(\d+)_(\d{12}_[cma])$", (query.data or "").strip())
    if not m:
        return

    kind = m.group(1)
    page = int(m.group(2))
    start_date, age_group = _parse_cal_group_slot_token(m.group(3))
    if not start_date or not age_group:
        return

    try:
        with get_db_session() as session:
            user, err = _calendar_load_coach(session, query.from_user.id)
            if err:
                await query.edit_message_text(err)
                return
            await _render_cal_group_athlete_picker(
                query,
                session,
                user,
                start_date,
                age_group,
                kind=kind,
                page=page,
            )
    except Exception as e:
        logger.error("cal_%s_pg: %s", kind, e, exc_info=True)


async def handle_calendar_group_athlete_pick(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    """Календарь: спортсмен выбран → создать и активировать monthly/single."""
    query = update.callback_query
    await query.answer()
    m = re.match(
        r"^cal_(grp|sgl)_a_(\d+)_(\d{12}_[cma])$",
        (query.data or "").strip(),
    )
    if not m:
        await query.edit_message_text("❌ Некорректный выбор спортсмена.")
        return

    kind = m.group(1)
    athlete_id = int(m.group(2))
    cfg = CAL_GROUP_BOOK_KINDS.get(kind)
    if not cfg:
        await query.edit_message_text("❌ Некорректный тип записи.")
        return

    subscription_type = cfg["subscription_type"]
    start_date, age_group = _parse_cal_group_slot_token(m.group(3))
    if not start_date or not age_group:
        await query.edit_message_text("❌ Некорректный слот.")
        return

    try:
        with get_db_session() as session:
            user = get_user_by_telegram_id(session, query.from_user.id)
            if not user or get_user_role(user) not in ("coach", "admin"):
                await query.edit_message_text("❌ У вас нет доступа")
                return

            athlete = session.query(Athlete).filter_by(id=athlete_id).first()
            if not athlete:
                await query.edit_message_text("❌ Спортсмен не найден")
                return
            if isinstance(user, Coach) and athlete.created_by != user.id:
                await query.edit_message_text("❌ Это не ваш спортсмен")
                return

            coach_id = user.id if isinstance(user, Coach) else athlete.created_by
            sport_type = coach_sport_type_name(user) if isinstance(user, Coach) else athlete.sport_type
            if not sport_type:
                sport_type = (athlete.sport_type or "").strip()
            if not sport_type or not coach_id:
                await query.edit_message_text("❌ Не удалось определить вид спорта или тренера.")
                return

            if normalize_age_group(athlete.age_group) != normalize_age_group(age_group):
                await query.edit_message_text(
                    "❌ Возрастная группа спортсмена не совпадает с выбранным слотом."
                )
                return

            now = now_moscow()
            if now > start_date + ACTIVATION_GRACE_AFTER_START:
                await query.edit_message_text("❌ Время для записи на этот слот истекло.")
                return
            if db_utils_pkg.is_training_in_global_freeze(session, start_date):
                await query.edit_message_text("❌ Время в периоде массовой заморозки.")
                return

            if isinstance(user, Coach):
                slots = _iter_group_slots_for_calendar_day(
                    session, user, sport_type, start_date.date()
                )
                slot_ok = any(
                    s.training_datetime == start_date
                    and normalize_age_group(s.age_group) == normalize_age_group(age_group)
                    for s in slots
                )
                if not slot_ok:
                    await query.edit_message_text(
                        "❌ Слот недоступен. Выберите другое время.",
                        reply_markup=_build_cal_group_time_keyboard(
                            session,
                            user,
                            sport_type,
                            start_date.year,
                            start_date.month,
                            start_date.day,
                            kind=kind,
                        ),
                    )
                    return

            dk = discipline_key_for(sport_type, format="group")
            active_same = (
                session.query(Subscription.id)
                .filter_by(
                    athlete_id=athlete.id,
                    discipline_key=dk,
                    is_active=True,
                )
                .first()
            )
            if active_same:
                await query.edit_message_text(
                    "❌ У спортсмена уже есть активный групповой абонемент в этом направлении."
                )
                return

            if subscription_type == "single":
                dup = (
                    session.query(Subscription.id)
                    .filter(
                        Subscription.athlete_id == athlete.id,
                        Subscription.subscription_type == "single",
                        Subscription.is_active.is_(True),
                        Subscription.start_date == start_date,
                    )
                    .first()
                )
                if dup:
                    await query.edit_message_text(
                        "❌ У спортсмена уже есть разовая запись на это время."
                    )
                    return

            from handlers.card_handlers import _finalize_subscription_activation
            from database.db_utils.subscriptions import create_subscription as db_create_subscription

            try:
                subscription = db_create_subscription(
                    session=session,
                    athlete_id=athlete.id,
                    subscription_type=subscription_type,
                    sport_type=sport_type,
                    discipline_key=dk,
                    subscription_format="group",
                    responsible_coach_id=coach_id,
                    commit=False,
                )
            except ValueError as ve:
                await query.edit_message_text(f"❌ {ve}")
                return

            session.flush()
            await _finalize_subscription_activation(
                update, context, query, session, subscription, athlete, start_date
            )
    except Exception as e:
        logger.error("cal_%s_a: %s", kind, e, exc_info=True)
        await query.edit_message_text("❌ Ошибка при записи на тренировку")
