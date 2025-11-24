import telebot
import os
import sys
import datetime
import logging
import time
from telebot.types import ReplyKeyboardRemove

# Настройка логирования ДО всех импортов
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

logger.info("🚀 Инициализация бота...")

# ... существующий код ...

try:
    import config
    logger.info("✅ Конфигурация загружена")
except ImportError as e:
    logger.error(f"❌ Ошибка загрузки конфигурации: {e}")
    sys.exit(1)

# Импортируем остальные модули
try:
    from database import Database
    from handlers import (
        handle_start, send_main_menu, show_booking_days, handle_day_selection_callback,
        handle_booking_callback, show_progress_info, show_challenges_info,
        show_profile_info, show_leaderboard_info, show_my_bookings_info,
        show_schedule_info, send_prices_info, send_contacts_info, send_help_info,
        show_qr_code  # ДОБАВЛЯЕМ ЭТОТ ИМПОРТ
    )
    from keyboards import main_menu, back_to_menu_keyboard
    from handlers.trainer.auth import handle_trainer_command  # ДОБАВЛЯЕМ ЭТОТ ИМПОРТ

    logger.info("✅ Все модули успешно импортированы")
except ImportError as e:
    logger.error(f"❌ Ошибка импорта модулей: {e}")
    sys.exit(1)


def check_environment():
    """Проверяет настройки окружения для безопасности"""
    logger.info("🔒 Проверка безопасности окружения...")

    # Проверяем наличие токена
    token = os.getenv('BOT_TOKEN')
    if not token:
        logger.error("❌ BOT_TOKEN не установлен в переменных окружения")
        print("\n❌ ОШИБКА БЕЗОПАСНОСТИ: BOT_TOKEN не найден!")
        print("📝 Создайте файл .env в корне проекта с содержимым:")
        print("   BOT_TOKEN=ваш_настоящий_токен_от_BotFather")
        return False

    # Проверяем, что токен не является примером
    example_tokens = [
        '859514',
        '123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11',
        'your_bot_token_here',
        '1234567890:ABCdefGHIjklMNOpqrSTUvwxYZ123456789'
    ]

    if token in example_tokens:
        logger.error("❌ Обнаружен пример токена вместо реального")
        print("\n❌ ОШИБКА БЕЗОПАСНОСТИ: Используется пример токена!")
        print("💡 Замените токен в .env на ваш настоящий токен от @BotFather")
        return False

    # Проверяем длину токена (минимальная проверка формата)
    if len(token) < 30:
        logger.error(f"❌ Подозрительно короткий токен: {len(token)} символов")
        print(f"\n⚠️  ПРЕДУПРЕЖДЕНИЕ: Токен слишком короткий ({len(token)} символов)")

    logger.info("✅ Проверка безопасности пройдена")
    return True


# Импортируем config после настройки логирования
try:
    import config

    logger.info("✅ Конфигурация загружена")
except ImportError as e:
    logger.error(f"❌ Ошибка загрузки конфигурации: {e}")
    sys.exit(1)

# Импортируем остальные модули
try:
    from database import Database
    from handlers import (
        handle_start, send_main_menu, show_booking_days, handle_day_selection_callback,
        handle_booking_callback, show_progress_info, show_challenges_info,
        show_profile_info, show_leaderboard_info, show_my_bookings_info,
        show_schedule_info, send_prices_info, send_contacts_info, send_help_info
    )
    from keyboards import main_menu, back_to_menu_keyboard

    logger.info("✅ Все модули успешно импортированы")
except ImportError as e:
    logger.error(f"❌ Ошибка импорта модулей: {e}")
    sys.exit(1)


