import datetime
import logging
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from keyboards import days_selection_keyboard, back_to_days_keyboard, back_to_menu_keyboard
from handlers.start import send_main_menu

logger = logging.getLogger(__name__)


def show_booking_days(bot, message):
    """Показ выбора дней для записи через инлайн-кнопки"""
    booking_text = "🗓️ *Выберите день для записи на тренировку:*\n\n💡 Доступны ближайшие 7 дней:"

    # Всегда отправляем новое сообщение вместо редактирования
    bot.send_message(
        message.chat.id,
        booking_text,
        parse_mode='Markdown',
        reply_markup=days_selection_keyboard()
    )


def handle_day_selection_callback(bot, db, config, call):
    """Обработчик выбора дня через инлайн-кнопки"""
    try:
        bot.answer_callback_query(call.id)

        # Получаем смещение дней из callback_data
        days_offset = int(call.data.replace('select_day_', ''))
        selected_date = datetime.datetime.now() + datetime.timedelta(days=days_offset)
        date_str = selected_date.strftime('%Y-%m-%d')

        # ДОБАВИМ ОТЛАДОЧНУЮ ИНФОРМАЦИЮ
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"🔍 ОБРАБОТКА ВЫБОРА ДНЯ: offset={days_offset}, date={date_str}")

        workouts = db.get_workouts_by_date(date_str)
        logger.info(f"🔍 НАЙДЕНО ТРЕНИРОВОК: {len(workouts)} для даты {date_str}")

        if not workouts:
            _send_no_workouts_message(bot, config, call.message, selected_date)
            return

        _send_workouts_list(bot, db, call.message, workouts, selected_date)

    except Exception as e:
        logger.error(f"Ошибка выбора дня: {e}")
        bot.answer_callback_query(call.id, "❌ Ошибка выбора дня")
        send_main_menu(bot, call.message.chat.id)

def _send_no_workouts_message(bot, config, message, selected_date):
    """Отправляет сообщение об отсутствии тренировок"""
    text = f"❌ На *{selected_date.strftime('%d.%m.%Y')}* нет доступных тренировок\n\n"
    text += "💡 *Что можно сделать:*\n• Выбрать другой день\n"
    text += f"• Обратиться к тренеру: {config.ADMIN_CONTACT}\n"
    text += f"• Позвонить: {config.GYM_PHONE}"

    # Всегда отправляем новое сообщение для сохранения истории
    bot.send_message(
        message.chat.id,
        text,
        parse_mode='Markdown',
        reply_markup=back_to_days_keyboard()
    )


def _send_workouts_list(bot, db, message, workouts, selected_date):
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

    # Отправляем новое сообщение для сохранения истории
    bot.send_message(message.chat.id, workouts_text, parse_mode='Markdown')
    bot.send_message(message.chat.id, "👇 *Выберите тренировку:*",
                     parse_mode='Markdown', reply_markup=keyboard)


def handle_booking_callback(bot, db, config, call):
    """Обработчик подтверждения записи"""
    try:
        workout_id = int(call.data.split('_')[1])
        success = db.book_workout(call.from_user.id, workout_id)

        if success:
            bot.answer_callback_query(call.id, "✅ Запись подтверждена!")

            # Получаем информацию о записанной тренировке для подробного сообщения
            with db.get_connection() as conn:
                workout_info = conn.execute('''
                    SELECT s.date, s.time, wt.name as workout_name, t.name as trainer_name
                    FROM schedule s
                    JOIN workout_types wt ON s.workout_type_id = wt.id
                    JOIN trainers t ON s.trainer_id = t.id
                    WHERE s.id = ?
                ''', (workout_id,)).fetchone()

                if workout_info:
                    booking_text = f"""🎉 *ЗАПИСЬ ПОДТВЕРЖДЕНА!*

📅 *Дата:* {workout_info['date']}
⏰ *Время:* {workout_info['time']}
🥊 *Тренировка:* {workout_info['workout_name']}
👨‍🏫 *Тренер:* {workout_info['trainer_name']}

💡 *Что дальше:*
• Придите за 10-15 минут до начала
• Возьмите сменную обувь
• Сообщите тренеру о записи через бота

🏋️ *Готовьтесь к тренировке и ждем вас в зале!*"""
                else:
                    booking_text = config.MESSAGES['workout_booked']

            # Отправляем сообщение о успешной записи НЕ возвращая в главное меню
            bot.send_message(
                call.message.chat.id,
                booking_text,
                parse_mode='Markdown',
                reply_markup=back_to_menu_keyboard()  # Просто кнопка возврата в меню
            )

        else:
            bot.answer_callback_query(call.id, "❌ Не удалось записаться")
            _send_booking_error(bot, config, call.message.chat.id)

    except Exception as e:
        logger.error(f"Ошибка бронирования: {e}")
        bot.answer_callback_query(call.id, "❌ Ошибка записи")
        _send_booking_error(bot, config, call.message.chat.id)


def _send_booking_error(bot, config, chat_id):
    """Отправляет сообщение об ошибке записи"""
    error_text = "❌ Не удалось завершить запись.\n"
    error_text += f"Попробуйте позже или свяжитесь с тренеру: {config.ADMIN_CONTACT}"
    bot.send_message(chat_id, error_text, reply_markup=back_to_menu_keyboard())