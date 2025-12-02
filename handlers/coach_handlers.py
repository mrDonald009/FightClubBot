import logging
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ContextTypes, ConversationHandler
from database.models import Session, User, Athlete, Subscription
from database.db_utils import get_user_by_telegram_id, create_athlete, create_subscription
import random
import re

logger = logging.getLogger(__name__)

# Состояния для добавления спортсмена
(
    ATHLETE_FULL_NAME,
    ATHLETE_PHONE,
    ATHLETE_MEDICAL,
    ATHLETE_AGE_GROUP,
    ATHLETE_SUBSCRIPTION
) = range(5)

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
            print(f"❌ У ПОЛЬЗОВАТЕЛЯ {user_id} НЕТ ДОСТУПА К МЕНЮ ТРЕНЕРА")
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

    # ИЗМЕНЕННОЕ СООБЩЕНИЕ:
    await update.message.reply_text(
        "🏥 Введите медицинские противопоказания (или нажмите 'нет' если отсутствуют):"
    )
    return ATHLETE_MEDICAL


async def add_athlete_medical(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка медицинской информации"""
    user_id = update.effective_user.id
    user_text = update.message.text.strip()  # Добавил strip() для удаления пробелов
    print(f"🎯 ВХОД В add_athlete_medical ДЛЯ ПОЛЬЗОВАТЕЛЬ {user_id}, ТЕКСТ: '{user_text}'")

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
    print(f"🎯 ВХОД В add_athlete_subscription ДЛЯ ПОЛЬЗОВАТЕЛЯ {user_id}, ТЕКСТ: '{user_text}'")

    # Проверяем, не является ли ввод кнопкой меню
    if user_text in MENU_BUTTONS:
        print(f"🚫 ПОЛЬЗОВАТЕЛЬ {user_id} ПРЕРВАЛ ВЫБОР АБОНЕМЕНТА, ВЫБРАВ: {user_text}")
        await cancel_athlete_creation(update, context)
        return ConversationHandler.END

    subscription_type_ru = user_text
    subscription_type = "monthly" if subscription_type_ru == "Месячный" else "single"
    print(f"✅ ВЫБРАН ТИП АБОНЕМЕНТА: {subscription_type_ru} ({subscription_type})")

    # ОТЛАДОЧНАЯ ИНФОРМАЦИЯ: выводим все данные перед созданием
    print(f"📋 ДАННЫЕ ДЛЯ СОЗДАНИЯ СПОРТСМЕНА:")
    print(f"   ФИО: {context.user_data.get('full_name')}")
    print(f"   Телефон: {context.user_data.get('phone')}")
    print(f"   Медицинская информация: {context.user_data.get('medical_info')}")
    print(f"   Вид спорта: {context.user_data.get('sport_type')}")
    print(f"   Возрастная группа: {context.user_data.get('age_group')}")
    print(f"   ID тренера: {context.user_data.get('coach_id')}")

    session = Session()
    try:
        import random
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
            medical_info=context.user_data['medical_info'],  # ВОТ ТУТ ДОЛЖНА БЫТЬ МЕД. ИНФОРМАЦИЯ
            sport_type=context.user_data['sport_type'],
            age_group=context.user_data['age_group'],
            created_by=context.user_data['coach_id']
        )

        subscription = create_subscription(
            session=session,
            athlete_id=athlete.id,
            subscription_type=subscription_type
        )

        # Конвертируем возрастную группу для отображения
        age_group_display = "Детская" if athlete.age_group == "children" else "Взрослая"

        # Очищаем данные процесса
        context.user_data.clear()

        print(f"✅ УСПЕШНО ДОБАВЛЕН СПОРТСМЕН: {athlete.full_name}")
        print(f"   Мед. информация в БД: '{athlete.medical_info}'")  # ОТЛАДКА

        # ФИНАЛЬНОЕ СООБЩЕНИЕ С ИЗМЕНЕННЫМ ПОРЯДКОМ
        message = f"""✅ Спортсмен успешно добавлен!

        📝 ФИО: {athlete.full_name}
        📞 Телефон: {athlete.phone}
        🥊 Вид спорта: {athlete.sport_type}
        👥 Группа: {age_group_display}
        🎫 Абонемент: {subscription_type_ru}
        🏥 Мед. информация: {athlete.medical_info}
        💪 Осталось тренировок: {subscription.trainings_remaining}"""

        await update.message.reply_text(
            message,
            reply_markup=ReplyKeyboardMarkup([["/menu"]], resize_keyboard=True)
        )

    except Exception as e:
        print(f"❌ ОШИБКА ПРИ ДОБАВЛЕНИИ СПОРТСМЕНА: {e}")
        await update.message.reply_text("❌ Ошибка при добавлении спортсмена")
    finally:
        session.close()

    return ConversationHandler.END


async def athletes_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает список спортсменов тренера (только его вида спорта)"""
    user_id = update.effective_user.id
    print(f"📋 ПОЛЬЗОВАТЕЛЬ {user_id} ЗАПРОСИЛ СПИСОК СПОРТСМЕНОВ")

    session = Session()
    try:
        user = get_user_by_telegram_id(session, user_id)

        if not user or user.role not in ['coach', 'admin']:
            await update.message.reply_text("❌ У вас нет доступа к этому меню")
            return

        # Для админа - показываем всех спортсменов
        if user.role == 'admin':
            athletes = session.query(Athlete).all()
            message_header = "🏃‍♂️ <b>СПИСОК ВСЕХ СПОРТСМЕНОВ</b>\n\n"
        else:
            # Для тренера - только спортсменов его вида спорта
            athletes = session.query(Athlete).filter_by(
                created_by=user.id,
                sport_type=user.sport_type
            ).all()
            message_header = f"🏃‍♂️ <b>СПИСОК ВАШИХ СПОРТСМЕНОВ ({user.sport_type})</b>\n\n"

        if not athletes:
            await update.message.reply_text(
                "📭 У вас пока нет спортсменов.\n\n"
                "Добавьте первого спортсмена через меню '👥 Добавить спортсмена'"
            )
            return

        # Формируем сообщение со списком спортсменов
        # Формируем сообщение со списком спортсменов
        message = message_header

        for i, athlete in enumerate(athletes, 1):
            # Получаем активный абонемент спортсмена
            subscription = session.query(Subscription).filter_by(
                athlete_id=athlete.id,
                is_active=True
            ).first()

            # Определяем статус абонемента
            if subscription:
                status = f"🎫 {subscription.trainings_remaining}/{subscription.trainings_total}"
                sub_type = "Месячный" if subscription.subscription_type == "monthly" else "Разовый"
            else:
                status = "❌ Нет абонемента"
                sub_type = "—"

            # Конвертируем возрастную группу для отображения
            age_group_display = "Детская" if athlete.age_group == "children" else "Взрослая"

            # Сокращаем медицинскую информацию если слишком длинная
            medical_display = athlete.medical_info
            if medical_display and len(medical_display) > 30:
                medical_display = medical_display[:27] + "..."

            message += (
                f"{i}. <b>{athlete.full_name}</b>\n"
                f"   📞 {athlete.phone}\n"
                f"   🥊 {athlete.sport_type} | {age_group_display}\n"
                f"   🏥 Мед: {medical_display or '—'}\n"
                f"   {status} | {sub_type}\n"
                f"   🆔 ID: {athlete.id}\n\n"
            )

        message += f"📊 Всего спортсменов: <b>{len(athletes)}</b>"

        await update.message.reply_text(message, parse_mode='HTML')

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