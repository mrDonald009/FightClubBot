import logging
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes, ConversationHandler
from database.models import Session, Coach, Admin, Athlete, Subscription, Training, Attendance
from database.db_utils import get_user_by_telegram_id, get_user_role, create_athlete
from typing import Union
from utils.training_manager import TrainingManager
from utils.time_utils import now_moscow, ACTIVATION_GRACE_AFTER_START
from keyboards.coach_kb import get_coach_main_menu
from datetime import datetime, timedelta
from sqlalchemy import func
from sqlalchemy.orm import joinedload
import re
import calendar
import html
import asyncio


logger = logging.getLogger(__name__)


async def _set_reply_keyboard_silently(message, reply_markup):
    """
    Telegram не позволяет совмещать InlineKeyboardMarkup и ReplyKeyboardMarkup в одном сообщении,
    поэтому меню приходится "устанавливать" отдельным сообщением.
    Иногда Telegram отклоняет пустые/невидимые символы (400 Text must be non-empty),
    поэтому используем невидимый символ и самый крайний fallback удаляем.
    """
    # Telegram не позволяет применить ReplyKeyboard без сообщения.
    # Поэтому отправляем служебное сообщение и сразу удаляем его — клавиатура при этом остается.
    for text in ("\u3164", "\u200e", "."):  # HANGUL FILLER, LRM, крайний fallback
        try:
            tmp = await message.reply_text(text, reply_markup=reply_markup)
            # Даем клиенту шанс применить клавиатуру
            try:
                await asyncio.sleep(0.2)
            except Exception:
                pass
            try:
                await tmp.delete()
            except Exception:
                pass
            return
        except Exception:
            continue
    return

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
    "🏋️ Начать тренировку",
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
    print(f"🔔🔔🔔 ОБРАБОТЧИК ВЫЗВАН: add_athlete_start для пользователя {user_id}")
    print(f"👤 ПОЛЬЗОВАТЕЛЬ {user_id} НАЧАЛ ДОБАВЛЕНИЕ СПОРТСМЕНА")
    print(f"🔍 DEBUG: add_athlete_start вызван для пользователя {user_id}")

    # Очищаем данные предыдущего процесса
    context.user_data.clear()

    session = Session()
    try:
        user = get_user_by_telegram_id(session, user_id)
        print(f"🔍 DEBUG add_athlete_start: user={user}, type={type(user) if user else None}")

        if not user:
            print(f"❌ DEBUG: Пользователь {user_id} не найден в базе данных")
            await update.message.reply_text("❌ Пользователь не найден в базе данных. Используйте /start для регистрации.")
            return ConversationHandler.END

        user_role = get_user_role(user)
        print(f"🔍 DEBUG add_athlete_start: user_role={user_role}")

        if user_role not in ['coach', 'admin']:
            print(f"❌ У ПОЛЬЗОВАТЕЛЯ {user_id} НЕТ ПРАВ ДОБАВЛЯТЬ СПОРТСМЕНОВ (роль: {user_role})")
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
                print(f"🥊 ТРЕНЕР {user_id} РАБОТАЕТ С ВИДОМ СПОРТА: {sport_type_name}")

                await update.message.reply_text(
                    f"👤 <b>Добавление нового спортсмена</b>\n\n"
                    f"<b>Вид спорта:</b> {sport_type_name}\n\n"
                    f"Введите ФИО спортсмена:",
                    parse_mode='HTML'
                )
                print(f"✅ УСТАНОВЛЕНО СОСТОЯНИЕ ATHLETE_FULL_NAME ДЛЯ {user_id}")
                return ATHLETE_FULL_NAME
        else:
            # Если у тренера не указан вид спорта или это админ - показываем выбор
            await update.message.reply_text(
                "❌ У вас не указана спортивная специализация. Обратитесь к администратору."
            )
            return ConversationHandler.END

    except Exception as e:
        print(f"❌ ОШИБКА ПРИ НАЧАЛЕ ДОБАВЛЕНИЯ: {e}")
        await update.message.reply_text("❌ Произошла ошибка")
        return ConversationHandler.END
    finally:
        session.close()


