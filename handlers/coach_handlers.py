import logging
import os
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes, ConversationHandler
from database.models import Session, Coach, Admin, Athlete, Subscription, Training, Attendance, GlobalFreeze
from database.db_utils import (
    get_user_by_telegram_id,
    get_user_role,
    create_athlete,
    get_delegate_coach_for_admin,
)
import database.db_utils as db_utils_pkg
from typing import List, Optional, Tuple, Union
from utils.training_manager import TrainingManager
from utils.time_utils import now_moscow, ACTIVATION_GRACE_AFTER_START
from keyboards.coach_kb import get_coach_main_menu
from datetime import datetime, timedelta
from sqlalchemy import func
from sqlalchemy.orm import joinedload
import re
import calendar
import html


logger = logging.getLogger(__name__)

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


def resolve_coach_sport_type_name(user) -> Optional[str]:
    """Имя вида спорта для тренера: связь sport_types приоритетнее legacy-строки. У админа — None."""
    if isinstance(user, Coach):
        if user.sport_type_rel:
            return user.sport_type_rel.name
        if user.sport_type:
            return user.sport_type
        return None
    return None


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


def _coach_calendar_message_header(
    *,
    current_year: int,
    current_month: int,
    is_admin: bool,
    sport_type_name: Optional[str],
) -> str:
    title = _MONTH_NAMES_RU[current_month]
    lines = [
        f"📅 <b>{html.escape(title)} {current_year}</b>",
        "",
        "<i>Обозначения: [день] — сегодня; +день — в базе есть тренировка; "
        "(день) — день с тренировкой по расписанию.</i>",
    ]
    if is_admin and not sport_type_name:
        lines.append(
            "<i>Режим администратора: подсветка по общему расписанию недоступна; "
            "символ + показывает дни, где в базе уже есть тренировки.</i>"
        )
    elif sport_type_name:
        lines.append(f"Вид спорта: {html.escape(sport_type_name)}")
    lines.append("")
    return "\n".join(lines)


