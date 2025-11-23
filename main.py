import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
import datetime

# Инициализация бота
TOKEN = '8595147547:AAHaVSpH7KeDKkl2iyZhuNGjfR01JSelIL8'  # замените на ваш токен
bot = telebot.TeleBot(TOKEN)

# Обработчик команды /start
@bot.message_handler(commands=['start'])
def handle_start(message):
    bot.send_message(message.chat.id, "Бот запущен и работает!")

# Пример обработчика callback-данных для открытия меню
@bot.callback_query_handler(func=lambda call: call.data == 'show_booking_options')
def handle_show_booking(call):
    show_booking_options(call)

def show_booking_options(call):
    # Создаем inline клавиатуру
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("📅 Сегодня", callback_data='day_today'),
        InlineKeyboardButton("📅 Завтра", callback_data='day_tomorrow'),
        InlineKeyboardButton("📅 Послезавтра", callback_data='day_day_after'),
        InlineKeyboardButton("🔙 Назад в меню", callback_data='back_to_menu')
    )
    # Правильный вызов
    bot.edit_message_text(
        "Выберите день:",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=markup
    )

# Функция для отображения выбора дня через reply клавиатуру
def show_booking_options_reply(message):
    markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    markup.add(
        KeyboardButton("📅 Сегодня"),
        KeyboardButton("📅 Завтра"),
        KeyboardButton("📅 Послезавтра"),
        KeyboardButton("🔙 Назад в меню")
    )
    bot.send_message(
        message.chat.id,
        "Выберите день:",
        reply_markup=markup
    )

# Обработчик сообщений для выбора дня
@bot.message_handler(func=lambda m: m.text in ["📅 Сегодня", "📅 Завтра", "📅 Послезавтра"])
def handle_day_choice(message):
    days_map = {
        "📅 Сегодня": 0,
        "📅 Завтра": 1,
        "📅 Послезавтра": 2
    }
    delta = days_map[message.text]
    selected_date = datetime.date.today() + datetime.timedelta(days=delta)
    # Вы можете вызвать тут функцию получения расписания для выбранной даты
    bot.send_message(message.chat.id, f"Вы выбрали {message.text} ({selected_date})")

# Обработчик для возврата в главное меню или отмены
@bot.message_handler(func=lambda m: m.text == "🔙 Назад в меню")
def back_to_main_menu(message):
    bot.send_message(message.chat.id, "Главное меню", reply_markup=main_menu())

def main_menu():
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("Записаться на тренировку", callback_data='show_booking_options')
        # добавьте остальные кнопки
    )
    return markup

# Запуск бота
if __name__ == '__main__':
    bot.polling(none_stop=True)
