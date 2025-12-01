from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ContextTypes, ConversationHandler
from database.models import Session
from database.db_utils import get_user_by_telegram_id, create_user


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    user = update.effective_user
    session = Session()

    try:
        # Проверяем, есть ли пользователь в базе
        db_user = get_user_by_telegram_id(session, user.id)

        if not db_user:
            # Создаем нового пользователя (по умолчанию спортсмен)
            db_user = create_user(
                session=session,
                telegram_id=user.id,
                username=user.username,
                first_name=user.first_name,
                role='athlete'
            )

        # Показываем меню в зависимости от роли
        if db_user.role == 'coach':
            await show_coach_menu(update, context)
        else:
            await show_athlete_menu(update, context)

    except Exception as e:
        await update.message.reply_text(
            "❌ Произошла ошибка. Попробуйте еще раз или обратитесь к администратору."
        )
        print(f"Ошибка в start: {e}")
    finally:
        session.close()


async def show_coach_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать меню тренера"""
    keyboard = [
        ["👥 Добавить спортсмена", "📋 Список спортсменов"],
        ["📅 Отметить посещение", "📊 Статистика посещений"],
        ["💰 Финансовая статистика", "⚙️ Настройки"]
    ]

    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    if update.message:
        await update.message.reply_text(
            "🏋️ *Панель тренера*\n\n"
            "Выберите действие:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    elif update.callback_query:
        await update.callback_query.message.reply_text(
            "🏋️ *Панель тренера*\n\n"
            "Выберите действие:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )


async def show_athlete_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать меню спортсмена"""
    keyboard = [
        ["📅 Мои тренировки", "💳 Мои платежи"],
        ["👤 Мой профиль", "📞 Связаться с тренером"]
    ]

    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    await update.message.reply_text(
        "🥊 *Панель спортсмена*\n\n"
        "Выберите действие:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )