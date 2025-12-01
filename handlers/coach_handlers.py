from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from database.models import Session, User, AthleteInfo
import logging

logger = logging.getLogger(__name__)

# Состояния для добавления спортсмена
ATHLETE_FULL_NAME, ATHLETE_PHONE, ATHLETE_MEDICAL, ATHLETE_AGE_GROUP, ATHLETE_SUBSCRIPTION = range(5)

# Состояния для редактирования спортсмена
EDIT_ATHLETE_START, EDIT_CHOOSE_FIELD, EDIT_INPUT_VALUE = range(3)


# ==================== МЕНЮ ТРЕНЕРА ====================

async def coach_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /menu для тренера"""
    session = Session()

    try:
        from database.db_utils import get_user_by_telegram_id
        user = get_user_by_telegram_id(session, update.effective_user.id)

        if not user or user.role != 'coach':
            await update.message.reply_text(
                "❌ У вас нет прав тренера.\n"
                "Обратитесь к администратору."
            )
            return

        # Клавиатура меню тренера
        keyboard = [
            ["👥 Добавить спортсмена", "📋 Список спортсменов"],
            ["📅 Отметить посещение", "📊 Статистика посещений"],
            ["💰 Финансовая статистика", "⚙️ Настройки"]
        ]

        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

        await update.message.reply_text(
            "🏋️ *Панель тренера*\n\n"
            "Выберите действие:",
            parse_mode='Markdown',
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Ошибка в coach_menu: {e}")
        await update.message.reply_text("❌ Ошибка при загрузке меню.")
    finally:
        session.close()


# ==================== ДОБАВЛЕНИЕ СПОРТСМЕНА ====================

async def add_athlete_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало добавления спортсмена"""
    await update.message.reply_text(
        "👤 *Добавление нового спортсмена*\n\n"
        "Введите ФИО спортсмена:",
        parse_mode='Markdown'
    )
    return ATHLETE_FULL_NAME


async def add_athlete_full_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка ФИО спортсмена"""
    context.user_data['full_name'] = update.message.text

    await update.message.reply_text(
        "📞 *Введите номер телефона спортсмена:*\n"
        "Формат: +79991234567 или 89991234567",
        parse_mode='Markdown'
    )
    return ATHLETE_PHONE


async def add_athlete_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка телефона спортсмена"""
    phone = update.message.text

    # Простая валидация телефона
    if (phone.startswith('+') and len(phone) == 12) or \
            (phone.startswith('8') and len(phone) == 11) or \
            (phone.startswith('7') and len(phone) == 11):
        context.user_data['phone'] = phone

        await update.message.reply_text(
            "🏥 *Медицинские заметки:*\n"
            "Введите медицинские ограничения или заметки\n"
            "(или напишите 'нет' если нет ограничений):",
            parse_mode='Markdown'
        )
        return ATHLETE_MEDICAL
    else:
        await update.message.reply_text(
            "❌ *Неверный формат телефона!*\n"
            "Пожалуйста, введите телефон в формате:\n"
            "+79991234567 или 89991234567",
            parse_mode='Markdown'
        )
        return ATHLETE_PHONE


async def add_athlete_medical(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка медицинских заметок"""
    context.user_data['medical_notes'] = update.message.text

    await update.message.reply_text(
        "👶 *Возрастная группа:*\n"
        "Введите возрастную группу:\n"
        "• Дети (до 12 лет)\n"
        "• Подростки (13-17 лет)\n"
        "• Взрослые (18+ лет)",
        parse_mode='Markdown'
    )
    return ATHLETE_AGE_GROUP


async def add_athlete_age_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка возрастной группы"""
    context.user_data['age_group'] = update.message.text

    await update.message.reply_text(
        "💰 *Тип абонемента:*\n"
        "Введите тип абонемента:\n"
        "• Разовое посещение\n"
        "• Месячный абонемент\n"
        "• Годовой абонемент",
        parse_mode='Markdown'
    )
    return ATHLETE_SUBSCRIPTION


async def add_athlete_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка типа абонемента и сохранение спортсмена"""
    context.user_data['subscription_type'] = update.message.text

    session = Session()

    try:
        from database.db_utils import get_user_by_telegram_id, create_user, create_athlete_info

        # Получаем тренера
        coach = get_user_by_telegram_id(session, update.effective_user.id)

        if not coach or coach.role != 'coach':
            await update.message.reply_text("❌ Ошибка: вы не являетесь тренером.")
            return ConversationHandler.END

        # Создаем спортсмена
        athlete = create_user(
            session=session,
            telegram_id=None,  # У спортсмена может не быть Telegram
            username=None,
            first_name=context.user_data['full_name'],
            phone=context.user_data['phone'],
            role='athlete',
            sport_type='MMA'  # По умолчанию MMA
        )

        # Создаем информацию о спортсмене
        create_athlete_info(
            session=session,
            user_id=athlete.id,
            medical_notes=context.user_data['medical_notes'],
            age_group=context.user_data['age_group'],
            subscription_type=context.user_data['subscription_type']
        )

        await update.message.reply_text(
            f"✅ *Спортсмен успешно добавлен!*\n\n"
            f"*ФИО:* {context.user_data['full_name']}\n"
            f"*Телефон:* {context.user_data['phone']}\n"
            f"*Возрастная группа:* {context.user_data['age_group']}\n"
            f"*Абонемент:* {context.user_data['subscription_type']}\n\n"
            f"Спортсмен добавлен в ваш список.",
            parse_mode='Markdown'
        )

        # Очищаем данные
        context.user_data.clear()

        return ConversationHandler.END

    except Exception as e:
        logger.error(f"Ошибка при добавлении спортсмена: {e}")
        await update.message.reply_text(
            "❌ *Ошибка при сохранении спортсмена!*\n"
            "Попробуйте еще раз или обратитесь к администратору.",
            parse_mode='Markdown'
        )
        return ConversationHandler.END
    finally:
        session.close()


async def cancel_athlete_creation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отмена добавления спортсмена"""
    context.user_data.clear()

    await update.message.reply_text(
        "❌ Добавление спортсмена отменено.",
        parse_mode='Markdown'
    )

    # Показываем меню тренера
    return await coach_menu(update, context)


# ==================== СПИСОК И РЕДАКТИРОВАНИЕ СПОРТСМЕНОВ ====================

async def athletes_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Список спортсменов тренера с возможностью редактирования"""
    session = Session()

    try:
        from database.db_utils import get_user_by_telegram_id, get_coach_athletes

        coach = get_user_by_telegram_id(session, update.effective_user.id)
        if not coach or coach.role != 'coach':
            await update.message.reply_text("❌ У вас нет доступа к этому разделу.")
            return

        athletes = get_coach_athletes(session, coach.telegram_id)

        if not athletes:
            await update.message.reply_text(
                "📭 *У вас пока нет спортсменов.*\n"
                "Добавьте спортсменов через меню '👥 Добавить спортсмена'",
                parse_mode='Markdown'
            )
            return

        await update.message.reply_text(
            f"📋 *Список ваших спортсменов:*\n"
            f"Всего: *{len(athletes)}* спортсменов\n\n"
            f"Выберите спортсмена для редактирования:",
            parse_mode='Markdown'
        )

        for athlete in athletes:
            # Получаем дополнительную информацию
            athlete_info = session.query(AthleteInfo).filter_by(user_id=athlete.id).first()

            message = f"👤 *{athlete.first_name}*\n"
            message += f"📞 `{athlete.phone or 'Не указан'}`\n"

            if athlete_info:
                message += f"🏥 Мед. заметки: {athlete_info.medical_notes[:50] if athlete_info.medical_notes else 'Нет'}\n"
                message += f"👶 Группа: {athlete_info.age_group or 'Не указана'}\n"
                message += f"💰 Абонемент: {athlete_info.subscription_type or 'Не указан'}\n"

            message += f"🏷️ ID: `{athlete.id}`\n"
            message += f"📅 Зарегистрирован: {athlete.created_at.strftime('%d.%m.%Y')}\n"

            # Кнопки для каждого спортсмена
            keyboard = [
                [
                    InlineKeyboardButton("✏️ Редактировать",
                                         callback_data=f"edit_athlete_{athlete.id}"),
                    InlineKeyboardButton("📅 Тренировки",
                                         callback_data=f"trainings_{athlete.id}")
                ]
            ]

            reply_markup = InlineKeyboardMarkup(keyboard)

            await update.message.reply_text(
                message,
                parse_mode='Markdown',
                reply_markup=reply_markup
            )

    except Exception as e:
        logger.error(f"Ошибка в athletes_list: {e}")
        await update.message.reply_text(
            "❌ Произошла ошибка при загрузке списка спортсменов."
        )
    finally:
        session.close()


async def edit_athlete_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало редактирования спортсмена"""
    query = update.callback_query
    await query.answer()

    athlete_id = int(query.data.split('_')[2])
    context.user_data['edit_athlete_id'] = athlete_id

    session = Session()
    try:
        athlete = session.query(User).filter_by(id=athlete_id).first()

        if not athlete:
            await query.edit_message_text("❌ Спортсмен не найден.")
            return ConversationHandler.END

        athlete_info = session.query(AthleteInfo).filter_by(user_id=athlete_id).first()

        # Сохраняем данные спортсмена в context
        context.user_data['athlete_name'] = athlete.first_name

        # Клавиатура выбора поля для редактирования
        keyboard = [
            [InlineKeyboardButton("👤 Имя", callback_data='edit_field_first_name')],
            [InlineKeyboardButton("📞 Телефон", callback_data='edit_field_phone')],
            [InlineKeyboardButton("🏥 Мед. заметки", callback_data='edit_field_medical')],
            [InlineKeyboardButton("👶 Возрастная группа", callback_data='edit_field_age_group')],
            [InlineKeyboardButton("💰 Абонемент", callback_data='edit_field_subscription')],
            [InlineKeyboardButton("❌ Отмена", callback_data='edit_cancel')]
        ]

        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(
            f"✏️ *Редактирование спортсмена:* {athlete.first_name}\n\n"
            f"*Текущие данные:*\n"
            f"👤 Имя: {athlete.first_name}\n"
            f"📞 Телефон: `{athlete.phone or 'Не указан'}`\n"
            f"🏥 Мед. заметки: {athlete_info.medical_notes[:100] + '...' if athlete_info and athlete_info.medical_notes and len(athlete_info.medical_notes) > 100 else (athlete_info.medical_notes if athlete_info and athlete_info.medical_notes else 'Нет')}\n"
            f"👶 Группа: {athlete_info.age_group if athlete_info else 'Не указана'}\n"
            f"💰 Абонемент: {athlete_info.subscription_type if athlete_info else 'Не указан'}\n\n"
            f"Выберите что хотите отредактировать:",
            parse_mode='Markdown',
            reply_markup=reply_markup
        )

        return EDIT_CHOOSE_FIELD

    except Exception as e:
        logger.error(f"Ошибка в edit_athlete_start: {e}")
        await query.edit_message_text("❌ Ошибка при загрузке данных спортсмена.")
        return ConversationHandler.END
    finally:
        session.close()


async def edit_choose_field(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выбор поля для редактирования"""
    query = update.callback_query
    await query.answer()

    if query.data == 'edit_cancel':
        await query.edit_message_text("❌ Редактирование отменено.")
        return ConversationHandler.END

    # Определяем выбранное поле
    field_map = {
        'edit_field_first_name': 'Имя',
        'edit_field_phone': 'Телефон',
        'edit_field_medical': 'Медицинские заметки',
        'edit_field_age_group': 'Возрастная группа',
        'edit_field_subscription': 'Тип абонемента'
    }

    field_key = query.data
    field_name = field_map.get(field_key)

    if not field_name:
        await query.edit_message_text("❌ Неизвестное поле.")
        return ConversationHandler.END

    # Сохраняем выбранное поле в context
    context.user_data['edit_field'] = field_key
    context.user_data['edit_field_name'] = field_name

    # Подготавливаем сообщение в зависимости от поля
    messages = {
        'edit_field_first_name': "✏️ *Введите новое имя спортсмена:*",
        'edit_field_phone': "📞 *Введите новый телефон спортсмена:*\nФормат: +79991234567 или 89991234567",
        'edit_field_medical': "🏥 *Введите новые медицинские заметки:*",
        'edit_field_age_group': "👶 *Введите новую возрастную группу:*\n(Дети, Подростки, Взрослые)",
        'edit_field_subscription': "💰 *Введите новый тип абонемент:*\n(Разовое, Месячный, Годовой)"
    }

    message_text = messages.get(field_key, "Введите новое значение:")

    await query.edit_message_text(
        f"{message_text}\n\n"
        f"Спортсмен: *{context.user_data.get('athlete_name', 'Неизвестно')}*",
        parse_mode='Markdown'
    )

    return EDIT_INPUT_VALUE


async def edit_input_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка введенного значения"""
    session = Session()

    try:
        from database.db_utils import update_user, update_athlete_info

        athlete_id = context.user_data.get('edit_athlete_id')
        field_key = context.user_data.get('edit_field')
        field_name = context.user_data.get('edit_field_name')

        if not athlete_id or not field_key:
            await update.message.reply_text("❌ Ошибка: данные не найдены.")
            return ConversationHandler.END

        new_value = update.message.text.strip()

        # Обработка в зависимости от типа поля
        if field_key == 'edit_field_first_name':
            update_user(session, athlete_id, first_name=new_value)
            message = f"✅ *Имя изменено на:* {new_value}"

        elif field_key == 'edit_field_phone':
            # Простая валидация телефона
            if (new_value.startswith('+') and len(new_value) == 12) or \
                    (new_value.startswith('8') and len(new_value) == 11) or \
                    (new_value.startswith('7') and len(new_value) == 11):
                update_user(session, athlete_id, phone=new_value)
                message = f"✅ *Телефон изменен на:* `{new_value}`"
            else:
                await update.message.reply_text(
                    "❌ *Неверный формат телефона!*\n"
                    "Используйте формат: +79991234567 или 89991234567\n"
                    "Попробуйте еще раз:",
                    parse_mode='Markdown'
                )
                return EDIT_INPUT_VALUE

        elif field_key == 'edit_field_medical':
            update_athlete_info(session, athlete_id, medical_notes=new_value)
            message = f"✅ *Медицинские заметки обновлены*"

        elif field_key == 'edit_field_age_group':
            update_athlete_info(session, athlete_id, age_group=new_value)
            message = f"✅ *Возрастная группа изменена на:* {new_value}"

        elif field_key == 'edit_field_subscription':
            update_athlete_info(session, athlete_id, subscription_type=new_value)
            message = f"✅ *Тип абонемента изменен на:* {new_value}"

        # Отправляем сообщение об успехе
        await update.message.reply_text(
            f"{message}\n\n"
            f"✏️ *Хотите отредактировать что-то еще?*",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Продолжить редактирование",
                                      callback_data=f"edit_athlete_{athlete_id}")],
                [InlineKeyboardButton("📋 Вернуться к списку",
                                      callback_data='back_to_list')]
            ])
        )

        return ConversationHandler.END

    except Exception as e:
        logger.error(f"Ошибка в edit_input_value: {e}")
        await update.message.reply_text("❌ Ошибка при сохранении изменений.")
        return ConversationHandler.END
    finally:
        session.close()


async def edit_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отмена редактирования"""
    context.user_data.clear()
    await update.message.reply_text("❌ Редактирование отменено.")
    return ConversationHandler.END


async def back_to_list_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Возврат к списку спортсменов через callback"""
    query = update.callback_query
    await query.answer()

    # Очищаем данные редактирования
    context.user_data.clear()

    # Вызываем функцию отображения списка
    await athletes_list(query, context)
    return ConversationHandler.END