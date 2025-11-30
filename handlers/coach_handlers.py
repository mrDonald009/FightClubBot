from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ContextTypes, ConversationHandler
from database.models import Session, User
from database.db_utils import get_user_by_telegram_id, create_athlete, create_subscription
import logging
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
    print(f"🎯 ВЫЗВАН add_athlete_start ДЛЯ ПОЛЬЗОВАТЕЛЯ {user_id}")

    session = Session()
    try:
        user = get_user_by_telegram_id(session, user_id)
        print(f"🔍 ПОЛЬЗОВАТЕЛЬ В БАЗЕ: {user}, РОЛЬ: {user.role if user else 'None'}")

        if not user or user.role not in ['coach', 'admin']:
            print(f"❌ У ПОЛЬЗОВАТЕЛЯ {user_id} НЕТ ПРАВ ДОБАВЛЯТЬ СПОРТСМЕНОВ")
            await update.message.reply_text("❌ У вас нет прав для добавления спортсменов")
            return ConversationHandler.END

        await update.message.reply_text(
            "👤 <b>Добавление нового спортсмена</b>\n\n"
            "Введите ФИО спортсмена:",
            parse_mode='HTML'
        )

        context.user_data['coach_id'] = user.id
        print(f"✅ УСТАНОВЛЕНО СОСТОЯНИЕ ATHLETE_FULL_NAME ДЛЯ {user_id}")
        return ATHLETE_FULL_NAME

    except Exception as e:
        print(f"❌ ОШИБКА В add_athlete_start: {e}")
        await update.message.reply_text("❌ Произошла ошибка")
        return ConversationHandler.END
    finally:
        session.close()


async def add_athlete_full_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка ФИО спортсмена"""
    user_text = update.message.text

    # Проверяем, не является ли ввод кнопкой меню
    if user_text in MENU_BUTTONS:
        print(f"🚫 ПОЛЬЗОВАТЕЛЬ {update.effective_user.id} ПРЕРВАЛ ВВОД ФИО, ВЫБРАВ: {user_text}")
        await cancel_athlete_creation(update, context)
        return ConversationHandler.END

    full_name = user_text
    context.user_data['full_name'] = full_name
    print(f"📝 ВВЕДЕНО ФИО: {full_name}")

    await update.message.reply_text(
        "📞 Введите номер телефона спортсмена в формате:\n"
        "XXX-XXX-XX-XX\n\n"
        "Пример: 925-123-45-67"
    )
    return ATHLETE_PHONE


async def add_athlete_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка телефона спортсмена"""
    user_text = update.message.text

    # Проверяем, не является ли ввод кнопкой меню
    if user_text in MENU_BUTTONS:
        print(f"🚫 ПОЛЬЗОВАТЕЛЬ {update.effective_user.id} ПРЕРВАЛ ВВОД ТЕЛЕФОНА, ВЫБРАВ: {user_text}")
        await cancel_athlete_creation(update, context)
        return ConversationHandler.END

    # Удаляем все нецифровые символы кроме дефисов
    cleaned_input = re.sub(r'[^\d-]', '', user_text)

    # Проверяем формат: XXX-XXX-XX-XX (9 цифр с дефисами)
    phone_pattern = r'^\d{3}-\d{3}-\d{2}-\d{2}$'

    if not re.match(phone_pattern, cleaned_input):
        # Если формат неверный, показываем пример
        error_message = """❌ Неверный формат!

📞 Правильный формат: <b>XXX-XXX-XX-XX</b>
Пример: <code>925-123-45-67</code>

Пожалуйста, введите телефон в правильном формате:"""

        await update.message.reply_text(error_message, parse_mode='HTML')
        return ATHLETE_PHONE

    # Если формат правильный - сохраняем полный номер
    full_phone = f"+7-{cleaned_input}"
    context.user_data['phone'] = full_phone
    print(f"📞 ВВЕДЕН ТЕЛЕФОН: {full_phone}")

    await update.message.reply_text(
        "🏥 Введите медицинские противопоказания (или 'нет' если отсутствуют):"
    )
    return ATHLETE_MEDICAL