class FightClubBot:
    def __init__(self):
        logger.info("🔄 Инициализация FightClubBot...")
        self.config = config.config
        self.setup_database()
        self.bot = telebot.TeleBot(self.config.TOKEN)
        self.setup_handlers()
        logger.info("✅ FightClubBot инициализирован")

    def handle_trainer_callback(self, call):
        """Обработчик тренерского меню"""
        try:
            action = call.data.replace('trainer_', '')
            logger.info(f"👨‍🏫 Тренерский callback: {action}")

            if action == "qr_scanner":
                self.bot.answer_callback_query(call.id)
                self.bot.send_message(
                    call.message.chat.id,
                    "🔧 *Сканер QR-кодов в разработке*\n\n"
                    "Эта функция будет доступна в ближайшем обновлении.\n\n"
                    "📱 *Планируемый функционал:*\n"
                    "• Сканирование QR-кодов участников\n"
                    "• Автоматическая отметка посещений\n"
                    "• Проверка статуса абонемента",
                    parse_mode='Markdown'
                )
            elif action == "groups":
                self.bot.answer_callback_query(call.id)
                self.bot.send_message(
                    call.message.chat.id,
                    "🔧 *Мои группы в разработке*\n\n"
                    "Эта функция будет доступна в ближайшем обновлении.\n\n"
                    "👥 *Планируемый функционал:*\n"
                    "• Просмотр списка участников групп\n"
                    "• Статистика посещаемости\n"
                    "• Контактная информация",
                    parse_mode='Markdown'
                )
            elif action == "attendance":
                self.bot.answer_callback_query(call.id)
                self.bot.send_message(
                    call.message.chat.id,
                    "🔧 *Отметка посещений в разработке*\n\n"
                    "Эта функция будет доступна в ближайшем обновлении.\n\n"
                    "✅ *Планируемый функционал:*\n"
                    "• Ручная отметка присутствующих\n"
                    "• Учет посещений тренировок\n"
                    "• Формирование отчетов",
                    parse_mode='Markdown'
                )
            elif action == "schedule":
                self.bot.answer_callback_query(call.id)
                self.bot.send_message(
                    call.message.chat.id,
                    "🔧 *Расписание тренера в разработке*\n\n"
                    "Эта функция будет доступна в ближайшем обновлении.\n\n"
                    "📅 *Планируемый функционал:*\n"
                    "• Личное расписание тренировок\n"
                    "• Управление занятиями\n"
                    "• Уведомления об изменениях",
                    parse_mode='Markdown'
                )
            elif action == "analytics":
                self.bot.answer_callback_query(call.id)
                self.bot.send_message(
                    call.message.chat.id,
                    "🔧 *Статистика в разработке*\n\n"
                    "Эта функция будет доступна в ближайшем обновлении.\n\n"
                    "📊 *Планируемый функционал:*\n"
                    "• Аналитика посещаемости\n"
                    "• Прогресс участников\n"
                    "• Финансовые отчеты",
                    parse_mode='Markdown'
                )
            else:
                self.bot.answer_callback_query(call.id, "❌ Неизвестная команда тренера")

        except Exception as e:
            logger.error(f"Ошибка обработки тренерского callback: {e}")
            self.bot.answer_callback_query(call.id, "❌ Ошибка")

    def setup_database(self):
        """Инициализация базы данных"""
        try:
            self.db = Database()
            logger.info("✅ База данных подключена")

            # ПРИНУДИТЕЛЬНО ПЕРЕИНИЦИАЛИЗИРУЕМ БАЗУ ДАННЫХ
            logger.info("🔄 Принудительная инициализация базы данных...")
            self.db.initialize_real_data()

            # Проверяем тренеров после инициализации
            with self.db.get_connection() as conn:
                trainers = conn.execute('SELECT * FROM trainers').fetchall()
                logger.info(f"🔍 Найдено тренеров в базе: {len(trainers)}")
                for trainer in trainers:
                    logger.info(
                        f"👨‍🏫 Тренер: ID={trainer['telegram_id']}, Name={trainer['name']}, Active={trainer['is_active']}")

        except Exception as e:
            logger.error(f"❌ Ошибка настройки базы данных: {e}")
            sys.exit(1)

    def clear_old_keyboards(self, chat_id):
        """Принудительно очищает все старые клавиатуры"""
        try:
            # Отправляем сообщение с удалением клавиатуры
            self.bot.send_message(
                chat_id,
                "🔄 Очистка интерфейса...",
                reply_markup=ReplyKeyboardRemove()
            )
            time.sleep(0.3)
        except Exception as e:
            logger.warning(f"⚠️ Не удалось очистить старые клавиатуры: {e}")

    def setup_handlers(self):
        """Настройка обработчиков сообщений"""
        logger.info("🔄 Настройка обработчиков...")

        @self.bot.message_handler(commands=['start', 'menu', 'reset'])
        def send_welcome(message):
            logger.info(f"📨 Команда {message.text} от пользователя {message.from_user.id}")
            # Очищаем ВСЕ старые состояния
            self.clear_old_keyboards(message.chat.id)
            handle_start(self.bot, self.db, message)

        @self.bot.message_handler(commands=['trainer'])  # ДОБАВЛЯЕМ ЭТОТ ОБРАБОТЧИК
        def handle_trainer_command(message):
            logger.info(f"📨 Команда /trainer от пользователя {message.from_user.id}")
            from handlers.trainer.auth import handle_trainer_command as trainer_handler
            trainer_handler(self.bot, self.db, message)

        @self.bot.message_handler(commands=['schedule', 'price', 'contacts', 'help'])
        def handle_commands(message):
            logger.info(f"📨 Команда {message.text} от пользователя {message.from_user.id}")
            self.handle_quick_commands(message)

        # СПЕЦИАЛЬНЫЙ обработчик для старых сообщений "Выберите день"
        @self.bot.message_handler(func=lambda message:
        "Выберите день" in message.text or
        message.text in ["🗓️ Выберите день:", "📅 Сегодня", "📅 Завтра", "📅 Послезавтра"])
        def handle_old_messages(message):
            logger.info(f"🔄 Обработка старого сообщения: {message.text}")
            self.clear_old_keyboards(message.chat.id)
            send_main_menu(self.bot, message.chat.id, "🔄 Обновляем интерфейс...")

        @self.bot.callback_query_handler(func=lambda call: True)
        def handle_all_callbacks(call):
            logger.info(f"🖱️ Callback: {call.data} от пользователя {call.from_user.id}")
            self.handle_callback_queries(call)

        @self.bot.message_handler(content_types=['text'])
        def handle_unknown(message):
            logger.info(f"📨 Неизвестное сообщение: {message.text} от пользователя {message.from_user.id}")
            self.handle_unknown_message(message)

        logger.info("✅ Обработчики настроены")

    def handle_callback_queries(self, call):
        """Централизованный обработчик всех callback запросов"""
        try:
            if call.data.startswith('menu_'):
                self.handle_menu_callback(call)
            elif call.data.startswith('book_'):
                handle_booking_callback(self.bot, self.db, self.config, call)
            elif call.data.startswith('trainer_'):  # ДОБАВЛЯЕМ ЭТУ СТРОЧКУ
                self.handle_trainer_callback(call)
            elif call.data == "back_to_days":
                self.handle_back_callback(call)
            elif call.data == "contacts_back_to_main":
                self.handle_contacts_back_to_main(call)
            elif call.data == "return_to_main_menu":
                self.handle_return_to_main_menu(call)
            elif call.data.startswith('select_day_'):
                handle_day_selection_callback(self.bot, self.db, self.config, call)
            else:
                logger.warning(f"Неизвестный callback: {call.data}")
                self.bot.answer_callback_query(call.id, "❌ Неизвестная команда")

        except Exception as e:
            logger.error(f"Ошибка обработки callback: {e}")
            self.bot.answer_callback_query(call.id, "❌ Ошибка")

    def handle_return_to_main_menu(self, call):
        """Универсальный обработчик возврата в главное меню - СОХРАНЯЕМ ИСТОРИЮ"""
        try:
            self.bot.answer_callback_query(call.id)

            # Вместо редактирования сообщения отправляем НОВОЕ сообщение
            # Это сохраняет историю диалога
            send_main_menu(
                self.bot,
                call.message.chat.id,
                "💪 *Возвращаемся в главное меню!*\n\n👇 Выберите следующий раздел:"
            )

        except Exception as e:
            logger.error(f"Ошибка возврата в меню: {e}")
            send_main_menu(self.bot, call.message.chat.id)

    def handle_menu_callback(self, call):
        """Обработчик главного меню"""
        try:
            action = call.data.replace('menu_', '')
            logger.info(f"📋 Выбрано меню: {action}")

            handlers = {
                'booking': lambda: show_booking_days(self.bot, call.message),
                'progress': lambda: show_progress_info(self.bot, self.db, call.message),
                'challenges': lambda: show_challenges_info(self.bot, call.message),
                'profile': lambda: show_profile_info(self.bot, self.db, call.message),
                'qr_code': lambda: show_qr_code(self.bot, self.db, call.message),  # ДОБАВЛЯЕМ ЭТУ СТРОЧКУ
                'leaderboard': lambda: show_leaderboard_info(self.bot, call.message),
                'my_bookings': lambda: show_my_bookings_info(self.bot, self.db, call.message),
                'schedule': lambda: show_schedule_info(self.bot, call.message),
                'prices': lambda: send_prices_info(self.bot, self.config, call.message),
                'contacts': lambda: send_contacts_info(self.bot, self.config, call.message),
                'help': lambda: send_help_info(self.bot, call.message)
            }

            if action in handlers:
                self.bot.answer_callback_query(call.id)
                handlers[action]()
            else:
                logger.warning(f"Неизвестное действие меню: {action}")
                self.bot.answer_callback_query(call.id, "❌ Неизвестный раздел")

        except Exception as e:
            logger.error(f"Ошибка обработки меню: {e}")
            self.bot.answer_callback_query(call.id, "❌ Ошибка")
            send_main_menu(self.bot, call.message.chat.id)

    def handle_quick_commands(self, message):
        """Обработчик быстрых команд"""
        command = message.text.split('@')[0]
        handlers = {
            '/schedule': lambda: show_schedule_info(self.bot, message),
            '/price': lambda: send_prices_info(self.bot, self.config, message),
            '/contacts': lambda: send_contacts_info(self.bot, self.config, message),
            '/help': lambda: send_help_info(self.bot, message)
        }

        if command in handlers:
            handlers[command]()
        else:
            send_main_menu(self.bot, message.chat.id)

    def handle_back_callback(self, call):
        """Обработчик возврата к выбору дня"""
        try:
            # Вместо редактирования отправляем новое сообщение для сохранения истории
            self.bot.send_message(
                call.message.chat.id,
                "🗓️ *Выберите день для записи:*",
                parse_mode='Markdown'
            )
            show_booking_days(self.bot, call.message)
        except Exception as e:
            logger.error(f"Ошибка возврата: {e}")

    def handle_contacts_back_to_main(self, call):
        """Обработчик возврата из контактов в главное меню - СОХРАНЯЕМ ИСТОРИЮ"""
        try:
            self.bot.answer_callback_query(call.id)
            # Отправляем новое сообщение вместо редактирования
            send_main_menu(
                self.bot,
                call.message.chat.id,
                "💪 *Возвращаемся в главное меню!*\n\n👇 Выберите следующий раздел:"
            )
        except Exception as e:
            logger.error(f"Ошибка возврата из контактов: {e}")
            send_main_menu(self.bot, call.message.chat.id)

    def handle_unknown_message(self, message):
        """Обработчик неизвестных сообщений - ПРИНУДИТЕЛЬНО показываем главное меню"""
        # Сначала очищаем возможные старые клавиатуры
        self.clear_old_keyboards(message.chat.id)

        # Затем показываем главное меню
        send_main_menu(
            self.bot,
            message.chat.id,
            "🤖 Пожалуйста, используйте меню ниже для навигации:\n\n💡 *Доступные команды:*\n/start - главное меню\n/menu - показать меню\n/reset - сброс интерфейса"
        )

    def run(self):
        """Запуск бота"""
        logger.info("🚀 Запускаем FightClubManager...")
        try:
            logger.info("✅ Бот успешно запущен и ожидает сообщений...")
            logger.info("🤖 Бот готов к работе!")
            self.bot.infinity_polling(timeout=60, long_polling_timeout=60)
        except KeyboardInterrupt:
            logger.info("🛑 Бот остановлен пользователем")
        except Exception as e:
            logger.error(f"❌ Критическая ошибка бота: {e}")
        finally:
            self.cleanup()

    def cleanup(self):
        """Очистка ресурсов"""
        if hasattr(self, 'db'):
            self.db.backup_database()
        logger.info("✅ Очистка ресурсов завершена")


