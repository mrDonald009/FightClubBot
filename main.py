#import
import telebot
import config
import os
import sys
from database import Database
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
import datetime

# Удаляем файл блокировки при запуске
lock_file = "bot.lock"
if os.path.exists(lock_file):
    print("🗑️ Удаляем старый файл блокировки...")
    os.remove(lock_file)

# Создаем новый файл блокировки
try:
    with open(lock_file, 'w') as f:
        f.write(str(os.getpid()))
    print("✅ Файл блокировки создан")
except:
    print("⚠️ Не удалось создать файл блокировки")

print("🔄 Инициализация базы данных...")
try:
    db = Database()
    print("✅ База данных подключена")

    # Проверяем есть ли тренировки
    test_date = datetime.datetime.now().strftime('%Y-%m-%d')
    workouts = db.get_workouts_by_date(test_date)
    print(f"🔍 Тренировок на сегодня: {len(workouts)}")

    if len(workouts) == 0:
        print("⚠️ Нет тренировок в базе, инициализируем данные...")
        db.initialize_real_data()

except Exception as e:
    print(f"❌ Ошибка базы данных: {e}")
    sys.exit(1)

print("🔄 Инициализация бота...")
bot = telebot.TeleBot(config.TOKEN)
print("✅ Бот инициализирован")


# ФУНКЦИИ КЛАВИАТУР
def main_menu():
    """Главное меню с инлайн-кнопками"""
    keyboard = InlineKeyboardMarkup(row_width=2)

    keyboard.add(
        InlineKeyboardButton("🥊 Записаться на тренировку", callback_data="menu_booking"),
        InlineKeyboardButton("📊 Мой прогресс", callback_data="menu_progress"),
        InlineKeyboardButton("🎯 Челенджи и бонусы", callback_data="menu_challenges"),
        InlineKeyboardButton("👤 Мой профиль", callback_data="menu_profile"),
        InlineKeyboardButton("🏆 Таблица лидеров", callback_data="menu_leaderboard"),
        InlineKeyboardButton("📅 Мои записи", callback_data="menu_my_bookings"),
        InlineKeyboardButton("📋 Расписание", callback_data="menu_schedule"),
        InlineKeyboardButton("💰 Цены", callback_data="menu_prices"),
        InlineKeyboardButton("📞 Контакты", callback_data="menu_contacts"),
        InlineKeyboardButton("ℹ️ Помощь", callback_data="menu_help")
    )

    return keyboard


def days_keyboard():
    """Клавиатура выбора дня"""
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    keyboard.add(
        KeyboardButton("📅 Сегодня"),
        KeyboardButton("📅 Завтра"),
        KeyboardButton("📅 Послезавтра"),
        KeyboardButton("🔙 Назад в меню")
    )
    return keyboard


# ОБРАБОТЧИКИ КОМАНД
@bot.message_handler(commands=['start'])
def send_welcome(message):
    try:
        user_id = message.from_user.id
        username = message.from_user.username or "Не указан"
        full_name = f"{message.from_user.first_name} {message.from_user.last_name or ''}"

        # Регистрируем пользователя
        db.add_user(user_id, username, full_name)

        welcome_text = """💪 *Добро пожаловать в FightClubManager!*

Я — ваш цифровой помощник в мире единоборств! Выберите нужный раздел:"""

        # Отправляем сообщение с инлайн-меню
        bot.send_message(message.chat.id, welcome_text,
                         parse_mode='Markdown',
                         reply_markup=main_menu())

    except Exception as e:
        print(f"Ошибка в /start: {e}")
        bot.reply_to(message, "❌ Произошла ошибка. Попробуйте позже.")


# ОБРАБОТЧИКИ ИНЛАЙН-МЕНЮ
@bot.callback_query_handler(func=lambda call: call.data.startswith('menu_'))
def handle_main_menu(call):
    """Обработчик главного меню"""
    try:
        action = call.data.replace('menu_', '')

        if action == 'booking':
            bot.answer_callback_query(call.id)
            bot.send_message(call.message.chat.id, "🗓️ Выберите день:", reply_markup=days_keyboard())

        elif action == 'progress':
            bot.answer_callback_query(call.id)
            show_progress_info(call.message)

        elif action == 'challenges':
            bot.answer_callback_query(call.id)
            show_challenges_info(call.message)

        elif action == 'profile':
            bot.answer_callback_query(call.id)
            show_profile_info(call.message)

        elif action == 'leaderboard':
            bot.answer_callback_query(call.id)
            show_leaderboard_info(call.message)

        elif action == 'my_bookings':
            bot.answer_callback_query(call.id)
            show_my_bookings_info(call.message)

        elif action == 'schedule':
            bot.answer_callback_query(call.id)
            show_schedule_info(call.message)

        elif action == 'prices':
            bot.answer_callback_query(call.id)
            send_prices_info(call.message)

        elif action == 'contacts':
            bot.answer_callback_query(call.id)
            send_contacts_info(call.message)

        elif action == 'help':
            bot.answer_callback_query(call.id)
            send_help_info(call.message)

    except Exception as e:
        print(f"Ошибка в обработчике меню: {e}")
        bot.answer_callback_query(call.id, "❌ Ошибка")


