import logging
from telebot.types import ReplyKeyboardRemove
from keyboards import main_menu

logger = logging.getLogger(__name__)


def handle_start(bot, db, message):
    """Обработчик команды /start и /menu - ПРИНУДИТЕЛЬНО показываем главное меню"""
    try:
        user = message.from_user
        logger.info(f"👤 Новый пользователь: {user.id} - {user.first_name}")

        # Добавляем/обновляем пользователя в БД
        db.add_user(user.id, user.username, f"{user.first_name} {user.last_name or ''}")

        # ПРИНУДИТЕЛЬНО удаляем ВСЮ предыдущую клавиатуру
        bot.send_message(
            message.chat.id,
            "🔄 Инициализация бота...",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode='Markdown'
        )

        # Небольшая задержка для лучшего UX
        import time
        time.sleep(0.5)

        # Отправляем главное меню
        send_main_menu(bot, message.chat.id)

    except Exception as e:
        logger.error(f"❌ Ошибка в /start: {e}")
        # Даже при ошибке пытаемся показать меню
        try:
            send_main_menu(bot, message.chat.id, "❌ Произошла ошибка, но вы можете продолжить:")
        except:
            bot.reply_to(message, "❌ Критическая ошибка. Попробуйте позже.")


def send_main_menu(bot, chat_id, welcome_text=None):
    """Отправляет главное меню (универсальный метод)"""
    if welcome_text is None:
        welcome_text = """💪 *Добро пожаловать в FightClubManager!*

Я — ваш цифровой помощник в мире единоборств! 

🥊 *Что я умею:*
• Запись на тренировки
• Отслеживание прогресса  
• Челенджи и бонусы
• Расписание и цены

👇 *Выберите нужный раздел:*"""

    try:
        bot.send_message(
            chat_id,
            welcome_text,
            parse_mode='Markdown',
            reply_markup=main_menu()
        )
    except Exception as e:
        logger.error(f"❌ Ошибка отправки главного меню: {e}")
        # Фолбэк - простая текстовая команда
        bot.send_message(
            chat_id,
            "Используйте команды:\n/start - главное меню\n/menu - показать меню",
            reply_markup=ReplyKeyboardRemove()
        )