async def add_athlete_full_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка ФИО спортсмена с валидацией"""
    user_id = update.effective_user.id
    user_text = update.message.text
    print(f"🎯 ВХОД В add_athlete_full_name ДЛЯ ПОЛЬЗОВАТЕЛЯ {user_id}, ТЕКСТ: '{user_text}'")

    # Проверяем, не является ли ввод кнопкой меню
    if user_text in MENU_BUTTONS:
        print(f"🚫 ПОЛЬЗОВАТЕЛЬ {user_id} ПРЕРВАЛ ВВОД ФИО, ВЫБРАВ: {user_text}")
        await cancel_athlete_creation(update, context)
        return ConversationHandler.END

    full_name = normalize_full_name(user_text)

    # ВАЛИДАЦИЯ ФИО - проверяем, что это не номер телефона
    if is_phone_number(full_name):
        print(f"❌ ОБНАРУЖЕН НОМЕР ТЕЛЕФОНА ВМЕСТО ФИО: {full_name}")
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
    print(f"✅ ВВЕДЕНО ФИО: {normalized}, ПЕРЕХОДИМ В ATHLETE_PHONE")

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
    print(f"🎯 ВХОД В add_athlete_phone ДЛЯ ПОЛЬЗОВАТЕЛЯ {user_id}, ТЕКСТ: '{user_text}'")

    # Проверяем, не является ли ввод кнопкой меню
    if user_text in MENU_BUTTONS:
        print(f"🚫 ПОЛЬЗОВАТЕЛЬ {user_id} ПРЕРВАЛ ВВОД ТЕЛЕФОНА, ВЫБРАВ: {user_text}")
        await cancel_athlete_creation(update, context)
        return ConversationHandler.END

    # Проверяем, что пользователь не ввел ФИО вместо телефона
    if not has_digits(user_text) or is_valid_name_format(user_text):
        print(f"❌ ПОЛЬЗОВАТЕЛЬ {user_id} ВВЕЛ ФИО ВМЕСТО ТЕЛЕФОНА: '{user_text}'")
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
        print(f"❌ НЕВЕРНЫЙ ФОРМАТ ТЕЛЕФОНА ОТ ПОЛЬЗОВАТЕЛЯ {user_id}: '{user_text}'")
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
    print(f"✅ ВВЕДЕН ТЕЛЕФОН: {full_phone}, ПЕРЕХОДИМ В ATHLETE_BIRTH_DATE")

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
    print(f"🎯 ВХОД В add_athlete_birth_date ДЛЯ ПОЛЬЗОВАТЕЛЯ {user_id}, ТЕКСТ: '{user_text}'")

    # Проверяем, не является ли ввод кнопкой меню
    if user_text in MENU_BUTTONS:
        print(f"🚫 ПОЛЬЗОВАТЕЛЬ {user_id} ПРЕРВАЛ ВВОД ДАТЫ РОЖДЕНИЯ, ВЫБРАВ: {user_text}")
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
    print(f"✅ СОХРАНЕНА ДАТА РОЖДЕНИЯ: {bd.strftime('%d.%m.%Y')}")

    await update.message.reply_text(
        "🏥 Введите медицинские противопоказания (или напишите <b>нет</b>, если отсутствуют):",
        parse_mode="HTML"
    )
    return ATHLETE_MEDICAL


async def add_athlete_medical(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка медицинской информации"""
    user_id = update.effective_user.id
    user_text = update.message.text.strip()
    print(f"🎯 ВХОД В add_athlete_medical ДЛЯ ПОЛЬЗОВАТЕЛЯ {user_id}, ТЕКСТ: '{user_text}'")

    # Проверяем, не является ли ввод кнопкой меню
    if user_text in MENU_BUTTONS:
        print(f"🚫 ПОЛЬЗОВАТЕЛЬ {user_id} ПРЕРВАЛ ВВОД МЕД.ДАННЫХ, ВЫБРАВ: {user_text}")
        await cancel_athlete_creation(update, context)
        return ConversationHandler.END

    # Обрабатываем кнопку "нет"
    if user_text.lower() == "нет":
        print(f"✅ ПОЛЬЗОВАТЕЛЬ {user_id} УКАЗАЛ ОТСУТСТВИЕ ПРОТИВОПОКАЗАНИЙ")
        medical_info = "Нет противопоказаний"
    else:
        medical_info = user_text
        print(f"✅ ВВЕДЕНЫ МЕД.ДАННЫЕ: '{medical_info}'")

    context.user_data['medical_info'] = medical_info
    print(f"✅ МЕД.ДАННЫЕ СОХРАНЕНЫ В user_data: '{medical_info}', ПЕРЕХОДИМ К ВЫБОРУ ТИПА АБОНЕМЕНТА")

    keyboard = [[KeyboardButton("Месячный"), KeyboardButton("Разовый")]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text(
        "🎫 Выберите тип абонемента:",
        reply_markup=reply_markup
    )
    return ATHLETE_SUBSCRIPTION


async def add_athlete_age_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка возрастной группы"""
    user_id = update.effective_user.id
    user_text = update.message.text
    print(f"🎯 ВХОД В add_athlete_age_group ДЛЯ ПОЛЬЗОВАТЕЛЯ {user_id}, ТЕКСТ: '{user_text}'")

    # Проверяем, не является ли ввод кнопкой меню
    if user_text in MENU_BUTTONS:
        print(f"🚫 ПОЛЬЗОВАТЕЛЬ {user_id} ПРЕРВАЛ ВЫБОР ВОЗРАСТНОЙ ГРУППЫ, ВЫБРАВ: {user_text}")
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
    print(f"✅ ВЫБРАНА ВОЗРАСТНАЯ ГРУППА: {age_group_ru} ({age_group}), ПОКАЗЫВАЕМ КАЛЕНДАРЬ ДАТ")

    sport_type = context.user_data['sport_type']
    date_kb = create_date_keyboard(sport_type, age_group)
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


async def add_athlete_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка типа абонемента → переход к выбору возрастной группы."""
    user_id = update.effective_user.id
    user_text = update.message.text
    print(f"🎯 ВХОД В add_athlete_subscription ДЛЯ ПОЛЬЗОВАТЕЛЯ {user_id}, ТЕКСТ: '{user_text}'")

    if user_text in MENU_BUTTONS:
        print(f"🚫 ПОЛЬЗОВАТЕЛЬ {user_id} ПРЕРВАЛ ВЫБОР АБОНЕМЕНТА, ВЫБРАВ: {user_text}")
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
    print(f"✅ ВЫБРАН ТИП АБОНЕМЕНТА: {subscription_type_ru} ({subscription_type}), ПЕРЕХОД К ВЫБОРУ ВОЗРАСТНОЙ ГРУППЫ")

    keyboard = [[KeyboardButton("Детская"), KeyboardButton("Взрослая")]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text(
        "👦👨 Выберите возрастную группу:",
        reply_markup=reply_markup
    )
    return ATHLETE_AGE_GROUP


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
        print(f"❌ ОШИБКА ПАРСИНГА ДАТЫ: {e}")
        await query.edit_message_text("❌ Ошибка при обработке выбранной даты")
        return ConversationHandler.END

    sport_type = context.user_data['sport_type']
    age_group = context.user_data['age_group']
    subscription_type = context.user_data['subscription_type']

    from database.db_utils import (
        _find_nearest_training_date,
        _calculate_12th_training_date,
        _create_and_deduct_scheduled_trainings,
        create_subscription as db_create_subscription,
        training_end_time,
        sync_subscription_trainings_remaining,
    )

    # Первая дата тренировки по расписанию
    start_date = _find_nearest_training_date(
        coach_selected_date.replace(hour=0, minute=0, second=0, microsecond=0),
        sport_type,
        age_group,
    )
    if subscription_type == "monthly":
        end_date = _calculate_12th_training_date(start_date, sport_type, age_group)
    else:
        end_date = training_end_time(start_date)

    session = Session()
    try:
        athlete = create_athlete(
            session=session,
            telegram_id=None,
            full_name=context.user_data['full_name'],
            phone=context.user_data['phone'],
            birth_date=context.user_data.get('birth_date'),
            medical_info=context.user_data['medical_info'],
            sport_type=sport_type,
            age_group=age_group,
            created_by=context.user_data['coach_id'],
            commit=False,
        )
        subscription = db_create_subscription(
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
            _create_and_deduct_scheduled_trainings(session, subscription, athlete, start_date, end_date)
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
        sync_subscription_trainings_remaining(session, subscription)

        session.commit()

        age_group_display = "Детская" if athlete.age_group == "children" else "Взрослая"
        subscription_type_ru = context.user_data['subscription_type_ru']
        context.user_data.clear()

        print(f"✅ УСПЕШНО ДОБАВЛЕН СПОРТСМЕН: {athlete.full_name}, абонемент {subscription_type_ru}, первая дата {start_date}")

        birth_date_display = (
            athlete.birth_date.strftime('%d.%m.%Y')
            if getattr(athlete, "birth_date", None) else "Не указана"
        )
        await query.edit_message_text(
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
        await _set_reply_keyboard_silently(query.message, get_coach_main_menu())
    except Exception as e:
        print(f"❌ ОШИБКА ПРИ ДОБАВЛЕНИИ СПОРТСМЕНА: {e}")
        logger.error(f"❌ ОШИБКА ПРИ ДОБАВЛЕНИИ СПОРТСМЕНА: {e}", exc_info=True)
        await query.edit_message_text("❌ Ошибка при добавлении спортсмена")
    finally:
        session.close()

    return ConversationHandler.END


async def athletes_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать меню выбора категории для списка спортсменов тренера"""
    user_id = update.effective_user.id
    print(f"🔔🔔🔔 ОБРАБОТЧИК ВЫЗВАН: athletes_list для пользователя {user_id}")
    print(f"📋 ПОЛЬЗОВАТЕЛЬ {user_id} ЗАПРОСИЛ СПИСОК СПОРТСМЕНОВ (КАТЕГОРИИ)")
    print(f"🔍 DEBUG: athletes_list вызван для пользователя {user_id}")

    session = Session()
    try:
        user = get_user_by_telegram_id(session, user_id)
        print(f"🔍 DEBUG: Пользователь найден: {user}, тип: {type(user)}")

        if not user or get_user_role(user) not in ['coach', 'admin']:
            if update.callback_query:
                await update.callback_query.answer("❌ У вас нет доступа")
            else:
                await update.message.reply_text("❌ У вас нет доступа к этому меню")
            return

        # Получаем спортсменов
        if isinstance(user, Admin):
            athletes = session.query(Athlete).all()
            message_header = "🏃‍♂️ <b>СПИСОК СПОРТСМЕНОВ</b>\n\n"
        else:
            # Фильтруем по тренеру и виду спорта
            if isinstance(user, Coach):
                query = session.query(Athlete).filter_by(created_by=user.id)
                # Получаем вид спорта из связи или из строки (для обратной совместимости)
                sport_type_name = None
                if user.sport_type_rel:
                    sport_type_name = user.sport_type_rel.name
                elif user.sport_type:
                    sport_type_name = user.sport_type
                if sport_type_name:
                    query = query.filter_by(sport_type=sport_type_name)
                athletes = query.all()
            else:
                athletes = []
            message_header = "🏃‍♂️ <b>СПИСОК ВАШИХ СПОРТСМЕНОВ</b>\n\n"

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
    await query.answer()

    filter_key = (query.data or "").replace("athletes_", "").strip()
    
    # Если выбран active или inactive, показываем подменю с детьми/взрослыми
    if filter_key == "active":
        await show_active_inactive_submenu(update, context, "active")
        return
    elif filter_key == "inactive":
        await show_active_inactive_submenu(update, context, "inactive")
        return
    elif filter_key in ("active_children", "active_adults", "inactive_children", "inactive_adults", "all"):
        await show_athletes_list_by_filter(update, context, filter_key)
        return
    
    await query.answer("❌ Неизвестный фильтр")


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

        # Получаем спортсменов с явной загрузкой subscription
        if isinstance(user, Admin):
            athletes = session.query(Athlete).options(joinedload(Athlete.subscriptions)).all()
            message_header = "🏃‍♂️ <b>СПИСОК СПОРТСМЕНОВ</b>\n\n"
        else:
            # Фильтруем по тренеру и виду спорта
            if isinstance(user, Coach):
                query = session.query(Athlete).options(joinedload(Athlete.subscriptions)).filter_by(created_by=user.id)
                # Получаем вид спорта из связи или из строки (для обратной совместимости)
                sport_type_name = None
                if user.sport_type_rel:
                    sport_type_name = user.sport_type_rel.name
                elif user.sport_type:
                    sport_type_name = user.sport_type
                if sport_type_name:
                    query = query.filter_by(sport_type=sport_type_name)
                athletes = query.all()
            else:
                athletes = []
            message_header = "🏃‍♂️ <b>СПИСОК ВАШИХ СПОРТСМЕНОВ</b>\n\n"

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
    # сбрасываем последний фильтр, чтобы «к списку» из карточки возвращал в категории
    context.user_data.pop("athletes_list_filter", None)
    await athletes_list(update, context)


async def show_athletes_list_by_filter(update: Update, context: ContextTypes.DEFAULT_TYPE, filter_key: str):
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

        # Берем базовый список с явной загрузкой subscriptions (один-ко-многим)
        if isinstance(user, Admin):
            athletes = session.query(Athlete).options(joinedload(Athlete.subscriptions)).all()
            header_base = "🏃‍♂️ <b>СПИСОК СПОРТСМЕНОВ</b>\n\n"
        else:
            # Фильтруем по тренеру и виду спорта
            if isinstance(user, Coach):
                query = session.query(Athlete).options(joinedload(Athlete.subscriptions)).filter_by(created_by=user.id)
                # Получаем вид спорта из связи или из строки (для обратной совместимости)
                sport_type_name = None
                if user.sport_type_rel:
                    sport_type_name = user.sport_type_rel.name
                elif user.sport_type:
                    sport_type_name = user.sport_type
                if sport_type_name:
                    query = query.filter_by(sport_type=sport_type_name)
                athletes = query.all()
            else:
                athletes = []
            header_base = "🏃‍♂️ <b>СПИСОК ВАШИХ СПОРТСМЕНОВ</b>\n\n"

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
        
        # Формируем текст
        message = header_base + filter_title

        # Клавиатура спортсменов (первые 20)
        keyboard = []
        for i in range(0, min(len(filtered), 20), 2):
            row = []
            for j in range(2):
                if i + j < len(filtered):
                    a = filtered[i + j]

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

        if len(filtered) > 20:
            keyboard.append([InlineKeyboardButton(f"📝 Показано 20 из {len(filtered)}", callback_data="show_more_info")])

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
    print(f"🚫 ПОЛЬЗОВАТЕЛЬ {user_id} ОТМЕНИЛ ДОБАВЛЕНИЕ СПОРТСМЕНА")

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
    """Шаг 1: показать тренировки на сегодня для дальнейшей отметки посещений."""
    user_id = update.effective_user.id
    query = update.callback_query
    if query:
        await query.answer()

    logger.info("🏋️ Запрос на начало тренировки от пользователя %s", user_id)
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
        today_start = datetime(now.year, now.month, now.day)
        today_end = today_start + timedelta(days=1)
        weekday = now.weekday()
        context.user_data["attendance_virtual_slots"] = {}

        if isinstance(user, Admin):
            today_trainings = session.query(Training).filter(
                Training.training_date >= today_start,
                Training.training_date < today_end,
                Training.is_cancelled == False
            ).order_by(Training.training_date.asc()).all()
            existing_keys = {(t.sport_type, t.age_group, t.training_date.hour, t.training_date.minute) for t in today_trainings}
            for sport_type_name, schedule_map in TrainingManager.TRAINING_SCHEDULE.items():
                for age_group in ("children", "adults"):
                    schedule = schedule_map.get(age_group)
                    if not schedule or weekday not in schedule.get("days", []):
                        continue
                    hour, minute = TrainingManager.get_hour_minute_for_weekday(schedule, weekday)
                    key = (sport_type_name, age_group, hour, minute)
                    if key in existing_keys:
                        continue
                    token = f"v{len(context.user_data['attendance_virtual_slots'])}"
                    context.user_data["attendance_virtual_slots"][token] = {
                        "sport_type": sport_type_name,
                        "age_group": age_group,
                        "hour": hour,
                        "minute": minute,
                        "coach_id": None,
                    }
                    virtual_training = Training(
                        sport_type=sport_type_name,
                        age_group=age_group,
                        training_date=today_start.replace(hour=hour, minute=minute, second=0, microsecond=0),
                        is_cancelled=False,
                    )
                    virtual_training.id = None
                    virtual_training._is_virtual = True
                    virtual_training._virtual_token = token
                    today_trainings.append(virtual_training)
            today_trainings.sort(key=lambda t: t.training_date)
        else:
            trainings_query = session.query(Training).filter(
                Training.training_date >= today_start,
                Training.training_date < today_end,
                Training.is_cancelled == False,
                Training.coach_id == user.id
            )
            sport_type_name = None
            if isinstance(user, Coach):
                if user.sport_type_rel:
                    sport_type_name = user.sport_type_rel.name
                elif user.sport_type:
                    sport_type_name = user.sport_type
            if sport_type_name:
                trainings_query = trainings_query.filter(Training.sport_type == sport_type_name)
            today_trainings = trainings_query.order_by(Training.training_date.asc()).all()

            # Fallback по расписанию: если слот есть по расписанию, но еще не создан в БД,
            # показываем его в списке как доступный для выбора.
            if not sport_type_name and today_trainings:
                # В старых профилях тренера вид спорта может отсутствовать в карточке,
                # но присутствовать в уже созданных слотах тренировок.
                sport_type_name = today_trainings[0].sport_type
            schedule_map = TrainingManager.TRAINING_SCHEDULE.get(sport_type_name, {}) if sport_type_name else {}
            existing_keys = {(t.sport_type, t.age_group, t.training_date.hour, t.training_date.minute) for t in today_trainings}
            for age_group in ("children", "adults"):
                schedule = schedule_map.get(age_group)
                if not schedule or weekday not in schedule.get("days", []):
                    continue
                hour, minute = TrainingManager.get_hour_minute_for_weekday(schedule, weekday)
                key = (sport_type_name, age_group, hour, minute)
                if key in existing_keys:
                    continue
                token = f"v{len(context.user_data['attendance_virtual_slots'])}"
                context.user_data["attendance_virtual_slots"][token] = {
                    "sport_type": sport_type_name,
                    "age_group": age_group,
                    "hour": hour,
                    "minute": minute,
                    "coach_id": user.id,
                }
                virtual_training = Training(
                    sport_type=sport_type_name,
                    age_group=age_group,
                    training_date=today_start.replace(hour=hour, minute=minute, second=0, microsecond=0),
                    is_cancelled=False,
                    coach_id=user.id,
                )
                virtual_training.id = None
                virtual_training._is_virtual = True
                virtual_training._virtual_token = token
                today_trainings.append(virtual_training)

            today_trainings.sort(key=lambda t: t.training_date)

        message = "🏋️ <b>НАЧАТЬ ТРЕНИРОВКУ</b>\n\n"
        if today_trainings:
            message += (
                f"📅 Сегодня запланировано тренировок: <b>{len(today_trainings)}</b>\n\n"
                "<b>Шаг 1/2: выберите тренировку</b>\n"
            )
        else:
            message += "📅 На сегодня тренировок не запланировано.\n\n"

        keyboard = []

        for training in today_trainings:
            age_group_ru = "Дети" if training.age_group == "children" else "Взрослые"
            button_text = (
                f"🕒 {training.training_date.strftime('%H:%M')} | "
                f"{training.sport_type} ({age_group_ru})"
            )
            is_virtual = bool(getattr(training, "_is_virtual", False))
            callback_data = (
                f"select_mark_training_virtual_{getattr(training, '_virtual_token', '')}"
                if is_virtual else
                f"select_mark_training_{training.id}"
            )
            keyboard.append([
                InlineKeyboardButton(
                    button_text,
                    callback_data=callback_data
                )
            ])

        if not today_trainings:
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
    print(f"🔔🔔🔔 ОБРАБОТЧИК ВЫЗВАН: show_coach_calendar для пользователя {user_id}")
    print(f"📅 ПОЛЬЗОВАТЕЛЬ {user_id} ЗАПРОСИЛ КАЛЕНДАРЬ ТРЕНИРОВОК")
    print(f"🔍 DEBUG: show_coach_calendar вызван для пользователя {user_id}")

    session = Session()
    try:
        user = get_user_by_telegram_id(session, user_id)

        if not user or get_user_role(user) not in ['coach', 'admin']:
            if update.callback_query:
                await update.callback_query.answer("❌ У вас нет доступа")
            else:
                await update.message.reply_text("❌ У вас нет доступа к этому меню")
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
        
        # Получаем расписание для вида спорта тренера
        sport_type = user.sport_type if user.sport_type else None
        if not sport_type and user.role != 'admin':
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
            message_header = "📅 <b>КАЛЕНДАРЬ</b>\n\n"
        else:
            query = session.query(Training).filter(
                Training.coach_id == user.id,
                Training.training_date >= month_start,
                Training.training_date < month_end,
                Training.is_cancelled == False
            )
            if sport_type:
                query = query.filter(Training.sport_type == sport_type)
            trainings = query.order_by(Training.training_date.asc()).all()
            message_header = f"📅 <b>КАЛЕНДАРЬ</b>\n\n"

        # Группируем тренировки по датам
        trainings_by_date = {}
        for training in trainings:
            date_key = training.training_date.date()
            if date_key not in trainings_by_date:
                trainings_by_date[date_key] = []
            trainings_by_date[date_key].append(training)

        # Получаем все дни недели, когда есть тренировки по расписанию для данного вида спорта
        scheduled_days = set()
        if sport_type:
            schedule_dict = TrainingManager.TRAINING_SCHEDULE.get(sport_type, {})
            for age_group in ['children', 'adults']:
                schedule = schedule_dict.get(age_group)
                if schedule and 'days' in schedule:
                    scheduled_days.update(schedule['days'])

        # Формируем сообщение
        message = message_header
        
        # Создаем календарь (monthcalendar возвращает недели с понедельника как первый день)
        cal = calendar.monthcalendar(current_year, current_month)
        
        # Создаем интерактивную клавиатуру из 35 квадратных кнопок (5 строк × 7 дней)
        keyboard = []
        
        # Добавляем строку с днями недели над календарем
        day_names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
        day_names_buttons = []
        for day_name in day_names:
            # Формат: Пн. Вт. Ср. (просто текст)
            # Примечание: В Telegram Bot API нельзя задать цвет текста в InlineKeyboardButton
            day_names_buttons.append(InlineKeyboardButton(f"{day_name}.", callback_data="cal_empty"))
        keyboard.append(day_names_buttons)
        
        # Обеспечиваем ровно 5 строк (если недель меньше - дополняем пустыми, если больше - берем первые 5)
        weeks_to_show = cal[:5]  # Берем максимум 5 недель
        while len(weeks_to_show) < 5:
            # Дополняем пустыми неделями до 5 строк
            weeks_to_show.append([0, 0, 0, 0, 0, 0, 0])
        
        # Создаем ровно 5 строк по 7 квадратов (минимальный размер)
        for week in weeks_to_show:
            week_buttons = []
            for day in week:
                if day == 0:
                    # Пустой день - создаем неактивную квадратную кнопку
                    week_buttons.append(InlineKeyboardButton(" ", callback_data="cal_empty"))
                else:
                    date_obj = datetime(current_year, current_month, day).date()
                    # Проверяем, есть ли тренировка по расписанию на этот день недели
                    weekday = date_obj.weekday()
                    has_scheduled_training = weekday in scheduled_days
                    
                    # Формируем текст квадратной кнопки
                    if date_obj == today:
                        btn_text = f"[{day:2d}]"  # Сегодня - квадратные скобки (приоритет)
                    elif has_scheduled_training:
                        btn_text = f"({day:2d})"  # Есть тренировка по расписанию - круглые скобки
                    else:
                        btn_text = f"{day:2d}"  # Обычный день
                    
                    callback_data = f"cal_date_{current_year}_{current_month}_{day}"
                    week_buttons.append(InlineKeyboardButton(btn_text, callback_data=callback_data))
            
            # Всегда добавляем строку из 7 кнопок (квадратов)
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
        import traceback
        error_trace = traceback.format_exc()
        print(f"❌ ОШИБКА ПРИ ПОЛУЧЕНИИ КАЛЕНДАРЯ: {e}")
        print(f"❌ ТРАССИРОВКА: {error_trace}")
        logger.error(f"❌ ОШИБКА ПРИ ПОЛУЧЕНИИ КАЛЕНДАРЯ: {e}", exc_info=True)
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
    await query.answer()
    
    user_id = update.effective_user.id
    logger.info(f"📅 Клик по дате в календаре: {query.data} от пользователя {user_id}")
    session = Session()
    
    try:
        # Парсим callback_data: cal_date_YYYY_MM_DD
        parts = query.data.split("_")
        logger.info(f"📅 Парсинг callback_data: {parts}, длина: {len(parts)}")
        if len(parts) != 5:
            logger.error(f"❌ Неверный формат callback_data: {query.data}, частей: {len(parts)}")
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
        
        # Получаем тренировки на выбранную дату
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
            # Получаем вид спорта из связи или из строки (для обратной совместимости)
            sport_type_name = None
            if isinstance(user, Coach):
                if user.sport_type_rel:
                    sport_type_name = user.sport_type_rel.name
                elif user.sport_type:
                    sport_type_name = user.sport_type
            if sport_type_name:
                query_filter = query_filter.filter(Training.sport_type == sport_type_name)
            trainings = query_filter.order_by(Training.training_date.asc()).all()
        
        # Формируем сообщение
        date_str = selected_date.strftime("%d.%m.%Y")
        message = f"<b>📅 {date_str}</b>\n\n"
        
        if trainings:
            message += f"<b>Тренировок: {len(trainings)}</b>\n\n"
            for training in trainings:
                age_group_ru = "Дети" if training.age_group == "children" else "Взрослые"
                time_str = training.training_date.strftime("%H:%M")
                message += f"• <b>{time_str}</b> - {training.sport_type} ({age_group_ru})\n"
                
                # Получаем спортсменов по активным абонементам (а Attendance используем только для статуса).
                # Это нужно, чтобы новые/разовые абонементы отображались в календаре сразу после активации,
                # даже если еще не создана запись Attendance.
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
                    for sub in subs[:10]:
                        ath = session.query(Athlete).filter_by(id=sub.athlete_id).first()
                        if not ath:
                            continue
                        att = session.query(Attendance).filter_by(
                            athlete_id=ath.id,
                            training_id=training.id,
                            subscription_id=sub.id
                        ).first()
                        if att is None:
                            status_icon = "⏳"
                        else:
                            status_icon = "✅" if att.attended else "❌"
                        message += f"    {status_icon} {ath.full_name}\n"
                    if len(subs) > 10:
                        message += f"    ... и еще {len(subs) - 10}\n"
                else:
                    message += f"  Нет записанных спортсменов\n"
                
                message += "\n"
        else:
            # Если тренировок нет в базе, но есть расписание - показываем спортсменов по расписанию
            weekday = selected_date.weekday()
            # Получаем вид спорта из связи или из строки (для обратной совместимости)
            sport_type = None
            if user.sport_type_rel:
                sport_type = user.sport_type_rel.name
            elif user.sport_type:
                sport_type = user.sport_type
            
            if sport_type:
                schedule_info = {}
                for age_group in ['children', 'adults']:
                    schedule = TrainingManager.TRAINING_SCHEDULE.get(sport_type, {}).get(age_group)
                    if schedule and weekday in schedule['days']:
                        schedule_info[age_group] = schedule
                
                if schedule_info:
                    message += "<b>По расписанию:</b>\n\n"
                    
                    # Создаем datetime для этой даты и времени тренировки
                    for age_group, schedule in schedule_info.items():
                        age_group_ru = "Дети" if age_group == "children" else "Взрослые"
                        day_names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
                        day_name = day_names[weekday]
                        time_str = TrainingManager.get_time_str_for_weekday(schedule, weekday)
                        hour, minute = TrainingManager.get_hour_minute_for_weekday(schedule, weekday)
                        training_datetime = datetime.combine(selected_date, datetime.min.time()).replace(hour=hour, minute=minute)
                        
                        message += f"• <b>{time_str}</b> - {sport_type} ({age_group_ru})\n"
                        
                        # Получаем спортсменов с активными абонементами на эту дату
                        # Проверяем, что выбранная дата попадает в диапазон действия абонемента (включительно)
                        # Связь один-ко-многим через athlete_id
                        athletes_with_subscriptions = session.query(Athlete).join(
                            Subscription, Athlete.id == Subscription.athlete_id
                        ).filter(
                            Subscription.is_active == True,
                            func.date(Subscription.start_date) <= selected_date,  # Дата начала <= выбранная дата
                            func.date(Subscription.end_date) >= selected_date,    # Дата окончания >= выбранная дата
                            Athlete.sport_type == sport_type,
                            Athlete.age_group == age_group
                        )
                        
                        # Фильтруем по тренеру, если это тренер
                        if isinstance(user, Coach):
                            athletes_with_subscriptions = athletes_with_subscriptions.filter(
                                Athlete.created_by == user.id
                            )
                        
                        athletes_list = athletes_with_subscriptions.all()
                        
                        if athletes_list:
                            message += f"  <b>Записано спортсменов: {len(athletes_list)}</b>\n"
                            for athlete in athletes_list[:10]:  # Показываем до 10 спортсменов
                                # Проверяем, есть ли запись Attendance (для отметки статуса)
                                subscription = athlete.current_subscription
                                if subscription:
                                    attendance = session.query(Attendance).join(
                                        Training
                                    ).filter(
                                        Attendance.athlete_id == athlete.id,
                                        Attendance.subscription_id == subscription.id,
                                        Training.sport_type == sport_type,
                                        Training.age_group == age_group,
                                        func.date(Training.training_date) == selected_date
                                    ).first()
                                    
                                    if attendance:
                                        status_icon = "✅" if attendance.attended else "❌"
                                    else:
                                        status_icon = "❌"  # Неиспользовано по умолчанию
                                    
                                    message += f"    {status_icon} {athlete.full_name}\n"
                            if len(athletes_list) > 10:
                                message += f"    ... и еще {len(athletes_list) - 10}\n"
                        else:
                            message += f"  Нет записанных спортсменов\n"
                        
                        message += "\n"
                else:
                    message += "На эту дату тренировок не запланировано.\n\n"
        
        # Кнопка возврата к календарю
        keyboard = [[
            InlineKeyboardButton("🔙 К календарю", callback_data=f"calendar_{year}_{month}")
        ]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            message,
            reply_markup=reply_markup,
            parse_mode='HTML'
        )
        
    except Exception as e:
        logger.error(f"❌ ОШИБКА ПРИ ОБРАБОТКЕ КЛИКА ПО ДАТЕ: {e}", exc_info=True)
        await query.answer("❌ Ошибка при загрузке данных")
    finally:
        session.close()