# ФУНКЦИИ ДЛЯ МЕНЮ
def show_progress_info(message):
    """Показ прогресса пользователя"""
    try:
        user_id = message.from_user.id
        stats = db.get_user_stats(user_id)

        progress_text = f"""📊 *ВАШ ПРОГРЕСС*

🎯 Посещений всего: *{stats['total_workouts']}*
📈 Текущая серия: *{stats['current_streak']} дней*
🔥 Сожжено калорий: *~{stats['total_workouts'] * 500} ккал*

🏆 *ДОСТИЖЕНИЯ:*
{'✅' if stats['total_workouts'] >= 5 else '⏳'} Новичок (5 тренировок)
{'✅' if stats['current_streak'] >= 3 else '⏳'} Стабильность (3 дня подряд)
{'✅' if stats['total_workouts'] >= 10 else '⏳'} Боец (10 тренировок)"""

        bot.send_message(message.chat.id, progress_text, parse_mode='Markdown')
        bot.send_message(message.chat.id, "Выберите действие:", reply_markup=main_menu())

    except Exception as e:
        print(f"Ошибка прогресса: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка загрузки прогресса", reply_markup=main_menu())


def show_challenges_info(message):
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

    bot.send_message(message.chat.id, challenges_text, parse_mode='Markdown')
    bot.send_message(message.chat.id, "Выберите действие:", reply_markup=main_menu())


def show_profile_info(message):
    """Показ профиля пользователя"""
    try:
        user_id = message.from_user.id
        profile = db.get_user_profile(user_id)

        profile_text = f"""👤 *ВАШ ПРОФИЛЬ*

*Имя:* {profile['full_name']}
*Телеграм:* @{profile['username']}
*Дата регистрации:* {profile['registration_date']}

📞 *Контакты зала:*
г. Люберцы, ул. 8 Марта, д. 20
+7 (965) 229-64-06"""

        bot.send_message(message.chat.id, profile_text, parse_mode='Markdown')
        bot.send_message(message.chat.id, "Выберите действие:", reply_markup=main_menu())

    except Exception as e:
        print(f"Ошибка профиля: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка загрузки профиля", reply_markup=main_menu())


def show_leaderboard_info(message):
    """Показ таблицы лидеров"""
    leaderboard_text = """🏆 *ТАБЛИЦА ЛИДЕРОВ* | Этот месяц

🥇 Алексей П. - *12 тренировок*
🥈 Мария К. - *11 тренировок*  
🥉 Дмитрий С. - *10 тренировок*
4. Анна М. - *9 тренировок*
5. Сергей В. - *8 тренировок*

*Ваше место:* входите в топ-10!

💪 *Следующая цель:* 15 тренировок в месяце"""

    bot.send_message(message.chat.id, leaderboard_text, parse_mode='Markdown')
    bot.send_message(message.chat.id, "Выберите действие:", reply_markup=main_menu())


def show_my_bookings_info(message):
    """Показ активных записей пользователя"""
    try:
        user_id = message.from_user.id
        bookings = db.get_user_bookings(user_id)

        if not bookings:
            bot.send_message(message.chat.id, "📭 *У вас нет активных записей на тренировки*", parse_mode='Markdown')
            bot.send_message(message.chat.id, "Выберите действие:", reply_markup=main_menu())
            return

        bookings_text = "📅 *ВАШИ ЗАПИСИ:*\n\n"
        for booking in bookings:
            bookings_text += f"📅 *{booking['date']}* в *{booking['time']}*\n"
            bookings_text += f"🥊 {booking['workout_name']}\n"
            bookings_text += f"👨‍🏫 Тренер: {booking['trainer']}\n"
            bookings_text += "─" * 25 + "\n\n"

        bot.send_message(message.chat.id, bookings_text, parse_mode='Markdown')
        bot.send_message(message.chat.id, "Выберите действие:", reply_markup=main_menu())

    except Exception as e:
        print(f"Ошибка получения записей: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка загрузки записей", reply_markup=main_menu())


