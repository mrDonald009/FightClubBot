import logging
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes, ConversationHandler
from database.models import Session, User, Athlete, Subscription, Training, Attendance
from database.db_utils import get_user_by_telegram_id, create_athlete, create_subscription
import random
import re

logger = logging.getLogger(__name__)

# Состояния для добавления спортсмена
(
    ATHLETE_FULL_NAME,
    ATHLETE_PHONE,
    ATHLETE_MEDICAL,
    ATHLETE_SPORT_TYPE,
    ATHLETE_AGE_GROUP,
    ATHLETE_SUBSCRIPTION
) = range(6)

# Список кнопок меню для проверки прерывания
MENU_BUTTONS = [
    "👥 Добавить спортсмена",
    "📋 Список спортсменов",
    "📊 Статистика посещений",
    "💰 Финансовая статистика",
    "📅 Отметить посещение",
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

        keyboard = [
            [KeyboardButton("👥 Добавить спортсмена"), KeyboardButton("📋 Список спортсменов")],
            [KeyboardButton("📊 Статистика посещений"), KeyboardButton("💰 Финансовая статистика")],
            [KeyboardButton("📅 Отметить посещение"), KeyboardButton("⚙️ Настройки")]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

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


async def add_athlete_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка типа абонемента и завершение процесса"""
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
            reply_markup=ReplyKeyboardMarkup([["/menu"]], resize_keyboard=True)
        )

    except Exception as e:
        print(f"❌ ОШИБКА ПРИ ДОБАВЛЕНИИ СПОРТСМЕНА: {e}")
        await update.message.reply_text("❌ Ошибка при добавлении спортсмена")
    finally:
        session.close()

    return ConversationHandler.END


async def athletes_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает список спортсменов тренера с интерактивными кнопками"""
    user_id = update.effective_user.id
    print(f"📋 ПОЛЬЗОВАТЕЛЬ {user_id} ЗАПРОСИЛ СПИСОК СПОРТСМЕНОВ")

    session = Session()
    try:
        user = get_user_by_telegram_id(session, user_id)

        if not user or user.role not in ['coach', 'admin']:
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
            await update.message.reply_text(
                "📭 У вас пока нет спортсменов.\n\n"
                "Добавьте первого спортсмена через меню '👥 Добавить спортсмена'"
            )
            return

        # Создаем инлайн клавиатуру
        keyboard = []

        # Группируем спортсменов по 2 в строку
        for i in range(0, len(athletes), 2):
            row = []
            for j in range(2):
                if i + j < len(athletes):
                    athlete = athletes[i + j]

                    # Определяем иконку статуса
                    if athlete.current_subscription and athlete.current_subscription.is_active:
                        icon = "✅"
                    else:
                        icon = "❌"

                    # Сокращаем имя если длинное
                    name = athlete.full_name
                    if len(name) > 15:
                        name = name[:12] + "..."

                    btn_text = f"{icon} {name}"
                    row.append(InlineKeyboardButton(btn_text, callback_data=f"athlete_{athlete.id}"))

            if row:
                keyboard.append(row)

        # Добавляем кнопки навигации
        keyboard.append([
            InlineKeyboardButton("🔍 Поиск спортсмена", callback_data="search_athlete"),
            InlineKeyboardButton("📊 Общая статистика", callback_data="overall_stats")
        ])

        keyboard.append([
            InlineKeyboardButton("🏠 В меню", callback_data="back_to_menu_main")
        ])

        reply_markup = InlineKeyboardMarkup(keyboard)

        # Отправляем сообщение
        await update.message.reply_text(
            message_header +
            f"Выберите спортсмена для просмотра карточки:\n"
            f"✅ - есть активный абонемент\n"
            f"❌ - нет активного абонемента\n\n"
            f"📊 Всего спортсменов: <b>{len(athletes)}</b>",
            reply_markup=reply_markup,
            parse_mode='HTML'
        )

    except Exception as e:
        print(f"❌ ОШИБКА ПРИ ПОЛУЧЕНИИ СПИСКА СПОРТСМЕНОВ: {e}")
        await update.message.reply_text("❌ Ошибка при загрузке списка спортсменов")
    finally:
        session.close()


async def cancel_athlete_creation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отмена процесса добавления спортсмена"""
    user_id = update.effective_user.id
    print(f"🚫 ПОЛЬЗОВАТЕЛЬ {user_id} ОТМЕНИЛ ДОБАВЛЕНИЕ СПОРТСМЕНА")

    # Очищаем данные процесса
    context.user_data.clear()

    await update.message.reply_text(
        "❌ Добавление спортсмена отменено.\n\n"
        "Выберите действие из меню:",
        reply_markup=ReplyKeyboardMarkup([
            [KeyboardButton("👥 Добавить спортсмена"), KeyboardButton("📋 Список спортсменов")],
            [KeyboardButton("📊 Статистика посещений"), KeyboardButton("💰 Финансовая статистика")],
            [KeyboardButton("📅 Отметить посещение"), KeyboardButton("⚙️ Настройки")]
        ], resize_keyboard=True)
    )

    return ConversationHandler.END