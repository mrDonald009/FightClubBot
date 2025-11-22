import telebot
import config
import os
import sys
from database import Database

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
except Exception as e:
    print(f"❌ Ошибка базы данных: {e}")
    sys.exit(1)

print("🔄 Инициализация бота...")
bot = telebot.TeleBot(config.TOKEN)
print("✅ Бот инициализирован")


@bot.message_handler(commands=['start'])
def send_welcome(message):
    try:
        user_id = message.from_user.id
        username = message.from_user.username or "Не указан"
        full_name = f"{message.from_user.first_name} {message.from_user.last_name or ''}"

        # Регистрируем пользователя
        db.add_user(user_id, username, full_name)

        welcome_text = """
🎉 Добро пожаловать в Клуб единоборств Люберцы!

🥊 Клуб единоборств в Люберцах
📍 ул. 8 Марта, д. 20

🏋️ Направления:
• Тайский бокс (дети/взрослые)
• ММА (смешанные единоборства)
• Грэпплинг/БЖЖ
• Бокс

💬 Команды:
/schedule - Расписание тренировок
/price - Цены и абонементы  
/workouts - Все направления
/contacts - Контакты клуба
/help - Помощь

💪 Первая тренировка - БЕСПЛАТНО!
        """
        bot.reply_to(message, welcome_text)
    except Exception as e:
        print(f"Ошибка в /start: {e}")
        bot.reply_to(message, "❌ Произошла ошибка. Попробуйте позже.")


@bot.message_handler(commands=['schedule'])
def show_schedule(message):
    schedule_text = """
📅 Расписание тренировок:

ПОНЕДЕЛЬНИК:
19:00 - Тайский бокс (взрослые)
20:30 - ММА (взрослые)

ВТОРНИК:
17:00 - Тайский бокс (дети 9-14 лет)
19:00 - Грэпплинг (взрослые)

СРЕДА:
19:00 - Тайский бокс (взрослые)
20:30 - ММА (взрослые)

ЧЕТВЕРГ:
17:00 - Тайский бокс (дети 9-14 лет)
19:00 - Грэпплинг (взрослые)

ПЯТНИЦА:
19:00 - Тайский бокс (взрослые)
20:30 - ММА (взрослые)

СУББОТА:
11:00 - Тайский бокс (дети 5-8 лет)
12:00 - ММА (дети)

ВОСКРЕСЕНЬЕ - ВЫХОДНОЙ

💡 Первая тренировка - БЕСПЛАТНО!
    """
    bot.send_message(message.chat.id, schedule_text)


@bot.message_handler(commands=['price'])
def send_prices(message):
    prices_text = """
💳 Стоимость абонементов:

👶 ДЕТСКИЕ ГРУППЫ:
• Тайский бокс дети - 6 000 ₽/мес
• ММА дети - 6 000 ₽/мес

👨‍🦰 ВЗРОСЛЫЕ ГРУППЫ:
• Тайский бокс - 6 000 ₽/мес
• ММА - 6 000 ₽/мес  
• Грэпплинг/БЖЖ - 6 000 ₽/мес
• Бокс (утренние) - 6 000 ₽/мес

🎯 КОМБО И ИНДИВИДУАЛЬНО:
• Тайский бокс + ММА - 11 000 ₽/мес
• Индивидуальная тренировка - 3 000 ₽
• Сплит тренировка (2 чел) - 4 000 ₽

💪 Первая тренировка - БЕСПЛАТНО!
    """
    bot.send_message(message.chat.id, prices_text)


@bot.message_handler(commands=['contacts'])
def send_contacts(message):
    contacts_text = """
📞 Контакты клуба:

📍 Адрес:
г. Люберцы, ул. 8 Марта, д. 20

📱 Телефон:
+7 (965) 229-64-06

💬 Telegram:
@Zimin03

📱 WhatsApp:
https://api.whatsapp.com/send/?phone=79251506975

🕒 Режим работы:
Пн-Пт: 17:00 - 21:00
Сб: 10:00 - 14:00
Вс: Выходной
    """
    bot.send_message(message.chat.id, contacts_text)


@bot.message_handler(commands=['help'])
def send_help(message):
    help_text = """
❓ Доступные команды:

/start - Начать работу
/schedule - Расписание тренировок
/price - Цены и абонементы
/contacts - Контакты клуба
/help - Помощь

📞 Для записи на тренировку:
Напишите нам в Telegram: @Zimin03
Или позвоните: +7 (965) 229-64-06

💡 Первая тренировка - БЕСПЛАТНО!
    """
    bot.send_message(message.chat.id, help_text)


@bot.message_handler(content_types=['text'])
def echo_message(message):
    bot.send_message(message.chat.id, f"🤖 Получил: {message.text}")


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