if __name__ == '__main__':
    logger.info("=" * 50)
    logger.info("🏁 START FIGHTCLUB BOT")
    logger.info("=" * 50)

    # Удаляем старый файл блокировки если есть
    lock_file = "bot.lock"
    if os.path.exists(lock_file):
        os.remove(lock_file)
        logger.info("🗑️ Удален старый файл блокировки")

    # Проверяем безопасность окружения
    if not check_environment():
        print("\n🔒 НЕОБХОДИМЫЕ ДЕЙСТВИЯ:")
        print("1. Создайте файл .env в корне проекта")
        print("2. Добавьте в него: BOT_TOKEN=ваш_настоящий_токен")
        print("3. Убедитесь, что .env добавлен в .gitignore")
        print("4. Перезапустите бота")
        sys.exit(1)

    try:
        logger.info("🔄 Создание экземпляра бота...")
        bot = FightClubBot()
        logger.info("🎯 Запуск основного цикла бота...")
        bot.run()
    except Exception as e:
        logger.error(f"❌ Не удалось запустить бота: {e}")
        import traceback

        logger.error(f"🔍 Детали ошибки: {traceback.format_exc()}")
    finally:
        if os.path.exists(lock_file):
            os.remove(lock_file)
            logger.info("✅ Файл блокировки очищен")

    logger.info("=" * 50)
    logger.info("🏁 FIGHTCLUB BOT STOPPED")
    logger.info("=" * 50)