import telebot
import os
import sys
import datetime
import logging
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

# Импортируем config ПЕРВЫМ, чтобы загрузились переменные окружения
import config
from database import Database

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


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
        print("\n💡 Пример правильного .env файла:")
        print("   BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrSTUvwxYZ123456789")
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
        print("🔒 Никогда не используйте примеры токенов в продакшене!")
        return False

    # Проверяем длину токена (минимальная проверка формата)
    if len(token) < 30:
        logger.error(f"❌ Подозрительно короткий токен: {len(token)} символов")
        print(f"\n⚠️  ПРЕДУПРЕЖДЕНИЕ: Токен слишком короткий ({len(token)} символов)")
        print("   Убедитесь, что используете правильный токен от @BotFather")

    logger.info("✅ Проверка безопасности пройдена")
    return True


class FightClubBot:
    def __init__(self):
        self.config = config.config
        self.setup_database()
        self.bot = telebot.TeleBot(self.config.TOKEN)
        self.setup_handlers()

    def setup_database(self):
        """Инициализация базы данных"""
        try:
            self.db = Database()
            logger.info("✅ База данных подключена")

            # Проверяем и инициализируем данные если нужно
            test_date = datetime.datetime.now().strftime('%Y-%m-%d')
            workouts = self.db.get_workouts_by_date(test_date)

            if len(workouts) == 0:
                logger.info("🔄 Инициализируем базу данных с реальными данными...")
                self.db.initialize_real_data()

        except Exception as e:
            logger.error(f"❌ Ошибка настройки базы данных: {e}")
            sys.exit(1)

    def setup_handlers(self):
        """Настройка обработчиков сообщений"""

        @self.bot.message_handler(commands=['start', 'menu'])
        def send_welcome(message):
            self.handle_start(message)

        @self.bot.message_handler(commands=['schedule', 'price', 'contacts', 'help'])
        def handle_commands(message):
            self.handle_quick_commands(message)

        @self.bot.callback_query_handler(func=lambda call: True)
        def handle_all_callbacks(call):
            self.handle_callback_queries(call)

        @self.bot.message_handler(content_types=['text'])
        def handle_unknown(message):
            self.handle_unknown_message(message)

    def handle_callback_queries(self, call):
        """Централизованный обработчик всех callback запросов"""
        try:
            if call.data.startswith('menu_'):
                self.handle_menu_callback(call)
            elif call.data.startswith('book_'):
                self.handle_booking_callback(call)
            elif call.data == "back_to_days":
                self.handle_back_callback(call)
            elif call.data == "contacts_back_to_main":
                self.handle_contacts_back_to_main(call)
            elif call.data == "return_to_main_menu":
                self.handle_return_to_main_menu(call)
            elif call.data.startswith('select_day_'):
                self.handle_day_selection_callback(call)
            else:
                logger.warning(f"Неизвестный callback: {call.data}")
                self.bot.answer_callback_query(call.id, "❌ Неизвестная команда")

        except Exception as e:
            logger.error(f"Ошибка обработки callback: {e}")
            self.bot.answer_callback_query(call.id, "❌ Ошибка")

    def handle_return_to_main_menu(self, call):
        """Универсальный обработчик возврата в главное меню"""
        try:
            self.bot.answer_callback_query(call.id)

            # Пытаемся отредактировать сообщение, если это возможно
            try:
                self.bot.edit_message_text(
                    "💪 *Добро пожаловать в FightClubManager!*\n\nВыберите нужный раздел:",
                    call.message.chat.id,
                    call.message.message_id,
                    parse_mode='Markdown',
                    reply_markup=self.main_menu()
                )
            except:
                # Если не удалось отредактировать, отправляем новое сообщение
                self.bot.send_message(
                    call.message.chat.id,
                    "💪 *Добро пожаловать в FightClubManager!*\n\nВыберите нужный раздел:",
                    parse_mode='Markdown',
                    reply_markup=self.main_menu()
                )

        except Exception as e:
            logger.error(f"Ошибка возврата в меню: {e}")
            self.send_main_menu(call.message.chat.id)

    def run(self):
        """Запуск бота"""
        logger.info("🚀 Запускаем FightClubManager...")
        try:
            logger.info("✅ Бот успешно запущен и ожидает сообщений...")
            self.bot.polling(none_stop=True, interval=0)
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

    # Клавиатуры
    def main_menu(self):
        """Главное меню"""
        keyboard = InlineKeyboardMarkup(row_width=2)
        buttons = [
            ("🥊 Записаться на тренировку", "menu_booking"),
            ("📊 Мой прогресс", "menu_progress"),
            ("🎯 Челенджи и бонусы", "menu_challenges"),
            ("👤 Мой профиль", "menu_profile"),
            ("🏆 Таблица лидеров", "menu_leaderboard"),
            ("📅 Мои записи", "menu_my_bookings"),
            ("📋 Расписание", "menu_schedule"),
            ("💰 Цены", "menu_prices"),
            ("📞 Контакты", "menu_contacts"),
            ("ℹ️ Помощь", "menu_help")
        ]

        # Добавляем кнопки парами
        for i in range(0, len(buttons), 2):
            if i + 1 < len(buttons):
                keyboard.row(
                    InlineKeyboardButton(buttons[i][0], callback_data=buttons[i][1]),
                    InlineKeyboardButton(buttons[i + 1][0], callback_data=buttons[i + 1][1])
                )
            else:
                keyboard.add(InlineKeyboardButton(buttons[i][0], callback_data=buttons[i][1]))

        return keyboard

    def days_selection_keyboard(self):
        """Инлайн-клавиатура для выбора дня (как в контактах)"""
        today = datetime.datetime.now()
        tomorrow = today + datetime.timedelta(days=1)
        day_after_tomorrow = today + datetime.timedelta(days=2)

        keyboard = InlineKeyboardMarkup(row_width=1)

        # Добавляем кнопки выбора дней
        keyboard.add(
            InlineKeyboardButton(
                f"📅 Сегодня ({today.strftime('%d.%m')})",
                callback_data="select_day_0"
            ),
            InlineKeyboardButton(
                f"📅 Завтра ({tomorrow.strftime('%d.%m')})",
                callback_data="select_day_1"
            ),
            InlineKeyboardButton(
                f"📅 Послезавтра ({day_after_tomorrow.strftime('%d.%m')})",
                callback_data="select_day_2"
            )
        )

        # Кнопка возврата в меню
        keyboard.add(
            InlineKeyboardButton("🔙 Главное меню", callback_data="return_to_main_menu")
        )

        return keyboard

    def back_to_menu_keyboard(self):
        """Клавиатура с кнопкой возврата в меню"""
        keyboard = InlineKeyboardMarkup()
        keyboard.add(InlineKeyboardButton("🔙 Главное меню", callback_data="return_to_main_menu"))
        return keyboard

    def back_to_days_keyboard(self):
        """Клавиатура для возврата к выбору дня"""
        keyboard = InlineKeyboardMarkup()
        keyboard.add(InlineKeyboardButton("🔙 Выбрать другой день", callback_data="menu_booking"))
        return keyboard

    # Основные обработчики
    def handle_start(self, message):
        """Обработчик команды /start и /menu"""
        try:
            user = message.from_user
            self.db.add_user(user.id, user.username, f"{user.first_name} {user.last_name or ''}")

            self.send_main_menu(message.chat.id)

        except Exception as e:
            logger.error(f"Ошибка в /start: {e}")
            self.bot.reply_to(message, "❌ Произошла ошибка. Попробуйте позже.")

    def send_main_menu(self, chat_id, welcome_text=None):
        """Отправляет главное меню (универсальный метод)"""
        if welcome_text is None:
            welcome_text = "💪 *Добро пожаловать в FightClubManager!*\n\nЯ — ваш цифровой помощник в мире единоборств! Выберите нужный раздел:"

        self.bot.send_message(chat_id, welcome_text,
                              parse_mode='Markdown',
                              reply_markup=self.main_menu())

    def handle_menu_callback(self, call):
        """Обработчик главного меню"""
        try:
            action = call.data.replace('menu_', '')
            handlers = {
                'booking': self.show_booking_days,
                'progress': self.show_progress_info,
                'challenges': self.show_challenges_info,
                'profile': self.show_profile_info,
                'leaderboard': self.show_leaderboard_info,
                'my_bookings': self.show_my_bookings_info,
                'schedule': self.show_schedule_info,
                'prices': self.send_prices_info,
                'contacts': self.send_contacts_info,
                'help': self.send_help_info
            }

            if action in handlers:
                self.bot.answer_callback_query(call.id)
                handlers[action](call.message)

        except Exception as e:
            logger.error(f"Ошибка обработки меню: {e}")
            self.bot.answer_callback_query(call.id, "❌ Ошибка")
            self.send_main_menu(call.message.chat.id)

    def handle_quick_commands(self, message):
        """Обработчик быстрых команд"""
        command = message.text.split('@')[0]
        handlers = {
            '/schedule': self.show_schedule_info,
            '/price': self.send_prices_info,
            '/contacts': self.send_contacts_info,
            '/help': self.send_help_info
        }

        if command in handlers:
            handlers[command](message)
        else:
            self.send_main_menu(message.chat.id)

    def show_booking_days(self, message):
        """Показ выбора дней для записи через инлайн-кнопки"""
        booking_text = "🗓️ *Выберите день для записи на тренировку:*\n\n💡 Доступны ближайшие 3 дня:"

        # Если это callback (нажатие из меню), редактируем сообщение
        if hasattr(message, 'message_id'):
            self.bot.edit_message_text(
                booking_text,
                message.chat.id,
                message.message_id,
                parse_mode='Markdown',
                reply_markup=self.days_selection_keyboard()
            )
        else:
            # Если обычное сообщение, отправляем новое
            self.bot.send_message(
                message.chat.id,
                booking_text,
                parse_mode='Markdown',
                reply_markup=self.days_selection_keyboard()
            )

    def handle_day_selection_callback(self, call):
        """Обработчик выбора дня через инлайн-кнопки"""
        try:
            self.bot.answer_callback_query(call.id)

            # Получаем смещение дней из callback_data
            days_offset = int(call.data.replace('select_day_', ''))
            selected_date = datetime.datetime.now() + datetime.timedelta(days=days_offset)
            date_str = selected_date.strftime('%Y-%m-%d')

            workouts = self.db.get_workouts_by_date(date_str)

            if not workouts:
                self._send_no_workouts_message(call.message, selected_date)
                return

            self._send_workouts_list(call.message, workouts, selected_date)

        except Exception as e:
            logger.error(f"Ошибка выбора дня: {e}")
            self.bot.answer_callback_query(call.id, "❌ Ошибка выбора дня")
            self.send_main_menu(call.message.chat.id)

    def show_progress_info(self, message):
        """Показ прогресса пользователя"""
        try:
            stats = self.db.get_user_stats(message.from_user.id)
            progress_text = self._format_progress_text(stats)

            if hasattr(message, 'message_id'):
                self.bot.edit_message_text(
                    progress_text,
                    message.chat.id,
                    message.message_id,
                    parse_mode='Markdown',
                    reply_markup=self.back_to_menu_keyboard()
                )
            else:
                self.bot.send_message(message.chat.id, progress_text, parse_mode='Markdown',
                                      reply_markup=self.back_to_menu_keyboard())

        except Exception as e:
            logger.error(f"Ошибка загрузки прогресса: {e}")
            self.bot.send_message(message.chat.id, "❌ Ошибка загрузки прогресса")
            self.send_main_menu(message.chat.id)

    def _format_progress_text(self, stats):
        """Форматирует текст прогресса"""
        return f"""📊 *ВАШ ПРОГРЕСС*

🎯 Посещений всего: *{stats['total_workouts']}*
📈 Текущая серия: *{stats['current_streak']} дней*
🔥 Сожжено калорий: *~{stats['total_workouts'] * 500} ккал*

🏆 *ДОСТИЖЕНИЯ:*
{'✅' if stats['total_workouts'] >= 5 else '⏳'} Новичок (5 тренировок)
{'✅' if stats['current_streak'] >= 3 else '⏳'} Стабильность (3 дня подряд)
{'✅' if stats['total_workouts'] >= 10 else '⏳'} Боец (10 тренировок)"""

    def show_challenges_info(self, message):
        """Показ челенджей"""
        challenges_text = """🎯 *АКТИВНЫЕ ЧЕЛЛЕНДЖИ:*

🔥 *СИЛА ВОЛИ* - 0/7 дней
Посещайте тренировки 7 дней подряд
🎁 *Награда:* 1 бесплатная тренировка

👥 *ПРИВЕДИ ДРУГА*
Приведите друга и получите:
• 2 бесплатных занятия  
• Совместную тренировку с тренером
🎁 *Награда:* 2 бесплатных занятия

🏆 *МАРАФОН 30 ДНЕЙ*
Посещайте тренировки 30 дней подряд
🎁 *Награда:* Месячный абонемент в подарок

💪 *Участвуйте и получайте бонусы!*"""

        if hasattr(message, 'message_id'):
            self.bot.edit_message_text(
                challenges_text,
                message.chat.id,
                message.message_id,
                parse_mode='Markdown',
                reply_markup=self.back_to_menu_keyboard()
            )
        else:
            self.bot.send_message(message.chat.id, challenges_text, parse_mode='Markdown',
                                  reply_markup=self.back_to_menu_keyboard())

    def show_profile_info(self, message):
        """Показ профиля пользователя"""
        try:
            user_id = message.from_user.id
            profile = self.db.get_user_profile(user_id)

            profile_text = f"""👤 *ВАШ ПРОФИЛЬ*

*Имя:* {profile['full_name']}
*Телеграм:* @{profile['username']}
*Дата регистрации:* {profile['registration_date']}

📞 *Контакты зала:*
{self.config.GYM_ADDRESS}
{self.config.GYM_PHONE}"""

            if hasattr(message, 'message_id'):
                self.bot.edit_message_text(
                    profile_text,
                    message.chat.id,
                    message.message_id,
                    parse_mode='Markdown',
                    reply_markup=self.back_to_menu_keyboard()
                )
            else:
                self.bot.send_message(message.chat.id, profile_text, parse_mode='Markdown',
                                      reply_markup=self.back_to_menu_keyboard())

        except Exception as e:
            logger.error(f"Ошибка загрузки профиля: {e}")
            self.bot.send_message(message.chat.id, "❌ Ошибка загрузки профиля")
            self.send_main_menu(message.chat.id)

    def show_leaderboard_info(self, message):
        """Показ таблицы лидеров"""
        leaderboard_text = """🏆 *ТАБЛИЦА ЛИДЕРОВ* | Этот месяц

🥇 Алексей П. - *12 тренировок*
🥈 Мария К. - *11 тренировок*  
🥉 Дмитрий С. - *10 тренировок*
4. Анна М. - *9 тренировок*
5. Сергей В. - *8 тренировок*

*Ваше место:* входите в топ-10!

💪 *Следующая цель:* 15 тренировок в месяце"""

        if hasattr(message, 'message_id'):
            self.bot.edit_message_text(
                leaderboard_text,
                message.chat.id,
                message.message_id,
                parse_mode='Markdown',
                reply_markup=self.back_to_menu_keyboard()
            )
        else:
            self.bot.send_message(message.chat.id, leaderboard_text, parse_mode='Markdown',
                                  reply_markup=self.back_to_menu_keyboard())

    def show_my_bookings_info(self, message):
        """Показ активных записей пользователя"""
        try:
            user_id = message.from_user.id
            bookings = self.db.get_user_bookings(user_id)

            if not bookings:
                bookings_text = "📭 *У вас нет активных записей на тренировки*"
            else:
                bookings_text = "📅 *ВАШИ ЗАПИСИ:*\n\n"
                for booking in bookings:
                    bookings_text += f"📅 *{booking['date']}* в *{booking['time']}*\n"
                    bookings_text += f"🥊 {booking['workout_name']}\n"
                    bookings_text += f"👨‍🏫 Тренер: {booking['trainer']}\n"
                    bookings_text += "─" * 25 + "\n\n"

            if hasattr(message, 'message_id'):
                self.bot.edit_message_text(
                    bookings_text,
                    message.chat.id,
                    message.message_id,
                    parse_mode='Markdown',
                    reply_markup=self.back_to_menu_keyboard()
                )
            else:
                self.bot.send_message(message.chat.id, bookings_text, parse_mode='Markdown',
                                      reply_markup=self.back_to_menu_keyboard())

        except Exception as e:
            logger.error(f"Ошибка получения записей: {e}")
            self.bot.send_message(message.chat.id, "❌ Ошибка загрузки записей")
            self.send_main_menu(message.chat.id)

    def show_schedule_info(self, message):
        """Показ расписания"""
        schedule_text = """
📅 *Расписание тренировок:*

*ПОНЕДЕЛЬНИК:*
19:00 - Тайский бокс (взрослые)
20:30 - ММА (взрослые)

*ВТОРНИК:*
17:00 - Тайский бокс (дети 9-14 лет)
19:00 - Грэпплинг (взрослые)

*СРЕДА:*
19:00 - Тайский бокс (взрослые)
20:30 - ММА (взрослые)

*ЧЕТВЕРГ:*
17:00 - Тайский бокс (дети 9-14 лет)
19:00 - Грэпплинг (взрослые)

*ПЯТНИЦА:*
19:00 - Тайский бокс (взрослые)
20:30 - ММА (взрослые)

*СУББОТА:*
11:00 - Тайский бокс (дети 5-8 лет)
12:00 - ММА (дети)

*ВОСКРЕСЕНЬЕ* - ВЫХОДНОЙ

💡 *Первая тренировка - БЕСПЛАТНО!*"""

        if hasattr(message, 'message_id'):
            self.bot.edit_message_text(
                schedule_text,
                message.chat.id,
                message.message_id,
                parse_mode='Markdown',
                reply_markup=self.back_to_menu_keyboard()
            )
        else:
            self.bot.send_message(message.chat.id, schedule_text, parse_mode='Markdown',
                                  reply_markup=self.back_to_menu_keyboard())

    def send_prices_info(self, message):
        """Показ цен"""
        prices_text = """
💳 *Стоимость абонементов:*

*👶 ДЕТСКИЕ ГРУППЫ:*
• Тайский бокс дети - *6 000 ₽/мес*
• ММА дети - *6 000 ₽/мес*

*👨‍🦰 ВЗРОСЛЫЕ ГРУППЫ:*
• Тайский бокс - *6 000 ₽/мес*
• ММА - *6 000 ₽/мес*  
• Грэпплинг/БЖЖ - *6 000 ₽/мес*
• Бокс (утренние) - *6 000 ₽/мес*

*🎯 КОМБО И ИНДИВИДУАЛЬНО:*
• Тайский бокс + ММА - *11 000 ₽/мес*
• Индивидуальная тренировка - *3 000 ₽*
• Сплит тренировка (2 чел) - *4 000 ₽*

💪 *Первая тренировка - БЕСПЛАТНО!*"""

        if hasattr(message, 'message_id'):
            self.bot.edit_message_text(
                prices_text,
                message.chat.id,
                message.message_id,
                parse_mode='Markdown',
                reply_markup=self.back_to_menu_keyboard()
            )
        else:
            self.bot.send_message(message.chat.id, prices_text, parse_mode='Markdown',
                                  reply_markup=self.back_to_menu_keyboard())

    def send_contacts_info(self, message):
        """Показ контактов с инлайн-кнопками"""
        contacts_text = f"""
📞 *Контакты клуба:*

📍 *Адрес:*
{self.config.GYM_ADDRESS}

📱 *Телефон:*
{self.config.GYM_PHONE}

🕒 *Режим работы:*
Пн-Пт: 17:00 - 21:00
Сб: 10:00 - 14:00
Вс: Выходной

💪 *Первая тренировка - БЕСПЛАТНО!*"""

        keyboard = InlineKeyboardMarkup()
        keyboard.add(
            InlineKeyboardButton("💬 Telegram", url="https://t.me/Zimin03"),
            InlineKeyboardButton("📱 WhatsApp", url="https://wa.me/79251506975")
        )
        keyboard.add(
            InlineKeyboardButton("📷 Instagram", url="https://www.instagram.com/luber.fight.club"),
            InlineKeyboardButton("🗺️ На картах",
                                 url="https://yandex.ru/maps/?ll=37.884696,55.700928&z=17&pt=37.884696,55.700928,pm2grm")
        )
        keyboard.add(
            InlineKeyboardButton("🔙 Главное меню", callback_data="return_to_main_menu")
        )

        if hasattr(message, 'message_id'):
            self.bot.edit_message_text(
                contacts_text,
                message.chat.id,
                message.message_id,
                parse_mode='Markdown',
                reply_markup=keyboard
            )
        else:
            self.bot.send_message(
                message.chat.id,
                contacts_text,
                parse_mode='Markdown',
                reply_markup=keyboard
            )

    def send_help_info(self, message):
        """Показ помощи"""
        help_text = f"""
ℹ️ *Помощь по боту:*

*Основные функции:*
🥊 *Запись на тренировки* - выбирайте день и время
📊 *Прогресс* - отслеживайте свои достижения  
🎯 *Челенджи* - участвуйте и получайте бонусы
📅 *Мои записи* - просмотр активных бронирований

*Команды:*
/start - Главное меню
/menu - Показать меню
/schedule - Расписание тренировок
/price - Цены и абонементы
/contacts - Контакты клуба

📞 *Для связи:*
{self.config.ADMIN_CONTACT} | {self.config.GYM_PHONE}

💡 *Первая тренировка - БЕСПЛАТНО!*"""

        if hasattr(message, 'message_id'):
            self.bot.edit_message_text(
                help_text,
                message.chat.id,
                message.message_id,
                parse_mode='Markdown',
                reply_markup=self.back_to_menu_keyboard()
            )
        else:
            self.bot.send_message(message.chat.id, help_text, parse_mode='Markdown',
                                  reply_markup=self.back_to_menu_keyboard())

    def _send_no_workouts_message(self, message, selected_date):
        """Отправляет сообщение об отсутствии тренировок"""
        text = f"❌ На *{selected_date.strftime('%d.%m.%Y')}* нет доступных тренировок\n\n"
        text += "💡 *Что можно сделать:*\n• Выбрать другой день\n"
        text += f"• Обратиться к тренеру: {self.config.ADMIN_CONTACT}\n"
        text += f"• Позвонить: {self.config.GYM_PHONE}"

        if hasattr(message, 'message_id'):
            self.bot.edit_message_text(
                text,
                message.chat.id,
                message.message_id,
                parse_mode='Markdown',
                reply_markup=self.back_to_days_keyboard()
            )
        else:
            self.bot.send_message(message.chat.id, text, parse_mode='Markdown',
                                  reply_markup=self.back_to_days_keyboard())

    def _send_workouts_list(self, message, workouts, selected_date):
        """Отправляет список доступных тренировок"""
        workouts_text = f"🎯 *{selected_date.strftime('%d.%m.%Y')}*\n\n📍 *Доступные тренировки:*\n\n"
        keyboard = InlineKeyboardMarkup()

        for workout in workouts:
            emoji = "🥊" if "тайский" in workout['type'].lower() else "🥋"
            btn_text = f"{emoji} {workout['time']} - {workout['type']}"
            keyboard.add(InlineKeyboardButton(btn_text, callback_data=f"book_{workout['id']}"))

            workouts_text += f"⏰ *{workout['time']}* - {workout['type']}\n"
            workouts_text += f"   👨‍🏫 Тренер: {workout['trainer']}\n"
            workouts_text += f"   ✅ Свободно: {workout['available_slots']} мест\n\n"

        keyboard.add(InlineKeyboardButton("🔙 Выбрать другой день", callback_data="menu_booking"))
        keyboard.add(InlineKeyboardButton("🔙 Главное меню", callback_data="return_to_main_menu"))

        if hasattr(message, 'message_id'):
            self.bot.edit_message_text(
                workouts_text,
                message.chat.id,
                message.message_id,
                parse_mode='Markdown',
                reply_markup=keyboard
            )
        else:
            self.bot.send_message(message.chat.id, workouts_text, parse_mode='Markdown')
            self.bot.send_message(message.chat.id, "👇 *Выберите тренировку:*",
                                  parse_mode='Markdown', reply_markup=keyboard)

    def handle_booking_callback(self, call):
        """Обработчик подтверждения записи"""
        try:
            workout_id = int(call.data.split('_')[1])
            success = self.db.book_workout(call.from_user.id, workout_id)

            if success:
                self.bot.answer_callback_query(call.id, "✅ Запись подтверждена!")
                self.bot.edit_message_text(
                    self.config.MESSAGES['workout_booked'],
                    call.message.chat.id,
                    call.message.message_id,
                    parse_mode='Markdown'
                )
                self.send_main_menu(call.message.chat.id, "✅ Запись успешно оформлена! Что дальше?")
            else:
                self.bot.answer_callback_query(call.id, "❌ Не удалось записаться")
                self._send_booking_error(call.message.chat.id)

        except Exception as e:
            logger.error(f"Ошибка бронирования: {e}")
            self.bot.answer_callback_query(call.id, "❌ Ошибка записи")
            self._send_booking_error(call.message.chat.id)

    def _send_booking_error(self, chat_id):
        """Отправляет сообщение об ошибке записи"""
        error_text = "❌ Не удалось завершить запись.\n"
        error_text += f"Попробуйте позже или свяжитесь с тренером: {self.config.ADMIN_CONTACT}"
        self.bot.send_message(chat_id, error_text, reply_markup=self.back_to_menu_keyboard())

    def handle_back_callback(self, call):
        """Обработчик возврата к выбору дня"""
        try:
            self.bot.edit_message_text(
                "🗓️ Выберите день:",
                call.message.chat.id,
                call.message.message_id
            )
            self.show_booking_days(call.message)
        except Exception as e:
            logger.error(f"Ошибка возврата: {e}")

    def handle_contacts_back_to_main(self, call):
        """Обработчик возврата из контактов в главное меню"""
        try:
            self.bot.answer_callback_query(call.id)
            self.send_main_menu(call.message.chat.id)
        except Exception as e:
            logger.error(f"Ошибка возврата из контактов: {e}")
            self.send_main_menu(call.message.chat.id)

    def handle_unknown_message(self, message):
        """Обработчик неизвестных сообщений"""
        self.send_main_menu(message.chat.id,
                            "🤖 Используйте меню ниже для навигации\n\n💡 Доступные команды:\n/start - главное меню\n/menu - показать меню")


if __name__ == '__main__':
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
        bot = FightClubBot()
        bot.run()
    except Exception as e:
        logger.error(f"❌ Не удалось запустить бота: {e}")
    finally:
        if os.path.exists(lock_file):
            os.remove(lock_file)
            logger.info("✅ Файл блокировки очищен")