async def add_athlete_medical(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка медицинской информации"""
    user_text = update.message.text

    # Проверяем, не является ли ввод кнопкой меню
    if user_text in MENU_BUTTONS:
        print(f"🚫 ПОЛЬЗОВАТЕЛЬ {update.effective_user.id} ПРЕРВАЛ ВВОД МЕД.ДАННЫХ, ВЫБРАВ: {user_text}")
        await cancel_athlete_creation(update, context)
        return ConversationHandler.END

    medical_info = user_text
    context.user_data['medical_info'] = medical_info
    print(f"🏥 ВВЕДЕНЫ МЕД.ДАННЫЕ: {medical_info}")

    keyboard = [[KeyboardButton("MMA"), KeyboardButton("Тайский Бокс")]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    await update.message.reply_text(
        "🥊 Выберите вид спорта:",
        reply_markup=reply_markup
    )
    return ATHLETE_SPORT_TYPE


async def add_athlete_sport_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка вида спорта"""
    user_text = update.message.text

    # Проверяем, не является ли ввод кнопкой меню
    if user_text in MENU_BUTTONS:
        print(f"🚫 ПОЛЬЗОВАТЕЛЬ {update.effective_user.id} ПРЕРВАЛ ВЫБОР ВИДА СПОРТА, ВЫБРАВ: {user_text}")
        await cancel_athlete_creation(update, context)
        return ConversationHandler.END

    sport_type = user_text
    context.user_data['sport_type'] = sport_type
    print(f"🥊 ВЫБРАН ВИД СПОРТА: {sport_type}")

    keyboard = [[KeyboardButton("Детская"), KeyboardButton("Взрослая")]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    await update.message.reply_text(
        "👦👨 Выберите возрастную группу:",
        reply_markup=reply_markup
    )
    return ATHLETE_AGE_GROUP


async def add_athlete_age_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка возрастной группы"""
    user_text = update.message.text

    # Проверяем, не является ли ввод кнопкой меню
    if user_text in MENU_BUTTONS:
        print(f"🚫 ПОЛЬЗОВАТЕЛЬ {update.effective_user.id} ПРЕРВАЛ ВЫБОР ВОЗРАСТНОЙ ГРУППЫ, ВЫБРАВ: {user_text}")
        await cancel_athlete_creation(update, context)
        return ConversationHandler.END

    age_group_ru = user_text
    age_group = "children" if age_group_ru == "Детская" else "adults"
    context.user_data['age_group'] = age_group
    print(f"👥 ВЫБРАНА ВОЗРАСТНАЯ ГРУППА: {age_group_ru} ({age_group})")

    keyboard = [[KeyboardButton("Месячный"), KeyboardButton("Разовый")]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    await update.message.reply_text(
        "🎫 Выберите тип абонемента:",
        reply_markup=reply_markup
    )
    return ATHLETE_SUBSCRIPTION


async def add_athlete_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка типа абонемента и завершение процесса"""
    user_text = update.message.text

    # Проверяем, не является ли ввод кнопкой меню
    if user_text in MENU_BUTTONS:
        print(f"🚫 ПОЛЬЗОВАТЕЛЬ {update.effective_user.id} ПРЕРВАЛ ВЫБОР АБОНЕМЕНТА, ВЫБРАВ: {user_text}")
        await cancel_athlete_creation(update, context)
        return ConversationHandler.END

    subscription_type_ru = user_text
    subscription_type = "monthly" if subscription_type_ru == "Месячный" else "single"
    print(f"🎫 ВЫБРАН ТИП АБОНЕМЕНТА: {subscription_type_ru} ({subscription_type})")

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

        # Конвертируем возрастную группу для отображения (без "гр.")
        age_group_display = "Детская" if athlete.age_group == "children" else "Взрослая"

        context.user_data.clear()

        print(f"✅ УСПЕШНО ДОБАВЛЕН СПОРТСМЕН: {athlete.full_name}")

        await update.message.reply_text(
            f"✅ Спортсмен успешно добавлен!\n\n"
            f"📝 ФИО: {athlete.full_name}\n"
            f"📞 Телефон: {athlete.phone}\n"
            f"🥊 Вид спорта: {athlete.sport_type}\n"
            f"👥 Группа: {age_group_display}\n"
            f"🎫 Абонемент: {subscription_type_ru}\n"
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
    """Показывает список спортсменов тренера"""
    user_id = update.effective_user.id
    print(f"📋 ПОЛЬЗОВАТЕЛЬ {user_id} ЗАПРОСИЛ СПИСОК СПОРТСМЕНОВ")

    session = Session()
    try:
        user = get_user_by_telegram_id(session, user_id)

        if not user or user.role not in ['coach', 'admin']:
            await update.message.reply_text("❌ У вас нет доступа к этому меню")
            return

        # Получаем спортсменов, добавленных этим тренером
        from database.models import Athlete, Subscription
        athletes = session.query(Athlete).filter_by(created_by=user.id).all()

        if not athletes:
            await update.message.reply_text(
                "📭 У вас пока нет спортсменов.\n\n"
                "Добавьте первого спортсмена через меню '👥 Добавить спортсмена'"
            )
            return

        # Формируем сообщение со списком спортсменов
        message = "🏃‍♂️ <b>СПИСОК ВАШИХ СПОРТСМЕНОВ</b>\n\n"

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

            # Конвертируем возрастную группу для отображения (без "гр.")
            age_group_display = "Детская" if athlete.age_group == "children" else "Взрослая"

            message += (
                f"{i}. <b>{athlete.full_name}</b>\n"
                f"   📞 {athlete.phone}\n"
                f"   🥊 {athlete.sport_type} | {age_group_display}\n"
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
    """Отмена процесса добавления спортсмена и переход в выбранное меню"""
    user_id = update.effective_user.id
    user_message = update.message.text
    print(f"🚫 ПОЛЬЗОВАТЕЛЬ {user_id} ПРЕРВАЛ СОЗДАНИЕ СПОРТСМЕНА, ВЫБРАНО: {user_message}")

    # Очищаем данные процесса
    context.user_data.clear()
    print(f"🧹 ДАННЫЕ ПРОЦЕССА ОЧИЩЕНЫ ДЛЯ {user_id}")

    # Определяем, какое действие выбрал пользователь и переходим в соответствующее окно
    if user_message == "👥 Добавить спортсмена":
        print(f"👥 ПЕРЕЗАПУСК ДОБАВЛЕНИЯ СПОРТСМЕНА ДЛЯ {user_id}")
        # Начинаем процесс добавления заново
        return await add_athlete_start(update, context)
    elif user_message == "📋 Список спортсменов":
        print(f"📋 ПЕРЕХОД В СПИСОК СПОРТСМЕНОВ ДЛЯ {user_id}")
        await athletes_list(update, context)
    elif user_message == "📊 Статистика посещений":
        print(f"📊 ПЕРЕХОД В СТАТИСТИКУ ПОСЕЩЕНИЙ ДЛЯ {user_id}")
        await update.message.reply_text("📊 Функция статистики посещений в разработке")
    elif user_message == "💰 Финансовая статистика":
        print(f"💰 ПЕРЕХОД В ФИНАНСОВУЮ СТАТИСТИКУ ДЛЯ {user_id}")
        await update.message.reply_text("💰 Функция финансовой статистики в разработке")
    elif user_message == "📅 Отметить посещение":
        print(f"📅 ПЕРЕХОД В ОТМЕТКУ ПОСЕЩЕНИЙ ДЛЯ {user_id}")
        await update.message.reply_text("📅 Функция отметки посещений в разработке")
    elif user_message == "⚙️ Настройки":
        print(f"⚙️ ПЕРЕХОД В НАСТРОЙКИ ДЛЯ {user_id}")
        await update.message.reply_text("⚙️ Функция настроек в разработке")
    elif user_message in ["/menu", "/start", "/cancel"]:
        print(f"🏠 ПЕРЕХОД В ГЛАВНОЕ МЕНЮ ДЛЯ {user_id}")
        await coach_menu(update, context)
    else:
        print(f"🏠 ПЕРЕХОД В ГЛАВНОЕ МЕНЮ ПО УМОЛЧАНИЮ ДЛЯ {user_id}")
        await coach_menu(update, context)

    print(f"✅ ПРОЦЕСС ДОБАВЛЕНИЯ СПОРТСМЕНА ПРЕРВАН ДЛЯ {user_id}")
    return ConversationHandler.END