def show_schedule_info(message):
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

    bot.send_message(message.chat.id, schedule_text, parse_mode='Markdown')
    bot.send_message(message.chat.id, "Выберите действие:", reply_markup=main_menu())


def send_prices_info(message):
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

    bot.send_message(message.chat.id, prices_text, parse_mode='Markdown')
    bot.send_message(message.chat.id, "Выберите действие:", reply_markup=main_menu())


def send_contacts_info(message):
    """Показ контактов"""
    contacts_text = """
📞 *Контакты клуба:*

📍 *Адрес:*
г. Люберцы, ул. 8 Марта, д. 20

📱 *Телефон:*
+7 (965) 229-64-06

💬 *Telegram:*
@Zimin03

📱 *WhatsApp:*
https://api.whatsapp.com/send/?phone=79251506975

🕒 *Режим работы:*
Пн-Пт: 17:00 - 21:00
Сб: 10:00 - 14:00
Вс: Выходной"""

    bot.send_message(message.chat.id, contacts_text, parse_mode='Markdown')
    bot.send_message(message.chat.id, "Выберите действие:", reply_markup=main_menu())


def send_help_info(message):
    """Показ помощи"""
    help_text = """
ℹ️ *Помощь по боту:*

*Основные функции:*
🥊 *Запись на тренировки* - выбирайте день и время
📊 *Прогресс* - отслеживайте свои достижения  
🎯 *Челенджи* - участвуйте и получайте бонусы
📅 *Мои записи* - просмотр активных бронирований

*Команды:*
/start - Главное меню
/schedule - Расписание тренировок
/price - Цены и абонементы
/contacts - Контакты клуба

📞 *Для связи:*
@Zimin03 | +7 (965) 229-64-06

💡 *Первая тренировка - БЕСПЛАТНО!*"""

    bot.send_message(message.chat.id, help_text, parse_mode='Markdown')
    bot.send_message(message.chat.id, "Выберите действие:", reply_markup=main_menu())


# ОБРАБОТЧИКИ ЗАПИСИ НА ТРЕНИРОВКИ
@bot.message_handler(func=lambda message: message.text in ["📅 Сегодня", "📅 Завтра", "📅 Послезавтра"])
def select_day(message):
    """Выбор тренировки по дням"""
    try:
        day_map = {
            "📅 Сегодня": 0,
            "📅 Завтра": 1,
            "📅 Послезавтра": 2
        }

        days_offset = day_map[message.text]
        selected_date = datetime.datetime.now() + datetime.timedelta(days=days_offset)
        date_str = selected_date.strftime('%Y-%m-%d')

        print(f"🔍 Ищем тренировки на {date_str}")

        # Получаем тренировки на выбранный день
        workouts = db.get_workouts_by_date(date_str)

        print(f"📋 Найдено тренировок: {len(workouts)}")

        if not workouts:
            no_workouts_text = f"❌ На *{selected_date.strftime('%d.%m.%Y')}* нет доступных тренировок\n\n"
            no_workouts_text += "💡 *Что можно сделать:*\n"
            no_workouts_text += "• Выбрать другой день\n"
            no_workouts_text += "• Обратиться к тренеру: @Zimin03\n"
            no_workouts_text += "• Позвонить: +7 (965) 229-64-06"

            bot.send_message(message.chat.id, no_workouts_text, parse_mode='Markdown', reply_markup=main_menu())
            return

        # Формируем красивый список тренировок
        workouts_text = f"🎯 *{selected_date.strftime('%d.%m.%Y')}*\n\n"
        workouts_text += "📍 *Доступные тренировки:*\n\n"

        # Создаем инлайн-клавиатуру с тренировками
        keyboard = InlineKeyboardMarkup()
        for workout in workouts:
            # Красивая кнопка с эмодзи
            emoji = "🥊" if "тайский" in workout['type'].lower() else "🥋"
            btn_text = f"{emoji} {workout['time']} - {workout['type']}"
            callback_data = f"book_{workout['id']}"
            keyboard.add(InlineKeyboardButton(btn_text, callback_data=callback_data))

            # Информация в тексте
            workouts_text += f"⏰ *{workout['time']}* - {workout['type']}\n"
            workouts_text += f"   👨‍🏫 Тренер: {workout['trainer']}\n"
            workouts_text += f"   ✅ Свободно: {workout['available_slots']} мест\n\n"

        keyboard.add(InlineKeyboardButton("🔙 Назад к выбору дня", callback_data="back_to_days"))

        # Отправляем информацию
        bot.send_message(message.chat.id, workouts_text, parse_mode='Markdown')
        bot.send_message(message.chat.id, "👇 *Выберите тренировку:*",
                         parse_mode='Markdown', reply_markup=keyboard)

    except Exception as e:
        print(f"❌ Ошибка выбора дня: {e}")
        error_text = "❌ Ошибка при загрузке расписания\n\n"
        error_text += "Попробуйте позже или обратитесь к администратору:\n"
        error_text += "@Zimin03 | +7 (965) 229-64-06"
        bot.send_message(message.chat.id, error_text, reply_markup=main_menu())


