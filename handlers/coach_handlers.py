import logging
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes, ConversationHandler
from database.models import Session, User, Athlete, Subscription, Training, Attendance
from database.db_utils import get_user_by_telegram_id, create_athlete
from services.subscription_service import SubscriptionService
from utils.training_manager import TrainingManager
from keyboards.coach_kb import get_coach_main_menu
from datetime import datetime, timedelta
from sqlalchemy import func
from sqlalchemy.orm import joinedload
import random
import re
import calendar
import html


logger = logging.getLogger(__name__)

# Состояния для добавления спортсмена
(
    ATHLETE_FULL_NAME,
    ATHLETE_PHONE,
    ATHLETE_MEDICAL,
    ATHLETE_SPORT_TYPE,
    ATHLETE_AGE_GROUP,
    ATHLETE_TRAINING_DATE
) = range(6)

# Список кнопок меню для проверки прерывания
MENU_BUTTONS = [
    "👥 Добавить спортсмена",
    "📋 Список спортсменов",
    "🏋️ Начать тренировку",
    "📅 Мой календарь"
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
    # Разрешаем буквы, пробелы, дефисы и апострофы
    name_pattern = r'^[a-zA-Zа-яА-ЯёЁ\s\-'']+$'

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

        if not user or user.role not in ['coach', 'admin']:
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
    print(f"👤 ПОЛЬЗОВАТЕЛЬ {user_id} НАЧАЛ ДОБАВЛЕНИЕ СПОРТСМЕНА")

    # Очищаем данные предыдущего процесса
    context.user_data.clear()

    session = Session()
    try:
        user = get_user_by_telegram_id(session, user_id)

        if not user or user.role not in ['coach', 'admin']:
            print(f"❌ У ПОЛЬЗОВАТЕЛЯ {user_id} НЕТ ПРАВ ДОБАВЛЯТЬ СПОРТСМЕНОВ")
            await update.message.reply_text("❌ У вас нет прав для добавления спортсменов")
            return ConversationHandler.END

        # Если тренер, автоматически определяем вид спорта из его профиля
        if user.role == 'coach' and user.sport_type:
            context.user_data['sport_type'] = user.sport_type
            print(f"🥊 ТРЕНЕР {user_id} РАБОТАЕТ С ВИДОМ СПОРТА: {user.sport_type}")

            await update.message.reply_text(
                f"👤 <b>Добавление нового спортсмена</b>\n\n"
                f"<b>Вид спорта:</b> {user.sport_type}\n\n"
                f"Введите ФИО спортсмена:",
                parse_mode='HTML'
            )

            context.user_data['coach_id'] = user.id
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

    full_name = user_text.strip()

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

    # Проверяем, что введен текст (не пустой и не слишком короткий)
    if not full_name or len(full_name) < 2:
        await update.message.reply_text(
            "❌ ФИО слишком короткое!\n"
            "Пожалуйста, введите полное ФИО спортсмена:"
        )
        return ATHLETE_FULL_NAME

    # Проверяем, что в ФИО есть только буквы, пробелы, дефисы
    if not is_valid_name_format(full_name):
        await update.message.reply_text(
            "❌ <b>Некорректный формат ФИО!</b>\n\n"
            "ФИО должно содержать только:\n"
            "• Буквы русского/английского алфавита\n"
            "• Пробелы\n"
            "• Дефисы\n\n"
            "<i>Пример: Петров-Сидоров Иван Александрович</i>",
            parse_mode='HTML'
        )
        return ATHLETE_FULL_NAME

    # Проверяем на дубликаты ФИО
    session = Session()
    try:
        existing_athlete = session.query(Athlete).filter_by(full_name=full_name).first()
        if existing_athlete:
            await update.message.reply_text(
                f"❌ <b>Спортсмен с таким ФИО уже существует!</b>\n\n"
                f"ФИО: <b>{html.escape(full_name)}</b>\n"
                f"Телефон: {existing_athlete.phone or 'Не указан'}\n\n"
                f"Пожалуйста, введите другое ФИО или отмените добавление командой /cancel",
                parse_mode='HTML'
            )
            return ATHLETE_FULL_NAME
    finally:
        session.close()

    context.user_data['full_name'] = full_name
    print(f"✅ ВВЕДЕНО ФИО: {full_name}, ПЕРЕХОДИМ В ATHLETE_PHONE")

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
    print(f"✅ ВВЕДЕН ТЕЛЕФОН: {full_phone}, ПЕРЕХОДИМ В ATHLETE_MEDICAL")

    await update.message.reply_text(
        "🏥 Введите медицинские противопоказания (или нажмите 'нет' если отсутствуют):"
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
    print(f"✅ МЕД.ДАННЫЕ СОХРАНЕНЫ В user_data: '{medical_info}', ПЕРЕХОДИМ В ATHLETE_AGE_GROUP")

    # Пропускаем выбор вида спорта - используем специализацию тренера
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
    print(f"🎯 ВХОД В add_athlete_age_group ДЛЯ ПОЛЬЗОВАТЕЛЯ {user_id}, ТЕКСТ: '{user_text}'")

    # Проверяем, не является ли ввод кнопкой меню
    if user_text in MENU_BUTTONS:
        print(f"🚫 ПОЛЬЗОВАТЕЛЬ {user_id} ПРЕРВАЛ ВЫБОР ВОЗРАСТНОЙ ГРУППЫ, ВЫБРАВ: {user_text}")
        await cancel_athlete_creation(update, context)
        return ConversationHandler.END

    age_group_ru = user_text
    age_group = "children" if age_group_ru == "Детская" else "adults"
    context.user_data['age_group'] = age_group
    print(f"✅ ВЫБРАНА ВОЗРАСТНАЯ ГРУППА: {age_group_ru} ({age_group}), СОЗДАЕМ СПОРТСМЕНА И АБОНЕМЕНТ")

    # Создаем спортсмена и абонемент (без типа, неактивный)
    session = Session()
    try:
        temp_telegram_id = -random.randint(10000, 99999)

        from database.db_utils import create_user
        athlete_user = create_user(
            session=session,
            telegram_id=temp_telegram_id,
            username=None,
            first_name=context.user_data['full_name'].split()[0],
            role="athlete"
        )

        athlete = create_athlete(
            session=session,
            user_id=athlete_user.id,
            full_name=context.user_data['full_name'],
            phone=context.user_data['phone'],
            medical_info=context.user_data['medical_info'],
            sport_type=context.user_data['sport_type'],
            age_group=age_group,
            created_by=context.user_data['coach_id']
        )

        # Создаем абонемент автоматически (без типа, неактивный)
        from services.subscription_service import SubscriptionService
        subscription = SubscriptionService.create_subscription(
            session=session,
            athlete_id=athlete.id,
            subscription_type=None,  # Тип не определен, будет выбран при активации
            sport_type=athlete.sport_type
        )

        # Конвертируем возрастную группу для отображения
        age_group_display = "Детская" if age_group == "children" else "Взрослая"

        # Очищаем данные процесса
        context.user_data.clear()

        print(f"✅ УСПЕШНО ДОБАВЛЕН СПОРТСМЕН С АБОНЕМЕНТОМ: {athlete.full_name}")

        await update.message.reply_text(
            f"✅ Спортсмен успешно добавлен!\n\n"
            f"📝 ФИО: {athlete.full_name}\n"
            f"📞 Телефон: {athlete.phone}\n"
            f"🥊 Вид спорта: {athlete.sport_type}\n"
            f"👥 Группа: {age_group_display}\n"
            f"🏥 Мед. информация: {athlete.medical_info}\n"
            f"🎫 Абонемент: ❌ Неактивен (тип не определен)\n\n"
            f"💡 Активируйте абонемент в карточке спортсмена.",
            reply_markup=get_coach_main_menu()
        )

    except Exception as e:
        print(f"❌ ОШИБКА ПРИ ДОБАВЛЕНИИ СПОРТСМЕНА: {e}")
        logger.error(f"❌ ОШИБКА ПРИ ДОБАВЛЕНИИ СПОРТСМЕНА: {e}", exc_info=True)
        await update.message.reply_text("❌ Ошибка при добавлении спортсмена")
    finally:
        session.close()

    return ConversationHandler.END


def get_available_training_dates(sport_type, age_group, month=None, year=None, max_months=2):
    """Получить доступные даты тренировок для текущего и следующих месяцев по расписанию"""
    now = datetime.now()
    if month is None:
        month = now.month
        year = now.year
    
    schedule = TrainingManager.TRAINING_SCHEDULE.get(sport_type, {}).get(age_group)
    if not schedule:
        return []
    
    days = schedule['days']
    time_str = schedule['time']
    hour, minute = map(int, time_str.split(':'))
    
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
                training_datetime = current_day.replace(hour=hour, minute=minute, second=0, microsecond=0)
                # Добавляем только будущие даты (не прошедшие)
                if training_datetime >= now:
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
    """Обработка типа абонемента"""
    user_id = update.effective_user.id
    user_text = update.message.text
    print(f"🎯 ВХОД В add_athlete_subscription ДЛЯ ПОЛЬЗОВАТЕЛЬ {user_id}, ТЕКСТ: '{user_text}'")

    # Проверяем, не является ли ввод кнопкой меню
    if user_text in MENU_BUTTONS:
        print(f"🚫 ПОЛЬЗОВАТЕЛЬ {user_id} ПРЕРВАЛ ВЫБОР АБОНЕМЕНТА, ВЫБРАВ: {user_text}")
        await cancel_athlete_creation(update, context)
        return ConversationHandler.END

    subscription_type_ru = user_text
    subscription_type = "monthly" if subscription_type_ru == "Месячный" else "single"
    print(f"✅ ВЫБРАН ТИП АБОНЕМЕНТА: {subscription_type_ru} ({subscription_type})")
    
    # Сохраняем тип абонемента
    context.user_data['subscription_type'] = subscription_type
    context.user_data['subscription_type_ru'] = subscription_type_ru
    
    # Создаем спортсмена и абонемент сразу (для месячного и разового)
    session = Session()
    try:
        temp_telegram_id = -random.randint(10000, 99999)

        from database.db_utils import create_user
        athlete_user = create_user(
            session=session,
            telegram_id=temp_telegram_id,
            username=None,
            first_name=context.user_data['full_name'].split()[0],
            role="athlete"
        )

        athlete = create_athlete(
            session=session,
            user_id=athlete_user.id,
            full_name=context.user_data['full_name'],
            phone=context.user_data['phone'],
            medical_info=context.user_data['medical_info'],
            sport_type=context.user_data['sport_type'],
            age_group=context.user_data['age_group'],
            created_by=context.user_data['coach_id']
        )

        # Используем SubscriptionService для создания абонемента
        subscription = SubscriptionService.create_subscription(
            session=session,
            athlete_id=athlete.id,
            subscription_type=subscription_type,
            sport_type=athlete.sport_type
        )

        # Конвертируем возрастную группу для отображения
        age_group_display = "Детская" if athlete.age_group == "children" else "Взрослая"

        # Очищаем данные процесса
        context.user_data.clear()

        print(f"✅ УСПЕШНО ДОБАВЛЕН СПОРТСМЕН: {athlete.full_name}")

        await update.message.reply_text(
            f"✅ Спортсмен успешно добавлен!\n\n"
            f"📝 ФИО: {athlete.full_name}\n"
            f"📞 Телефон: {athlete.phone}\n"
            f"🥊 Вид спорта: {athlete.sport_type}\n"
            f"👥 Группа: {age_group_display}\n"
            f"🎫 Абонемент: {subscription_type_ru}\n"
            f"🏥 Мед. информация: {athlete.medical_info}\n"
            f"💪 Осталось тренировок: {subscription.trainings_remaining}",
            reply_markup=get_coach_main_menu()
        )

    except Exception as e:
        print(f"❌ ОШИБКА ПРИ ДОБАВЛЕНИИ СПОРТСМЕНА: {e}")
        await update.message.reply_text("❌ Ошибка при добавлении спортсмена")
    finally:
        session.close()

    return ConversationHandler.END


async def handle_training_date_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка выбора даты тренировки для разового абонемента"""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    print(f"🎯 ВЫБРАНА ДАТА ТРЕНИРОВКИ ПОЛЬЗОВАТЕЛЕМ {user_id}")
    
    # Парсим дату из callback_data: select_training_date_YYYY-MM-DD-HH-MM
    date_str = query.data.replace("select_training_date_", "")
    try:
        year, month, day, hour, minute = map(int, date_str.split("-"))
        training_datetime = datetime(year, month, day, hour, minute)
    except Exception as e:
        print(f"❌ ОШИБКА ПАРСИНГА ДАТЫ: {e}")
        await query.edit_message_text("❌ Ошибка при обработке выбранной даты")
        return ConversationHandler.END
    
    # Сохраняем выбранную дату
    context.user_data['selected_training_date'] = training_datetime
    
    session = Session()
    try:
        temp_telegram_id = -random.randint(10000, 99999)
        
        from database.db_utils import create_user
        athlete_user = create_user(
            session=session,
            telegram_id=temp_telegram_id,
            username=None,
            first_name=context.user_data['full_name'].split()[0],
            role="athlete"
        )
        
        athlete = create_athlete(
            session=session,
            user_id=athlete_user.id,
            full_name=context.user_data['full_name'],
            phone=context.user_data['phone'],
            medical_info=context.user_data['medical_info'],
            sport_type=context.user_data['sport_type'],
            age_group=context.user_data['age_group'],
            created_by=context.user_data['coach_id']
        )
        
        # Получаем тренера спортсмена (кто его создал) или из контекста
        coach_id = athlete.created_by if athlete.created_by else context.user_data.get('coach_id')
        
        # Создаем или находим тренировку
        training = session.query(Training).filter_by(
            sport_type=athlete.sport_type,
            age_group=athlete.age_group,
            training_date=training_datetime,
            is_cancelled=False
        ).first()
        
        if not training:
            training = Training(
                sport_type=athlete.sport_type,
                age_group=athlete.age_group,
                training_date=training_datetime,
                is_cancelled=False,
                coach_id=coach_id
            )
            session.add(training)
            session.flush()
        elif not training.coach_id and coach_id:
            # Обновляем coach_id если его не было
            training.coach_id = coach_id
            session.flush()
        
        # Используем SubscriptionService для создания абонемента
        subscription = SubscriptionService.create_subscription(
            session=session,
            athlete_id=athlete.id,
            subscription_type=context.user_data['subscription_type'],
            sport_type=athlete.sport_type
        )
        
        # Конвертируем возрастную группу для отображения
        age_group_display = "Детская" if athlete.age_group == "children" else "Взрослая"
        subscription_type_ru = context.user_data['subscription_type_ru']
        
        # Очищаем данные процесса
        context.user_data.clear()
        
        print(f"✅ УСПЕШНО ДОБАВЛЕН СПОРТСМЕН С РАЗОВОЙ ТРЕНИРОВКОЙ: {athlete.full_name}")
        
        await query.edit_message_text(
            f"✅ Спортсмен успешно добавлен!\n\n"
            f"📝 ФИО: {athlete.full_name}\n"
            f"📞 Телефон: {athlete.phone}\n"
            f"🥊 Вид спорта: {athlete.sport_type}\n"
            f"👥 Группа: {age_group_display}\n"
            f"🎫 Абонемент: {subscription_type_ru}\n"
            f"📅 Дата тренировки: {training_datetime.strftime('%d.%m.%Y %H:%M')}\n"
            f"🏥 Мед. информация: {athlete.medical_info}\n"
            f"💪 Осталось тренировок: {subscription.trainings_remaining}"
        )
        
        # Отправляем сообщение с клавиатурой меню
        await query.message.reply_text(
            "Выберите действие из меню:",
            reply_markup=get_coach_main_menu()
        )
        
    except Exception as e:
        print(f"❌ ОШИБКА ПРИ ДОБАВЛЕНИИ СПОРТСМЕНА С РАЗОВОЙ ТРЕНИРОВКОЙ: {e}")
        logger.error(f"❌ ОШИБКА ПРИ ДОБАВЛЕНИИ СПОРТСМЕНА С РАЗОВОЙ ТРЕНИРОВКОЙ: {e}", exc_info=True)
        await query.edit_message_text("❌ Ошибка при добавлении спортсмена")
    finally:
        session.close()
    
    return ConversationHandler.END


async def athletes_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать меню выбора категории для списка спортсменов тренера"""
    user_id = update.effective_user.id
    print(f"📋 ПОЛЬЗОВАТЕЛЬ {user_id} ЗАПРОСИЛ СПИСОК СПОРТСМЕНОВ (КАТЕГОРИИ)")

    session = Session()
    try:
        user = get_user_by_telegram_id(session, user_id)

        if not user or user.role not in ['coach', 'admin']:
            if update.callback_query:
                await update.callback_query.answer("❌ У вас нет доступа")
            else:
                await update.message.reply_text("❌ У вас нет доступа к этому меню")
            return

        # Получаем спортсменов
        if user.role == 'admin':
            athletes = session.query(Athlete).all()
            message_header = "🏃‍♂️ <b>СПИСОК СПОРТСМЕНОВ</b>\n\n"
        else:
            # Фильтруем по тренеру и виду спорта
            query = session.query(Athlete).filter_by(created_by=user.id)
            if user.sport_type:
                query = query.filter_by(sport_type=user.sport_type)
            athletes = query.all()
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

        if not user or user.role not in ['coach', 'admin']:
            if update.callback_query:
                await update.callback_query.answer("❌ У вас нет доступа")
            return

        # Получаем спортсменов с явной загрузкой subscription
        if user.role == 'admin':
            athletes = session.query(Athlete).options(joinedload(Athlete.subscriptions)).all()
            message_header = "🏃‍♂️ <b>СПИСОК СПОРТСМЕНОВ</b>\n\n"
        else:
            # Фильтруем по тренеру и виду спорта
            query = session.query(Athlete).options(joinedload(Athlete.subscriptions)).filter_by(created_by=user.id)
            if user.sport_type:
                query = query.filter_by(sport_type=user.sport_type)
            athletes = query.all()
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

        if not user or user.role not in ['coach', 'admin']:
            if update.callback_query:
                await update.callback_query.answer("❌ У вас нет доступа")
            else:
                await update.message.reply_text("❌ У вас нет доступа к этому меню")
            return

        # Запоминаем фильтр, чтобы возврат «📋 К списку» из карточки работал ожидаемо
        context.user_data["athletes_list_filter"] = filter_key

        # Берем базовый список с явной загрузкой subscriptions (один-ко-многим)
        if user.role == "admin":
            athletes = session.query(Athlete).options(joinedload(Athlete.subscriptions)).all()
            header_base = "🏃‍♂️ <b>СПИСОК СПОРТСМЕНОВ</b>\n\n"
        else:
            # Фильтруем по тренеру и виду спорта
            query = session.query(Athlete).options(joinedload(Athlete.subscriptions)).filter_by(created_by=user.id)
            if user.sport_type:
                query = query.filter_by(sport_type=user.sport_type)
            athletes = query.all()
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


async def start_training(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начать тренировку - показать список спортсменов для отметки посещения"""
    user_id = update.effective_user.id
    session = Session()
    try:
        user = get_user_by_telegram_id(session, user_id)

        if not user or user.role not in ['coach', 'admin']:
            await update.message.reply_text("❌ У вас нет доступа к этому меню")
            return

        # Получаем спортсменов с явной загрузкой subscription
        if user.role == 'admin':
            athletes = session.query(Athlete).options(joinedload(Athlete.subscriptions)).all()
        else:
            # Фильтруем по тренеру и виду спорта
            query = session.query(Athlete).options(joinedload(Athlete.subscriptions)).filter_by(created_by=user.id)
            if user.sport_type:
                query = query.filter_by(sport_type=user.sport_type)
            athletes = query.all()

        if not athletes:
            await update.message.reply_text(
                "📭 У вас пока нет спортсменов.\n\n"
                "Добавьте первого спортсмена через меню '👥 Добавить спортсмена'"
            )
            return

        # Получаем текущую дату и время
        now = datetime.utcnow()
        today_start = datetime(now.year, now.month, now.day)
        today_end = today_start + timedelta(days=1)

        # Получаем тренировки на сегодня
        if user.role == 'admin':
            today_trainings = session.query(Training).filter(
                Training.training_date >= today_start,
                Training.training_date < today_end,
                Training.is_cancelled == False
            ).all()
        else:
            # Фильтруем по тренеру и виду спорта
            query = session.query(Training).filter(
                Training.training_date >= today_start,
                Training.training_date < today_end,
                Training.is_cancelled == False,
                Training.coach_id == user.id
            )
            if user.sport_type:
                query = query.filter(Training.sport_type == user.sport_type)
            today_trainings = query.all()

        message = "🏋️ <b>НАЧАТЬ ТРЕНИРОВКУ</b>\n\n"
        
        if today_trainings:
            message += f"📅 Тренировок сегодня: {len(today_trainings)}\n\n"
        else:
            message += "📅 На сегодня тренировок не запланировано\n\n"
        
        message += "<b>ВЫБЕРИТЕ СПОРТСМЕНА ДЛЯ ОТМЕТКИ:</b>"

        # Создаем клавиатуру со спортсменами
        keyboard = []
        for i in range(0, min(len(athletes), 20), 2):
            row = []
            for j in range(2):
                if i + j < len(athletes):
                    a = athletes[i + j]
                    name = a.full_name.strip() if a.full_name else ""
                    if len(name) > 15:
                        name = name[:13] + "..."
                    row.append(InlineKeyboardButton(name, callback_data=f"mark_attendance_{a.id}"))
            if row:
                keyboard.append(row)

        if len(athletes) > 20:
            keyboard.append([InlineKeyboardButton(f"📝 Показано 20 из {len(athletes)}", callback_data="show_more_info")])

        keyboard.append([InlineKeyboardButton("🏠 В меню", callback_data="back_to_menu_main")])

        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            message,
            reply_markup=reply_markup,
            parse_mode='HTML'
        )

    except Exception as e:
        print(f"❌ ОШИБКА ПРИ НАЧАЛЕ ТРЕНИРОВКИ: {e}")
        await update.message.reply_text("❌ Ошибка при загрузке списка спортсменов")
    finally:
        session.close()


async def show_coach_calendar(update: Update, context: ContextTypes.DEFAULT_TYPE, month: int = None, year: int = None):
    """Показать календарь тренировок тренера с промаркированными днями и навигацией"""
    user_id = update.effective_user.id
    print(f"📅 ПОЛЬЗОВАТЕЛЬ {user_id} ЗАПРОСИЛ КАЛЕНДАРЬ ТРЕНИРОВОК")

    session = Session()
    try:
        user = get_user_by_telegram_id(session, user_id)

        if not user or user.role not in ['coach', 'admin']:
            if update.callback_query:
                await update.callback_query.answer("❌ У вас нет доступа")
            else:
                await update.message.reply_text("❌ У вас нет доступа к этому меню")
            return

        # Получаем текущую дату или используем переданные параметры
        now = datetime.utcnow()
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
        
        if user.role == 'admin':
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
        if not user or user.role not in ['coach', 'admin']:
            await query.answer("❌ У вас нет доступа")
            return
        
        # Получаем тренировки на выбранную дату
        if user.role == 'admin':
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
            if user.sport_type:
                query_filter = query_filter.filter(Training.sport_type == user.sport_type)
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
                
                # Получаем спортсменов, записанных на эту тренировку (с активными абонементами)
                # Проверяем, что дата тренировки попадает в диапазон действия абонемента
                training_date_only = training.training_date.date()
                attendances = session.query(Attendance).join(
                    Subscription, Attendance.subscription_id == Subscription.id
                ).join(
                    Athlete, Attendance.athlete_id == Athlete.id
                ).filter(
                    Attendance.training_id == training.id,
                    Subscription.is_active == True,
                    func.date(Subscription.start_date) <= training_date_only,  # Дата начала <= дата тренировки
                    func.date(Subscription.end_date) >= training_date_only,    # Дата окончания >= дата тренировки
                    Athlete.sport_type == training.sport_type,
                    Athlete.age_group == training.age_group
                ).all()
                
                if attendances:
                    message += f"  <b>Записано спортсменов: {len(attendances)}</b>\n"
                    for attendance in attendances[:10]:  # Показываем до 10 спортсменов
                        athlete = session.query(Athlete).filter_by(id=attendance.athlete_id).first()
                        if athlete:
                            status_icon = "✅" if attendance.attended else "❌"
                            message += f"    {status_icon} {athlete.full_name}\n"
                    if len(attendances) > 10:
                        message += f"    ... и еще {len(attendances) - 10}\n"
                else:
                    message += f"  Нет записанных спортсменов\n"
                
                message += "\n"
        else:
            # Если тренировок нет в базе, но есть расписание - показываем спортсменов по расписанию
            weekday = selected_date.weekday()
            sport_type = user.sport_type if user.sport_type else None
            
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
                        time_str = schedule['time']
                        hour, minute = map(int, time_str.split(':'))
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
                        if user.role == 'coach':
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