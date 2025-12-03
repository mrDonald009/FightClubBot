import logging
from datetime import datetime
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes, CallbackQueryHandler, CommandHandler
from database.models import Session, Athlete, Subscription, Training, Attendance, User
from database.db_utils import get_user_by_telegram_id, get_athlete_card_info
import html

logger = logging.getLogger(__name__)


async def show_athlete_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать карточку спортсмена"""
    query = update.callback_query
    if query:
        await query.answer()
        user_id = query.from_user.id
        # Получаем athlete_id из callback_data: athlete_123
        athlete_id = int(query.data.replace("athlete_", ""))
    else:
        user_id = update.effective_user.id
        # Получаем athlete_id из аргументов команды
        if context.args and len(context.args) > 0:
            try:
                athlete_id = int(context.args[0])
            except ValueError:
                await update.message.reply_text("❌ Неверный ID спортсмена")
                return
        else:
            await update.message.reply_text("❌ Укажите ID спортсмена: /card <ID>")
            return

    session = Session()
    try:
        user = get_user_by_telegram_id(session, user_id)

        if not user or user.role not in ['coach', 'admin']:
            if query:
                await query.edit_message_text("❌ У вас нет доступа")
            else:
                await update.message.reply_text("❌ У вас нет доступа")
            return

        # Получаем информацию для карточки
        card_info = get_athlete_card_info(session, athlete_id)

        if not card_info:
            if query:
                await query.edit_message_text("❌ Спортсмен не найден")
            else:
                await update.message.reply_text("❌ Спортсмен не найден")
            return

        athlete = card_info['athlete']
        subscription = card_info['subscription']
        stats = card_info['stats']

        # Проверяем права (тренер может видеть только своих спортсменов)
        if user.role == 'coach' and athlete.created_by != user.id:
            if query:
                await query.edit_message_text("❌ Вы не можете просматривать этого спортсмена")
            else:
                await update.message.reply_text("❌ Вы не можете просматривать этого спортсмена")
            return

        # Формируем сообщение
        message = f"👤 <b>КАРТОЧКА СПОРТСМЕНА</b>\n\n"
        message += f"<b>{html.escape(athlete.full_name)}</b>\n"
        message += f"📞 {athlete.phone}\n"
        message += f"🥊 {athlete.sport_type} | {card_info['age_group_display']}\n"
        message += f"👨‍🏫 Тренер: {athlete.coach.first_name if athlete.coach else 'Не указан'}\n"
        message += f"📅 В клубе с: {athlete.created_at.strftime('%d.%m.%Y')}\n\n"

        message += f"<b>📊 СТАТИСТИКА (30 дней)</b>\n"
        message += f"• Посещено: {stats['attended_trainings']}/{stats['total_trainings']}\n"
        message += f"• Пропущено: {stats['missed_trainings']}\n"
        message += f"• Посещаемость: {stats['attendance_rate']}%\n\n"

        message += f"<b>🏥 МЕДИЦИНСКАЯ ИНФОРМАЦИЯ</b>\n"
        message += f"{card_info['medical_display'] or '—'}\n\n"

        message += f"<b>🎫 АБОНЕМЕНТ</b>\n"
        if subscription:
            status = "✅ Активен" if subscription.is_active else "❌ Неактивен"
            trainings = f"{subscription.trainings_remaining}/{subscription.trainings_total}"
            if subscription.total_restored > 0:
                trainings += f" (🔄 +{subscription.total_restored})"
            sub_type = "Месячный" if subscription.subscription_type == "monthly" else "Разовый"
            end_date = subscription.end_date.strftime("%d.%m.%Y") if subscription.end_date else "—"

            message += f"• Статус: {status}\n"
            message += f"• Тип: {sub_type}\n"
            message += f"• Тренировки: {trainings}\n"
            message += f"• Действует до: {end_date}\n"
        else:
            message += f"• ❌ Нет активного абонемента\n"

        message += f"\n🆔 ID: {athlete_id}"
        if not stats['has_telegram']:
            message += f"\n⚠️ У спортсмена нет Telegram аккаунта"

        # Создаем инлайн клавиатуру
        keyboard = []

        # Первый ряд: основные действия
        keyboard.append([
            InlineKeyboardButton("🎫 Абонемент", callback_data=f"subscription_{athlete_id}"),
            InlineKeyboardButton("📅 Посещения", callback_data=f"visits_{athlete_id}")
        ])

        # Второй ряд: восстановление и статистика
        keyboard.append([
            InlineKeyboardButton("📊 Статистика", callback_data=f"stats_{athlete_id}"),
            InlineKeyboardButton("🔄 Восстановить", callback_data=f"restore_{athlete_id}")
        ])

        # Третий ряд: редактирование и отметка
        keyboard.append([
            InlineKeyboardButton("✏️ Редактировать", callback_data=f"edit_{athlete_id}"),
            InlineKeyboardButton("📅 Отметить", callback_data=f"mark_{athlete_id}")
        ])

        # Четвертый ряд: навигация
        keyboard.append([
            InlineKeyboardButton("📋 К списку", callback_data="back_to_list"),
            InlineKeyboardButton("🏠 В меню", callback_data="back_to_menu")
        ])

        reply_markup = InlineKeyboardMarkup(keyboard)

        if query:
            await query.edit_message_text(
                message,
                reply_markup=reply_markup,
                parse_mode='HTML'
            )
        else:
            await update.message.reply_text(
                message,
                reply_markup=reply_markup,
                parse_mode='HTML'
            )

    except Exception as e:
        logger.error(f"❌ ОШИБКА ПРИ ПОКАЗЕ КАРТОЧКИ: {e}")
        if query:
            await query.edit_message_text("❌ Ошибка при загрузке карточки")
        else:
            await update.message.reply_text("❌ Ошибка при загрузке карточки")
    finally:
        session.close()


async def show_subscription_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать детальную информацию об абонементе"""
    query = update.callback_query
    await query.answer()

    athlete_id = int(query.data.replace("subscription_", ""))

    session = Session()
    try:
        user = get_user_by_telegram_id(session, query.from_user.id)

        if not user or user.role not in ['coach', 'admin']:
            await query.edit_message_text("❌ У вас нет доступа")
            return

        # Получаем информацию о спортсмене
        card_info = get_athlete_card_info(session, athlete_id)

        if not card_info:
            await query.edit_message_text("❌ Спортсмен не найден")
            return

        athlete = card_info['athlete']
        subscription = card_info['subscription']

        # Проверяем права
        if user.role == 'coach' and athlete.created_by != user.id:
            await query.edit_message_text("❌ Вы не можете просматривать этого спортсмена")
            return

        if not subscription:
            # Если нет абонемента
            keyboard = [
                [InlineKeyboardButton("➕ Создать абонемент", callback_data=f"create_sub_{athlete_id}")],
                [InlineKeyboardButton("🔙 Назад", callback_data=f"athlete_{athlete_id}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await query.edit_message_text(
                f"👤 <b>{html.escape(athlete.full_name)}</b>\n\n"
                f"❌ У спортсмена нет активного абонемента.\n\n"
                f"Создайте абонемент для продолжения работы.",
                reply_markup=reply_markup,
                parse_mode='HTML'
            )
            return

        # Расчет прогресса использования
        used = subscription.trainings_total - subscription.trainings_remaining
        usage_percent = round((used / subscription.trainings_total) * 100, 1) if subscription.trainings_total > 0 else 0

        # Создаем визуальный прогресс-бар
        progress_length = 15
        filled = int(usage_percent * progress_length / 100)
        progress_bar = "█" * filled + "░" * (progress_length - filled)

        # Формируем сообщение
        message = f"🎫 <b>АБОНЕМЕНТ СПОРТСМЕНА</b>\n\n"
        message += f"👤 <b>{html.escape(athlete.full_name)}</b>\n\n"

        message += f"<b>📋 ОСНОВНАЯ ИНФОРМАЦИЯ</b>\n"
        message += f"• Тип: {'Месячный' if subscription.subscription_type == 'monthly' else 'Разовый'}\n"
        message += f"• Статус: {'✅ Активен' if subscription.is_active else '❌ Неактивен'}\n"
        message += f"• Начало: {subscription.start_date.strftime('%d.%m.%Y')}\n"

        if subscription.end_date:
            days_left = (subscription.end_date - datetime.utcnow()).days
            message += f"• Окончание: {subscription.end_date.strftime('%d.%m.%Y')}\n"
            message += f"• Дней осталось: {days_left if days_left > 0 else 0}\n"
        else:
            message += f"• Окончание: —\n"

        message += f"\n<b>🏋️ ТРЕНИРОВКИ</b>\n"
        message += f"• Всего: {subscription.trainings_total}\n"
        message += f"• Использовано: {used}\n"
        message += f"• Осталось: {subscription.trainings_remaining}\n"

        if subscription.total_restored > 0:
            message += f"• Восстановлено: {subscription.total_restored}\n"
            if subscription.restored_this_month > 0:
                message += f"• Восстановлено в этом месяце: {subscription.restored_this_month}\n"

        message += f"\n<b>📊 ИСПОЛЬЗОВАНИЕ</b>\n"
        message += f"{progress_bar} {usage_percent}%\n\n"

        message += f"<b>🔄 ДОСТУПНЫЕ ДЕЙСТВИЯ</b>\n"
        message += f"Выберите действие для управления абонементом:"

        # Создаем инлайн клавиатуру
        keyboard = []

        if subscription.is_active:
            keyboard.append([
                InlineKeyboardButton("📈 История списаний", callback_data=f"history_{athlete_id}"),
                InlineKeyboardButton("🔄 Восстановить", callback_data=f"restore_{athlete_id}")
            ])

            # Проверяем, есть ли тренировки для списания
            if subscription.trainings_remaining > 0:
                keyboard.append([
                    InlineKeyboardButton("📅 Отметить посещение", callback_data=f"mark_{athlete_id}")
                ])
        else:
            keyboard.append([
                InlineKeyboardButton("✅ Активировать", callback_data=f"activate_{athlete_id}"),
                InlineKeyboardButton("➕ Новый абонемент", callback_data=f"new_sub_{athlete_id}")
            ])

        keyboard.append([
            InlineKeyboardButton("📊 Статистика использования", callback_data=f"sub_stats_{athlete_id}"),
            InlineKeyboardButton("📋 История абонементов", callback_data=f"sub_history_{athlete_id}")
        ])

        keyboard.append([
            InlineKeyboardButton("🔙 Назад к карточке", callback_data=f"athlete_{athlete_id}"),
            InlineKeyboardButton("🏠 В меню", callback_data="back_to_menu")
        ])

        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(
            message,
            reply_markup=reply_markup,
            parse_mode='HTML'
        )

    except Exception as e:
        logger.error(f"❌ ОШИБКА ПРИ ПОКАЗЕ АБОНЕМЕНТА: {e}")
        await query.edit_message_text("❌ Ошибка при загрузке абонемента")
    finally:
        session.close()


async def handle_back_to_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Вернуться к списку спортсменов"""
    query = update.callback_query
    await query.answer()

    from handlers.coach_handlers import athletes_list
    await athletes_list(update, context)


async def handle_back_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Вернуться в главное меню"""
    query = update.callback_query
    await query.answer()

    from handlers.coach_handlers import coach_menu
    await coach_menu(update, context)