@bot.callback_query_handler(func=lambda call: call.data.startswith('book_'))
def confirm_booking(call):
    """Подтверждение записи на тренировку"""
    try:
        workout_id = int(call.data.split('_')[1])
        user_id = call.from_user.id

        # Записываем пользователя
        success = db.book_workout(user_id, workout_id)

        if success:
            bot.answer_callback_query(call.id, "✅ Запись подтверждена!")

            # Красивое сообщение об успехе
            success_text = """🎉 *Отлично! Вы записаны на тренировку!*

📋 *Что дальше:*
• Придите за 10-15 минут до начала
• Возьмите сменную обувь
• Сообщите тренеру о записи через бота

💡 *Помните:* 
Первая тренировка - *БЕСПЛАТНО!*

🏋️ *Готовьтесь к тренировке и ждем вас в зале!*"""

            bot.edit_message_text(
                success_text,
                call.message.chat.id,
                call.message.message_id,
                parse_mode='Markdown'
            )

            # Возвращаем в главное меню
            bot.send_message(call.message.chat.id, "Выберите действие:", reply_markup=main_menu())
        else:
            bot.answer_callback_query(call.id, "❌ Не удалось записаться")
            bot.send_message(call.message.chat.id,
                             "❌ К сожалению, не удалось завершить запись.\nПопробуйте позже или свяжитесь с тренером.",
                             reply_markup=main_menu())

    except Exception as e:
        print(f"Ошибка подтверждения: {e}")
        bot.answer_callback_query(call.id, "❌ Ошибка записи")
        bot.send_message(call.message.chat.id,
                         "❌ Произошла ошибка при записи.\nСвяжитесь с тренером: @Zimin03",
                         reply_markup=main_menu())


@bot.callback_query_handler(func=lambda call: call.data == "back_to_days")
def back_to_days(call):
    """Возврат к выбору дня"""
    try:
        bot.edit_message_text(
            "🗓️ Выберите день:",
            call.message.chat.id,
            call.message.message_id
        )
        bot.send_message(call.message.chat.id, "Выберите день:", reply_markup=days_keyboard())
    except Exception as e:
        print(f"Ошибка возврата: {e}")


@bot.message_handler(func=lambda message: message.text == "🔙 Назад в меню")
def back_to_main_menu(message):
    """Возврат в главное меню"""
    bot.send_message(message.chat.id, "Главное меню:", reply_markup=main_menu())


# КОМАНДЫ ДЛЯ БЫСТРОГО ДОСТУПА
@bot.message_handler(commands=['schedule'])
def schedule_command(message):
    show_schedule_info(message)


@bot.message_handler(commands=['price'])
def price_command(message):
    send_prices_info(message)


@bot.message_handler(commands=['contacts'])
def contacts_command(message):
    send_contacts_info(message)


@bot.message_handler(commands=['help'])
def help_command(message):
    send_help_info(message)


@bot.message_handler(content_types=['text'])
def echo_message(message):
    """Обработка неизвестных сообщений"""
    bot.send_message(message.chat.id,
                     "🤖 Используйте меню ниже или команды для навигации\n/start - открыть главное меню",
                     reply_markup=main_menu())


if __name__ == '__main__':
    print("🚀 Запускаем FightClubManager...")
    print("✅ Готов к работе!")
    print("⏳ Ожидаю сообщения...")

    try:
        bot.polling(none_stop=True)
    except KeyboardInterrupt:
        print("\n🛑 Бот остановлен пользователем")
    except Exception as e:
        print(f"❌ Ошибка: {e}")
    finally:
        # Удаляем файл блокировки при выходе
        if os.path.exists(lock_file):
            os.remove(lock_file)
            print("✅ Файл блокировки удален")