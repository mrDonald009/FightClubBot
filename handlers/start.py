from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ContextTypes
from database.models import User, Session
from database.db_utils import get_user_by_telegram_id, create_user
import logging

logger = logging.getLogger(__name__)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    user_id = update.effective_user.id
    username = update.effective_user.username or "Не указан"
    first_name = update.effective_user.first_name or "Пользователь"

    # Выводим в консоль
    print(f"🎯 ПОЛЬЗОВАТЕЛЬ {user_id} ({first_name}) ОТПРАВИЛ /start")

    # Проверяем, есть ли пользователь в базе
    session = Session()
    try:
        user = get_user_by_telegram_id(session, user_id)

        if not user:
            user = create_user(session, user_id, username, first_name, "athlete")
            welcome_text = f"""👋 Добро пожаловать, {first_name}!

Вы были зарегистрированы как спортсмен. Обратитесь к тренеру для изменения роли."""
            print(f"✅ СОЗДАН НОВЫЙ ПОЛЬЗОВАТЕЛЬ: {user_id} с ролью {user.role}")
        else:
            welcome_text = f"""👋 С возвращением, {first_name}!

Ваша роль: {user.role}"""
            print(f"🔍 ПОЛЬЗОВАТЕЛЬ {user_id} УЖЕ СУЩЕСТВУЕТ, роль: {user.role}")

        await update.message.reply_text(welcome_text)

        # Показываем соответствующее меню
        if user.role == "coach":
            print(f"🎯 ПОКАЗЫВАЕМ МЕНЮ ТРЕНЕРА ДЛЯ {user_id}")
            await show_coach_menu(update, context)
        elif user.role == "admin":
            print(f"👑 ПОКАЗЫВАЕМ МЕНЮ АДМИНА ДЛЯ {user_id}")
            await show_admin_menu(update, context)
        else:
            print(f"💪 ПОКАЗЫВАЕМ МЕНЮ СПОРТСМЕНА ДЛЯ {user_id}")
            await show_athlete_menu(update, context)

    except Exception as e:
        print(f"❌ ОШИБКА В /start: {e}")
        await update.message.reply_text("❌ Произошла ошибка. Попробуйте позже.")
    finally:
        session.close()


async def show_coach_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает меню тренера"""
    print("📋 ОТОБРАЖАЕМ МЕНЮ ТРЕНЕРА")
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


async def show_admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает меню администратора"""
    print("📋 ОТОБРАЖАЕМ МЕНЮ АДМИНИСТРАТОРА")
    keyboard = [
        [KeyboardButton("👥 Тренеры"), KeyboardButton("📊 Общая статистика")],
        [KeyboardButton("💰 Финансы"), KeyboardButton("⚙️ Настройки")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    await update.message.reply_text(
        "👑 Меню администратора",
        reply_markup=reply_markup
    )


async def show_athlete_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает меню спортсмена"""
    print("📋 ОТОБРАЖАЕМ МЕНЮ СПОРТСМЕНА")
    keyboard = [
        [KeyboardButton("📊 Мой прогресс"), KeyboardButton("📅 Расписание")],
        [KeyboardButton("🏆 Рейтинг"), KeyboardButton("ℹ️ Информация")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    await update.message.reply_text(
        "💪 Личный кабинет спортсмена",
        reply_markup=reply_markup
    )