from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ContextTypes
from core.database import get_db_session
from database.db_utils import get_user_by_telegram_id, get_user_role, create_athlete
from services.permissions import ROLE_LABEL_RU, is_coach, is_admin
from keyboards.coach_kb import get_coach_main_menu
from keyboards.admin_kb import get_admin_main_menu
import logging

logger = logging.getLogger(__name__)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    user_id = update.effective_user.id
    username = update.effective_user.username or "Не указан"
    first_name = update.effective_user.first_name or "Пользователь"

    print(f"🎯 ПОЛЬЗОВАТЕЛЬ {user_id} ({first_name}) ОТПРАВИЛ /start")
    print(f"📝 Username: {username}")

    try:
        with get_db_session() as session:
            user = get_user_by_telegram_id(session, user_id)

            if not user:
                # Для новых пользователей создаем запись спортсмена (в таблице athletes)
                # Создаем спортсмена без тренера (created_by будет NULL)
                user = create_athlete(
                    session=session,
                    telegram_id=user_id,
                    full_name=first_name,
                    phone=None,
                    medical_info="",
                    sport_type=None,
                    age_group=None,
                    created_by=None
                )
                print(f"✅ СОЗДАН НОВЫЙ СПОРТСМЕН: {user_id}")
                welcome_text = f"""👋 Добро пожаловать, {first_name}!

Вы были зарегистрированы как спортсмен. Обратитесь к тренеру для настройки профиля."""
            else:
                role = get_user_role(user)
                print(f"🔍 ПОЛЬЗОВАТЕЛЬ {user_id} УЖЕ СУЩЕСТВУЕТ, роль: {role}")
                label = ROLE_LABEL_RU.get(role, role)
                welcome_text = f"""👋 С возвращением, {first_name}!

Ваша роль: {label}"""

            await update.message.reply_text(welcome_text)

            # Показываем соответствующее меню
            if is_coach(user):
                print(f"🎯 ПОКАЗЫВАЕМ МЕНЮ ТРЕНЕРА ДЛЯ {user_id}")
                await show_coach_menu(update, context)
            elif is_admin(user):
                print(f"👑 ПОКАЗЫВАЕМ МЕНЮ АДМИНА ДЛЯ {user_id}")
                await show_admin_menu(update, context)
            else:
                print(f"💪 ПОКАЗЫВАЕМ МЕНЮ СПОРТСМЕНА ДЛЯ {user_id}")
                await show_athlete_menu(update, context)

    except Exception as e:
        print(f"❌ ОШИБКА В /start: {e}")
        await update.message.reply_text("❌ Произошла ошибка. Попробуйте позже.")
        print(f"🔚 ЗАВЕРШЕНА ОБРАБОТКА /start ДЛЯ {user_id}")


async def show_coach_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает меню тренера"""
    user_id = update.effective_user.id
    print(f"📋 ПОКАЗ МЕНЮ ТРЕНЕРА ДЛЯ {user_id}")

    await update.message.reply_text(
        "🏋️‍♂️ Меню тренера:\n\n"
        "Выберите действие.",
        reply_markup=get_coach_main_menu(),
    )
    print(f"✅ МЕНЮ ТРЕНЕРА ОТОБРАЖЕНО ДЛЯ {user_id}")


async def show_admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает меню администратора"""
    user_id = update.effective_user.id
    print(f"📋 ПОКАЗ МЕНЮ АДМИНА ДЛЯ {user_id}")

    await update.message.reply_text(
        "👑 Меню администратора\n\n"
        "Массовая заморозка клуба — кнопка «🌍 Массовая заморозка».",
        reply_markup=get_admin_main_menu(),
    )
    print(f"✅ МЕНЮ АДМИНА ОТОБРАЖЕНО ДЛЯ {user_id}")


async def show_role_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /menu: меню по роли (тренер или администратор)."""
    user_id = update.effective_user.id
    try:
        with get_db_session() as session:
            user = get_user_by_telegram_id(session, user_id)
            if is_coach(user):
                await show_coach_menu(update, context)
                return
            if is_admin(user):
                await show_admin_menu(update, context)
                return
    except Exception as e:
        logger.error("Ошибка show_role_menu: %s", e, exc_info=True)
    await update.message.reply_text(
        "❌ Меню доступно тренерам и администраторам. Спортсменам — /start."
    )


async def show_athlete_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает меню спортсмена"""
    user_id = update.effective_user.id
    print(f"📋 ПОКАЗ МЕНЮ СПОРТСМЕНА ДЛЯ {user_id}")

    query = update.callback_query
    message = update.message

    keyboard = [
        [KeyboardButton("👤 Моя карточка"), KeyboardButton("🎫 Мой абонемент")],
        [KeyboardButton("📅 Расписание"), KeyboardButton("📊 Мой прогресс")],
        [KeyboardButton("🏆 Рейтинг"), KeyboardButton("ℹ️ Информация")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    menu_text = "💪 Личный кабинет спортсмена"
    
    if query:
        await query.answer()
        await query.message.reply_text(menu_text, reply_markup=reply_markup)
    else:
        await message.reply_text(menu_text, reply_markup=reply_markup)
    
    print(f"✅ МЕНЮ СПОРТСМЕНА ОТОБРАЖЕНО ДЛЯ {user_id}")
