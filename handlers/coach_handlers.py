import logging
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes, ConversationHandler
from database.models import Session, User, Athlete, Subscription, Training, Attendance
from database.db_utils import get_user_by_telegram_id, create_athlete, create_subscription
from utils.training_manager import TrainingManager
from keyboards.coach_kb import get_coach_main_menu
from datetime import datetime, timedelta
import random
import re
import calendar


logger = logging.getLogger(__name__)

# Состояния для добавления спортсмена
(
    ATHLETE_FULL_NAME,
    ATHLETE_PHONE,
    ATHLETE_MEDICAL,
    ATHLETE_SPORT_TYPE,
    ATHLETE_AGE_GROUP,
    ATHLETE_SUBSCRIPTION,
    ATHLETE_TRAINING_DATE
) = range(7)

# Список кнопок меню для проверки прерывания
MENU_BUTTONS = [
    "👥 Добавить спортсмена",
    "📋 Список спортсменов",
    "📊 Статистика посещений",
    "💰 Финансовая статистика",
    "📅 Отметить посещение",
    "📅 Мой календарь",
    "⚙️ Настройки"
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
    print(f"✅ ВЫБРАНА ВОЗРАСТНАЯ ГРУППА: {age_group_ru} ({age_group}), ПЕРЕХОДИМ В ATHLETE_SUBSCRIPTION")

    keyboard = [[KeyboardButton("Месячный"), KeyboardButton("Разовый")]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    await update.message.reply_text(
        "🎫 Выберите тип абонемента:",
        reply_markup=reply_markup
    )
    return ATHLETE_SUBSCRIPTION


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
    
    # Если разовый абонемент - показываем выбор даты
    if subscription_type == "single":
        sport_type = context.user_data.get('sport_type')
        age_group = context.user_data.get('age_group')
        
        if not sport_type or not age_group:
            await update.message.reply_text("❌ Ошибка: не найдены данные о виде спорта или возрастной группе")
            return ConversationHandler.END
        
        keyboard = create_date_keyboard(sport_type, age_group)
        
        if not keyboard:
            # Если расписание не найдено, сообщаем об этом
            schedule_info = TrainingManager.get_training_schedule_info(sport_type, age_group)
            if not schedule_info:
                await update.message.reply_text(
                    f"❌ Расписание не найдено для вида спорта '{sport_type}' и группы '{age_group}'.\n\n"
                    "Обратитесь к администратору для настройки расписания."
                )
            else:
                await update.message.reply_text(
                    f"❌ Нет доступных дат для записи на ближайшие месяцы.\n\n"
                    f"📆 Расписание: {schedule_info['full_schedule']}\n\n"
                    "Пожалуйста, попробуйте позже или выберите месячный абонемент."
                )
            return ATHLETE_SUBSCRIPTION
        
        # Получаем информацию о расписании для сообщения
        schedule_info = TrainingManager.get_training_schedule_info(sport_type, age_group)
        schedule_text = schedule_info['full_schedule'] if schedule_info else "по расписанию"
        
        age_group_ru = "Детская" if age_group == "children" else "Взрослая"
        
        await update.message.reply_text(
            f"📅 <b>ВЫБОР ДАТЫ ТРЕНИРОВКИ</b>\n\n"
            f"🥊 Вид спорта: {sport_type}\n"
            f"👥 Группа: {age_group_ru}\n"
            f"📆 Расписание: {schedule_text}\n\n"
            f"Выберите дату для разовой тренировки:",
            reply_markup=keyboard,
            parse_mode='HTML'
        )
        
        return ATHLETE_TRAINING_DATE
    
    # Если месячный абонемент - создаем сразу
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

        subscription = create_subscription(
            session=session,
            athlete_id=athlete.id,
            subscription_type=subscription_type
        )

        # Устанавливаем текущий абонемент для спортсмена
        athlete.current_subscription_id = subscription.id
        session.commit()

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
        
        subscription = create_subscription(
            session=session,
            athlete_id=athlete.id,
            subscription_type=context.user_data['subscription_type']
        )
        
        # Устанавливаем текущий абонемент для спортсмена
        athlete.current_subscription_id = subscription.id
        session.commit()
        
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
    """Показывает упрощенный список спортсменов тренера"""
    user_id = update.effective_user.id
    print(f"📋 ПОЛЬЗОВАТЕЛЬ {user_id} ЗАПРОСИЛ СПИСОК СПОРТСМЕНОВ")

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
            message_header = "🏃‍♂️ <b>СПИСОК ВСЕХ СПОРТСМЕНОВ</b>\n\n"
        else:
            athletes = session.query(Athlete).filter_by(created_by=user.id).all()
            message_header = f"🏃‍♂️ <b>СПИСОК ВАШИХ СПОРТСМЕНОВ</b>\n\n"

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

        # Простая статистика
        total_athletes = len(athletes)
        active_count = 0
        children_count = 0

        for athlete in athletes:
            if athlete.current_subscription and athlete.current_subscription.is_active:
                active_count += 1
            if athlete.age_group == 'children':
                children_count += 1

        # Формируем сообщение
        message = message_header
        message += f"📊 <b>СТАТИСТИКА:</b>\n"
        message += f"• Всего спортсменов: {total_athletes}\n"
        message += f"• С активным абонементом: {active_count}\n"
        message += f"• Детская группа: {children_count}\n"
        message += f"• Взрослая группа: {total_athletes - children_count}\n\n"

        message += f"<b>ВЫБЕРИТЕ СПОРТСМЕНА:</b>\n"
        message += f"✅ - активный абонемент\n"
        message += f"❌ - нет абонемента\n"
        message += f"👶 - детская группа\n"
        message += f"👨‍🦰 - взрослая группа"

        # Создаем инлайн клавиатуру
        keyboard = []

        # Группируем спортсменов по 2 в строку (максимум 10 строк = 20 спортсменов)
        for i in range(0, min(len(athletes), 20), 2):
            row = []
            for j in range(2):
                if i + j < len(athletes):
                    athlete = athletes[i + j]

                    # Определяем иконки
                    icons = []

                    # Иконка активного абонемента
                    if athlete.current_subscription:
                        from utils.subscription_checker import SubscriptionChecker
                        status = SubscriptionChecker.get_subscription_status(athlete.current_subscription)

                        if status == "active":
                            icons.append("✅")
                        elif status == "expiring_soon":
                            icons.append("🟡")
                        elif status == "expired":
                            icons.append("🔴")
                        else:  # inactive или no_subscription
                            icons.append("❌")
                    else:
                        icons.append("❌")

                    # Иконка возрастной группы
                    if athlete.age_group == 'children':
                        icons.append("👶")
                    else:
                        icons.append("👨‍🦰")

                    # Сокращаем имя если длинное
                    name = athlete.full_name
                    if len(name) > 12:
                        name = name[:10] + "..."

                    btn_text = f"{''.join(icons)} {name}"
                    row.append(InlineKeyboardButton(btn_text, callback_data=f"athlete_{athlete.id}"))

            if row:
                keyboard.append(row)

        # Если спортсменов больше 20, показываем предупреждение
        if len(athletes) > 20:
            keyboard.append([
                InlineKeyboardButton(f"📝 Показано 20 из {len(athletes)}", callback_data="show_more_info")
            ])

        # Простая кнопка возврата в меню
        keyboard.append([
            InlineKeyboardButton("🏠 В меню", callback_data="back_to_menu_main")
        ])

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
        print(f"❌ ОШИБКА ПРИ ПОЛУЧЕНИИ СПИСКА СПОРТСМЕНОВ: {e}")
        error_msg = "❌ Ошибка при загрузке списка спортсменов"
        if update.callback_query:
            await update.callback_query.answer(error_msg)
        else:
            await update.message.reply_text(error_msg)
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


async def show_coach_calendar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать календарь тренировок тренера"""
    user_id = update.effective_user.id
    print(f"📅 ПОЛЬЗОВАТЕЛЬ {user_id} ЗАПРОСИЛ КАЛЕНДАРЬ ТРЕНИРОВОК")

    session = Session()
    try:
        user = get_user_by_telegram_id(session, user_id)

        if not user or user.role not in ['coach', 'admin']:
            await update.message.reply_text("❌ У вас нет доступа к этому меню")
            return

        # Получаем текущую дату
        now = datetime.utcnow()
        
        # Получаем тренировки тренера (будущие и за последний месяц)
        month_ago = now - timedelta(days=30)
        
        if user.role == 'admin':
            # Админ видит все тренировки
            trainings = session.query(Training).filter(
                Training.training_date >= month_ago,
                Training.is_cancelled == False
            ).order_by(Training.training_date.asc()).all()
            message_header = "📅 <b>КАЛЕНДАРЬ ВСЕХ ТРЕНИРОВОК</b>\n\n"
        else:
            # Тренер видит только свои тренировки
            trainings = session.query(Training).filter(
                Training.coach_id == user.id,
                Training.training_date >= month_ago,
                Training.is_cancelled == False
            ).order_by(Training.training_date.asc()).all()
            message_header = "📅 <b>МОЙ КАЛЕНДАРЬ ТРЕНИРОВОК</b>\n\n"

        if not trainings:
            await update.message.reply_text(
                "📭 У вас пока нет запланированных тренировок.\n\n"
                "Тренировки появятся здесь после их создания.",
                parse_mode='HTML'
            )
            return

        # Группируем тренировки по датам
        trainings_by_date = {}
        for training in trainings:
            date_key = training.training_date.date()
            if date_key not in trainings_by_date:
                trainings_by_date[date_key] = []
            trainings_by_date[date_key].append(training)

        # Формируем сообщение
        message = message_header
        
        # Сортируем даты
        sorted_dates = sorted(trainings_by_date.keys())
        
        # Разделяем на прошедшие и будущие
        today = now.date()
        past_trainings = [d for d in sorted_dates if d < today]
        future_trainings = [d for d in sorted_dates if d >= today]

        if future_trainings:
            message += "<b>🔜 БУДУЩИЕ ТРЕНИРОВКИ:</b>\n\n"
            for date in future_trainings:
                day_name = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"][date.weekday()]
                message += f"<b>{date.strftime('%d.%m.%Y')} ({day_name})</b>\n"
                
                for training in trainings_by_date[date]:
                    # Подсчитываем количество посетивших
                    attended_count = session.query(Attendance).filter(
                        Attendance.training_id == training.id,
                        Attendance.attended == True
                    ).count()
                    
                    # Подсчитываем общее количество записей
                    total_attendances = session.query(Attendance).filter(
                        Attendance.training_id == training.id
                    ).count()
                    
                    time_str = training.training_date.strftime('%H:%M')
                    age_group_ru = "👶 Детская" if training.age_group == "children" else "👨‍🦰 Взрослая"
                    
                    message += f"  ⏰ {time_str} | {training.sport_type} | {age_group_ru}\n"
                    message += f"     👥 Посетило: {attended_count}/{total_attendances}\n\n"
            
            message += "\n"

        if past_trainings:
            message += "<b>📜 ПРОШЕДШИЕ ТРЕНИРОВКИ (последние 30 дней):</b>\n\n"
            # Показываем только последние 10 дат из прошлого
            for date in past_trainings[-10:]:
                day_name = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"][date.weekday()]
                message += f"<b>{date.strftime('%d.%m.%Y')} ({day_name})</b>\n"
                
                for training in trainings_by_date[date]:
                    attended_count = session.query(Attendance).filter(
                        Attendance.training_id == training.id,
                        Attendance.attended == True
                    ).count()
                    
                    total_attendances = session.query(Attendance).filter(
                        Attendance.training_id == training.id
                    ).count()
                    
                    time_str = training.training_date.strftime('%H:%M')
                    age_group_ru = "👶 Детская" if training.age_group == "children" else "👨‍🦰 Взрослая"
                    
                    message += f"  ✅ {time_str} | {training.sport_type} | {age_group_ru}\n"
                    message += f"     👥 Посетило: {attended_count}/{total_attendances}\n\n"
            
            if len(past_trainings) > 10:
                message += f"\n<i>... и еще {len(past_trainings) - 10} дат</i>\n"

        # Статистика
        total_trainings = len(trainings)
        future_count = len(future_trainings)
        past_count = len(past_trainings)
        
        message += f"\n📊 <b>СТАТИСТИКА:</b>\n"
        message += f"• Всего тренировок: {total_trainings}\n"
        message += f"• Будущих: {future_count}\n"
        message += f"• Прошедших: {past_count}"

        await update.message.reply_text(
            message,
            parse_mode='HTML'
        )

    except Exception as e:
        print(f"❌ ОШИБКА ПРИ ПОЛУЧЕНИИ КАЛЕНДАРЯ: {e}")
        logger.error(f"❌ ОШИБКА ПРИ ПОЛУЧЕНИИ КАЛЕНДАРЯ: {e}", exc_info=True)
        await update.message.reply_text("❌ Ошибка при загрузке календаря")
    finally:
        session.close()