# Пагинация списка спортсменов (лимит Telegram на callback_data — 64 байта, префикс alpg_)
ATHLETE_LIST_PAGE_SIZE = 20
_ATHLETE_LIST_FILTER_CODES = {
    "active_children": "ac",
    "active_adults": "aa",
    "inactive_children": "ic",
    "inactive_adults": "ia",
    "all": "al",
    "children": "ch",
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
    Спортсмены для экранов «Список спортсменов» с eager-loading абонементов
    (избегает N+1 при обращении к current_subscription).
    """
    if isinstance(user, Admin):
        athletes = (
            session.query(Athlete)
            .options(joinedload(Athlete.subscriptions))
            .all()
        )
        header = "🏃‍♂️ <b>СПИСОК СПОРТСМЕНОВ</b>\n\n"
    elif isinstance(user, Coach):
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
            q = q.filter_by(sport_type=sport_type_name)
        athletes = q.all()
        header = "🏃‍♂️ <b>СПИСОК ВАШИХ СПОРТСМЕНОВ</b>\n\n"
    else:
        athletes = []
        header = "🏃‍♂️ <b>СПИСОК ВАШИХ СПОРТСМЕНОВ</b>\n\n"
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
    "📅 Отметить посещения",
    "📅 Мой календарь",
    "🌍 Массовая заморозка",
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

    session = Session()
    try:
        user = get_user_by_telegram_id(session, user_id)

        if not user or get_user_role(user) not in ['coach', 'admin']:
            print(f"❌ У ПОЛЬЗОВАТЕЛЬ {user_id} НЕТ ДОСТУПА К МЕНЮ ТРЕНЕРА")
            await update.message.reply_text("❌ У вас нет доступа к этому меню")
            return

        reply_markup = get_coach_main_menu()

        await update.message.reply_text(
            "🏋️‍♂️ Меню тренера:\n\n"
            "Выберите действие:",
            reply_markup=reply_markup
        )

    except Exception as e:
        print(f"❌ ОШИБКА В МЕНЮ ТРЕНЕРА: {e}")
        await update.message.reply_text("❌ Произошла ошибка")
    finally:
        session.close()


async def add_athlete_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало процесса добавления спортсмена"""
    user_id = update.effective_user.id
    logger.debug("add_athlete_start user_id=%s", user_id)

    # Очищаем данные предыдущего процесса
    context.user_data.clear()

    session = Session()
    try:
        user = get_user_by_telegram_id(session, user_id)
        logger.debug("add_athlete_start user_id=%s found=%s type=%s", user_id, bool(user), type(user).__name__ if user else None)

        if not user:
            logger.info("add_athlete_start denied: user not in DB user_id=%s", user_id)
            await update.message.reply_text("❌ Пользователь не найден в базе данных. Используйте /start для регистрации.")
            return ConversationHandler.END

        user_role = get_user_role(user)
        logger.debug("add_athlete_start user_id=%s role=%s", user_id, user_role)

        if user_role not in ['coach', 'admin']:
            logger.info("add_athlete_start denied: wrong role user_id=%s role=%s", user_id, user_role)
            await update.message.reply_text("❌ У вас нет прав для добавления спортсменов")
            return ConversationHandler.END

        # Если тренер, автоматически определяем вид спорта из его профиля
        sport_type_name = None
        if isinstance(user, Coach):
            if user.sport_type_rel:
                sport_type_name = user.sport_type_rel.name
            elif user.sport_type:
                sport_type_name = user.sport_type
            if sport_type_name:
                context.user_data['sport_type'] = sport_type_name
                context.user_data['coach_id'] = user.id
                logger.info("add_athlete_start ok user_id=%s sport=%s", user_id, sport_type_name)

                await update.message.reply_text(
                    f"👤 <b>Добавление нового спортсмена</b>\n\n"
                    f"<b>Вид спорта:</b> {sport_type_name}\n\n"
                    f"Введите ФИО спортсмена:",
                    parse_mode='HTML'
                )
                return ATHLETE_FULL_NAME
            await update.message.reply_text(
                "❌ У вас не указана спортивная специализация в профиле. Обратитесь к администратору."
            )
            return ConversationHandler.END

        if isinstance(user, Admin):
            # Вид спорта и слоты — по шаблону делегата (THAI / первый тренер), но created_by у спортсмена = NULL:
            # в списке тренера такие не показываются, только у администратора.
            _thai = os.getenv("THAI_COACH_TELEGRAM_ID", "").strip()
            thai_id = int(_thai) if _thai else None
            delegate = get_delegate_coach_for_admin(session, thai_id)
            if not delegate:
                await update.message.reply_text(
                    "❌ В базе нет тренеров — добавление спортсмена недоступно. "
                    "Создайте тренера или задайте THAI_COACH_TELEGRAM_ID в окружении."
                )
                return ConversationHandler.END
            sport_type_name = None
            if delegate.sport_type_rel:
                sport_type_name = delegate.sport_type_rel.name
            elif delegate.sport_type:
                sport_type_name = delegate.sport_type
            if not sport_type_name:
                await update.message.reply_text(
                    "❌ У тренера в базе не указана спортивная специализация."
                )
                return ConversationHandler.END
            context.user_data["sport_type"] = sport_type_name
            context.user_data["coach_id"] = None
            logger.info(
                "add_athlete_start admin sport=%s (no created_by / not in coach lists)",
                sport_type_name,
            )
            await update.message.reply_text(
                f"👤 <b>Добавление нового спортсмена</b>\n\n"
                f"<b>Вид спорта:</b> {sport_type_name}\n"
                f"<i>Без привязки к тренеру: в списке «своих» у тренеров не отображается, "
                f"только в полном списке администратора.</i>\n\n"
                f"Введите ФИО спортсмена:",
                parse_mode="HTML",
            )
            return ATHLETE_FULL_NAME

        await update.message.reply_text("❌ У вас нет прав для добавления спортсменов")
        return ConversationHandler.END

    except Exception as e:
        logger.exception("add_athlete_start error user_id=%s", user_id)
        await update.message.reply_text("❌ Произошла ошибка")
        return ConversationHandler.END
    finally:
        session.close()


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
    session = Session()
    try:
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
    finally:
        session.close()
    
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

    keyboard = [[KeyboardButton("Детская"), KeyboardButton("Взрослая")]]
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

    # Только кнопки "Детская" / "Взрослая"
    if user_text not in ("Детская", "Взрослая"):
        await update.message.reply_text(
            "❌ Выберите возрастную группу кнопкой: <b>Детская</b> или <b>Взрослая</b>.",
            parse_mode="HTML"
        )
        return ATHLETE_AGE_GROUP

    age_group_ru = user_text
    age_group = "children" if age_group_ru == "Детская" else "adults"
    context.user_data['age_group'] = age_group
    logger.debug("add_athlete_age_group user_id=%s group=%s -> subscription", user_id, age_group)

    keyboard = [[KeyboardButton("Месячный"), KeyboardButton("Разовый")]]
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


async def add_athlete_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка типа абонемента → переход к выбору первой даты тренировки."""
    user_id = update.effective_user.id
    user_text = update.message.text
    logger.debug("add_athlete_subscription user_id=%s", user_id)

    if user_text in MENU_BUTTONS:
        logger.info("add_athlete interrupted at subscription user_id=%s (menu)", user_id)
        await cancel_athlete_creation(update, context)
        return ConversationHandler.END

    if user_text not in ("Месячный", "Разовый"):
        await update.message.reply_text(
            "❌ Выберите тип абонемента кнопкой: <b>Месячный</b> или <b>Разовый</b>.",
            parse_mode="HTML"
        )
        return ATHLETE_SUBSCRIPTION

    subscription_type_ru = user_text
    subscription_type = "monthly" if subscription_type_ru == "Месячный" else "single"
    context.user_data['subscription_type'] = subscription_type
    context.user_data['subscription_type_ru'] = subscription_type_ru
    logger.info("add_athlete_subscription user_id=%s type=%s -> calendar", user_id, subscription_type)

    sport_type = context.user_data['sport_type']
    age_group = context.user_data['age_group']
    date_kb = create_add_athlete_training_calendar(sport_type, age_group)
    if not date_kb:
        await update.message.reply_text(
            "❌ Нет доступных дат тренировок по расписанию для этой группы. Обратитесь к администратору.",
            reply_markup=get_coach_main_menu()
        )
        return ConversationHandler.END

    await update.message.reply_text(
        "📅 Выберите <b>первую дату тренировки</b> по абонементу:",
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
    reply_markup = create_add_athlete_training_calendar(sport_type, age_group, month=month, year=year)
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

    schedule = TrainingManager.TRAINING_SCHEDULE.get(sport_type, {}).get(age_group)
    if not schedule:
        await query.answer()
        await query.edit_message_text("❌ Расписание для этой группы не найдено. Обратитесь к администратору.")
        return ConversationHandler.END

    coach_selected_date = datetime(year, month, day, 0, 0, 0)
    await query.answer()
    return await _finalize_add_athlete_from_selected_date(query, context, coach_selected_date)


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


async def _finalize_add_athlete_from_selected_date(query, context, coach_selected_date: datetime, *, skip_freeze_confirm: bool = False):
    """Единая логика завершения добавления спортсмена по выбранной дате."""
    sport_type = context.user_data['sport_type']
    age_group = context.user_data['age_group']
    subscription_type = context.user_data['subscription_type']

    # Первая дата тренировки по расписанию
    start_date = db_utils_pkg._find_nearest_training_date(
        coach_selected_date.replace(hour=0, minute=0, second=0, microsecond=0),
        sport_type,
        age_group,
    )
    session = Session()
    try:
        # Если первая тренировка попала в активную массовую заморозку,
        # просим подтверждение с автоматическим сдвигом.
        if db_utils_pkg.is_training_in_global_freeze(session, start_date) and not skip_freeze_confirm:
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
        else:
            end_date = db_utils_pkg.training_end_time(start_date)

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
            commit=False,
        )
        subscription.start_date = start_date
        subscription.end_date = end_date
        subscription.is_active = True

        if subscription_type == "monthly":
            db_utils_pkg._create_and_deduct_scheduled_trainings(session, subscription, athlete, start_date, end_date)
        else:
            coach_id = athlete.created_by or context.user_data.get('coach_id')
            training = session.query(Training).filter_by(
                sport_type=sport_type,
                age_group=age_group,
                training_date=start_date,
                is_cancelled=False
            ).first()
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

        session.commit()

        age_group_display = "Детская" if athlete.age_group == "children" else "Взрослая"
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
        try:
            session.rollback()
        except Exception:
            pass
        logger.error("add_athlete finalize failed: %s", e, exc_info=True)
        await query.edit_message_text("❌ Ошибка при добавлении спортсмена")
    finally:
        session.close()

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

    session = Session()
    try:
        user = get_user_by_telegram_id(session, user_id)
        logger.debug("athletes_list user_id=%s found=%s type=%s", user_id, bool(user), type(user).__name__ if user else None)

        if not user or get_user_role(user) not in ['coach', 'admin']:
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
        active_children = 0
        active_adults = 0
        inactive_children = 0
        inactive_adults = 0
        
        for a in athletes:
            status = SubscriptionChecker.get_subscription_status(a.current_subscription) if a.current_subscription else "no_subscription"
            is_active = is_active_status(status)
            
            if a.age_group == "children":
                if is_active:
                    active_children += 1
                else:
                    inactive_children += 1
            else:
                if is_active:
                    active_adults += 1
                else:
                    inactive_adults += 1
        
        active_total = active_children + active_adults
        inactive_total = inactive_children + inactive_adults

        message = message_header
        message += "<b>ВЫБЕРИТЕ КАТЕГОРИЮ:</b>"

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
    finally:
        session.close()


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
        "active_adults",
        "inactive_children",
        "inactive_adults",
        "all",
        "children",
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
    session = Session()
    try:
        user = get_user_by_telegram_id(session, user_id)

        if not user or get_user_role(user) not in ['coach', 'admin']:
            if update.callback_query:
                await update.callback_query.answer("❌ У вас нет доступа")
            return

        athletes, message_header = load_athletes_for_list(session, user)

        from utils.subscription_checker import SubscriptionChecker

        def is_active_status(status: str) -> bool:
            return status in ("active", "expiring_soon")

        # Подсчет детей и взрослых в выбранной категории
        children_count = 0
        adults_count = 0
        
        for a in athletes:
            status = SubscriptionChecker.get_subscription_status(a.current_subscription) if a.current_subscription else "no_subscription"
            is_active = is_active_status(status)
            
            if status_type == "active" and is_active:
                if a.age_group == "children":
                    children_count += 1
                else:
                    adults_count += 1
            elif status_type == "inactive" and not is_active:
                if a.age_group == "children":
                    children_count += 1
                else:
                    adults_count += 1

        status_label = "✅ <b>АКТИВНЫЕ</b>" if status_type == "active" else "❌ <b>НЕАКТИВНЫЕ</b>"
        message = message_header + status_label + "\n\n"
        message += "<b>ВЫБЕРИТЕ ВОЗРАСТНУЮ ГРУППУ:</b>"

        keyboard = [
            [
                InlineKeyboardButton(f"👶 Дети ({children_count})", callback_data=f"athletes_{status_type}_children"),
                InlineKeyboardButton(f"👨‍🦰 Взрослые ({adults_count})", callback_data=f"athletes_{status_type}_adults"),
            ],
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
    finally:
        session.close()


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
    session = Session()
    try:
        user = get_user_by_telegram_id(session, user_id)

        if not user or get_user_role(user) not in ['coach', 'admin']:
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

        def athlete_status(a: Athlete) -> str:
            return SubscriptionChecker.get_subscription_status(a.current_subscription) if a.current_subscription else "no_subscription"

        # Фильтрация
        if filter_key == "active_children":
            filtered = [a for a in athletes if a.age_group == "children" and is_active_status(athlete_status(a))]
            filter_title = "✅ <b>АКТИВНЫЕ - ДЕТСКАЯ ГРУППА</b>\n\n"
        elif filter_key == "active_adults":
            filtered = [a for a in athletes if a.age_group != "children" and is_active_status(athlete_status(a))]
            filter_title = "✅ <b>АКТИВНЫЕ - ВЗРОСЛАЯ ГРУППА</b>\n\n"
        elif filter_key == "inactive_children":
            filtered = [a for a in athletes if a.age_group == "children" and not is_active_status(athlete_status(a))]
            filter_title = "❌ <b>НЕАКТИВНЫЕ - ДЕТСКАЯ ГРУППА</b>\n\n"
        elif filter_key == "inactive_adults":
            filtered = [a for a in athletes if a.age_group != "children" and not is_active_status(athlete_status(a))]
            filter_title = "❌ <b>НЕАКТИВНЫЕ - ВЗРОСЛАЯ ГРУППА</b>\n\n"
        elif filter_key == "all":
            filtered = list(athletes)
            filter_title = "📋 <b>ВСЕ СПОРТСМЕНЫ</b>\n\n"
        else:
            # Старые фильтры для обратной совместимости
            if filter_key == "children":
                filtered = [a for a in athletes if a.age_group == "children"]
                filter_title = "👶 <b>ДЕТСКАЯ ГРУППА</b>\n\n"
            elif filter_key == "adults":
                filtered = [a for a in athletes if a.age_group != "children"]
                filter_title = "👨‍🦰 <b>ВЗРОСЛАЯ ГРУППА</b>\n\n"
            elif filter_key == "inactive":
                filtered = [a for a in athletes if not is_active_status(athlete_status(a))]
                filter_title = "❌ <b>НЕАКТИВНЫЕ АБОНЕМЕНТЫ / НЕТ АБОНЕМЕНТА</b>\n\n"
            else:
                filtered = list(athletes)
                filter_title = "📋 <b>ВСЕ СПОРТСМЕНЫ</b>\n\n"

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
    finally:
        session.close()

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
        "Выберите действие:",
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
        "Выберите действие из меню:",
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
        "Выберите действие из меню:",
        reply_markup=get_coach_main_menu(),
    )
    return ConversationHandler.END


async def start_training(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Шаг 1: слоты на сегодня для отметки посещений (после окончания занятия)."""
    from services.attendance_training_flow import build_today_attendance_slots

    user_id = update.effective_user.id
    query = update.callback_query
    if query:
        await query.answer()

    logger.info("📅 Запрос на отметку посещений от пользователя %s", user_id)
    session = Session()
    try:
        user = get_user_by_telegram_id(session, user_id)

        if not user or get_user_role(user) not in ['coach', 'admin']:
            if query:
                await query.edit_message_text("❌ У вас нет доступа к этому меню")
            else:
                await update.message.reply_text("❌ У вас нет доступа к этому меню")
            return

        now = now_moscow()
        slot_rows, virtual_slots = build_today_attendance_slots(session, user, now)
        context.user_data["attendance_virtual_slots"] = virtual_slots

        message = "📅 <b>ОТМЕТИТЬ ПОСЕЩЕНИЯ</b>\n\n"
        message += (
            "<i>Фиксировать присутствие можно после окончания слота (по расписанию).</i>\n\n"
        )
        if slot_rows:
            message += (
                f"Сегодня слотов: <b>{len(slot_rows)}</b>\n\n"
                "<b>Шаг 1/2: выберите слот</b>\n"
            )
        else:
            message += "На сегодня слотов не найдено.\n\n"

        keyboard = []
        for slot in slot_rows:
            age_group_ru = "Дети" if slot.age_group == "children" else "Взрослые"
            button_text = (
                f"🕒 {slot.training_datetime.strftime('%H:%M')} | "
                f"{slot.sport_type} ({age_group_ru})"
            )
            callback_data = (
                f"select_mark_training_virtual_{slot.virtual_token}"
                if slot.is_virtual
                else f"select_mark_training_{slot.training_id}"
            )
            keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])

        if not slot_rows:
            keyboard.append([InlineKeyboardButton("🔄 Обновить", callback_data="attendance_training_list")])

        keyboard.append([InlineKeyboardButton("🏠 В меню", callback_data="back_to_menu_main")])

        reply_markup = InlineKeyboardMarkup(keyboard)
        if query:
            await query.edit_message_text(message, reply_markup=reply_markup, parse_mode='HTML')
        else:
            await update.message.reply_text(message, reply_markup=reply_markup, parse_mode='HTML')

    except Exception as e:
        logger.error("❌ Ошибка в start_training: %s", e, exc_info=True)
        if query:
            await query.edit_message_text("❌ Ошибка при загрузке тренировок")
        else:
            await update.message.reply_text("❌ Ошибка при загрузке тренировок")
    finally:
        session.close()


async def handle_attendance_training_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обновить экран выбора тренировки для отметки посещений."""
    await start_training(update, context)


async def show_coach_calendar(update: Update, context: ContextTypes.DEFAULT_TYPE, month: int = None, year: int = None):
    """Показать календарь тренировок тренера с промаркированными днями и навигацией"""
    user_id = update.effective_user.id
    logger.debug("show_coach_calendar: user_id=%s", user_id)

    session = Session()
    try:
        user = get_user_by_telegram_id(session, user_id)

        if not user or get_user_role(user) not in ['coach', 'admin']:
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

        sport_type_name = resolve_coach_sport_type_name(user)
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

        if isinstance(user, Admin):
            trainings = session.query(Training).filter(
                Training.training_date >= month_start,
                Training.training_date < month_end,
                Training.is_cancelled == False
            ).order_by(Training.training_date.asc()).all()
        else:
            query = session.query(Training).filter(
                Training.coach_id == user.id,
                Training.training_date >= month_start,
                Training.training_date < month_end,
                Training.is_cancelled == False
            )
            if sport_type_name:
                query = query.filter(Training.sport_type == sport_type_name)
            trainings = query.order_by(Training.training_date.asc()).all()

        # Группируем тренировки по датам
        trainings_by_date = {}
        for training in trainings:
            date_key = training.training_date.date()
            if date_key not in trainings_by_date:
                trainings_by_date[date_key] = []
            trainings_by_date[date_key].append(training)

        # Дни недели с тренировками по расписанию (для тренера с видом спорта)
        scheduled_days = set()
        if sport_type_name:
            schedule_dict = TrainingManager.TRAINING_SCHEDULE.get(sport_type_name, {})
            for age_group in ['children', 'adults']:
                schedule = schedule_dict.get(age_group)
                if schedule and 'days' in schedule:
                    scheduled_days.update(schedule['days'])

        message = _coach_calendar_message_header(
            current_year=current_year,
            current_month=current_month,
            is_admin=isinstance(user, Admin),
            sport_type_name=sport_type_name,
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
                        btn_text = f"({day:2d})"
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
    finally:
        session.close()


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
    session = Session()

    try:
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
        if not user or get_user_role(user) not in ['coach', 'admin']:
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

        sport_type_name = resolve_coach_sport_type_name(user)

        if isinstance(user, Admin):
            trainings = session.query(Training).filter(
                Training.training_date >= date_start,
                Training.training_date <= date_end,
                Training.is_cancelled == False
            ).order_by(Training.training_date.asc()).all()
        else:
            query_filter = session.query(Training).filter(
                Training.coach_id == user.id,
                Training.training_date >= date_start,
                Training.training_date <= date_end,
                Training.is_cancelled == False
            )
            if sport_type_name:
                query_filter = query_filter.filter(Training.sport_type == sport_type_name)
            trainings = query_filter.order_by(Training.training_date.asc()).all()

        date_str = selected_date.strftime("%d.%m.%Y")
        message = f"<b>📅 {date_str}</b>\n\n"

        if trainings:
            message += f"<b>Тренировок: {len(trainings)}</b>\n\n"
            for training in trainings:
                age_group_ru = "Дети" if training.age_group == "children" else "Взрослые"
                time_str = training.training_date.strftime("%H:%M")
                message += f"• <b>{time_str}</b> - {training.sport_type} ({age_group_ru})\n"

                training_date_only = training.training_date.date()
                subs = session.query(Subscription).join(
                    Athlete, Subscription.athlete_id == Athlete.id
                ).filter(
                    Subscription.is_active == True,
                    Subscription.sport_type == training.sport_type,
                    Athlete.age_group == training.age_group,
                    func.date(Subscription.start_date) <= training_date_only,
                    func.date(Subscription.end_date) >= training_date_only,
                ).all()

                if subs:
                    message += f"  <b>Записано спортсменов: {len(subs)}</b>\n"
                    athlete_ids = [sub.athlete_id for sub in subs]
                    athletes_map = {
                        a.id: a
                        for a in session.query(Athlete).filter(Athlete.id.in_(athlete_ids)).all()
                    }
                    sub_ids = [s.id for s in subs]
                    att_by_sub = {
                        a.subscription_id: a
                        for a in session.query(Attendance).filter(
                            Attendance.training_id == training.id,
                            Attendance.subscription_id.in_(sub_ids),
                        ).all()
                    }
                    for sub in subs[:10]:
                        ath = athletes_map.get(sub.athlete_id)
                        if not ath:
                            continue
                        att = att_by_sub.get(sub.id)
                        if att is None:
                            status_icon = "⏳"
                        else:
                            status_icon = "✅" if att.attended else "❌"
                        message += f"    {status_icon} {html.escape(ath.full_name)}\n"
                    if len(subs) > 10:
                        message += f"    ... и еще {len(subs) - 10}\n"
                else:
                    message += f"  Нет записанных спортсменов\n"

                message += "\n"
        else:
            weekday = selected_date.weekday()

            if sport_type_name:
                schedule_info = {}
                for age_group in ['children', 'adults']:
                    schedule = TrainingManager.TRAINING_SCHEDULE.get(sport_type_name, {}).get(age_group)
                    if schedule and weekday in schedule['days']:
                        schedule_info[age_group] = schedule

                if schedule_info:
                    message += "<b>По расписанию:</b>\n\n"

                    for age_group, schedule in schedule_info.items():
                        age_group_ru = "Дети" if age_group == "children" else "Взрослые"
                        time_str = TrainingManager.get_time_str_for_weekday(schedule, weekday)

                        message += f"• <b>{time_str}</b> - {sport_type_name} ({age_group_ru})\n"

                        athletes_with_subscriptions = (
                            session.query(Athlete)
                            .options(joinedload(Athlete.subscriptions))
                            .join(Subscription, Athlete.id == Subscription.athlete_id)
                            .filter(
                                Subscription.is_active == True,
                                func.date(Subscription.start_date) <= selected_date,
                                func.date(Subscription.end_date) >= selected_date,
                                Athlete.sport_type == sport_type_name,
                                Athlete.age_group == age_group,
                            )
                        )

                        if isinstance(user, Coach):
                            athletes_with_subscriptions = athletes_with_subscriptions.filter(
                                Athlete.created_by == user.id
                            )

                        athletes_list = athletes_with_subscriptions.all()

                        if athletes_list:
                            message += f"  <b>Записано спортсменов: {len(athletes_list)}</b>\n"
                            aid_list = [a.id for a in athletes_list]
                            atts = (
                                session.query(Attendance)
                                .join(Training, Attendance.training_id == Training.id)
                                .filter(
                                    Training.sport_type == sport_type_name,
                                    Training.age_group == age_group,
                                    func.date(Training.training_date) == selected_date,
                                    Attendance.athlete_id.in_(aid_list),
                                )
                                .all()
                            )
                            att_by_pair = {}
                            for att in atts:
                                key = (att.athlete_id, att.subscription_id)
                                if key not in att_by_pair:
                                    att_by_pair[key] = att

                            for athlete in athletes_list[:10]:
                                subscription = athlete.current_subscription
                                if subscription:
                                    attendance = att_by_pair.get((athlete.id, subscription.id))
                                    if attendance:
                                        status_icon = "✅" if attendance.attended else "❌"
                                    else:
                                        status_icon = "❌"
                                    message += f"    {status_icon} {html.escape(athlete.full_name)}\n"
                            if len(athletes_list) > 10:
                                message += f"    ... и еще {len(athletes_list) - 10}\n"
                        else:
                            message += f"  Нет записанных спортсменов\n"

                        message += "\n"
                else:
                    message += "На эту дату тренировок не запланировано.\n\n"
            else:
                message += "На эту дату тренировок не запланировано.\n\n"

        keyboard = [[
            InlineKeyboardButton("🔙 К календарю", callback_data=f"calendar_{year}_{month}")
        ]]
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
    finally:
        session.close()