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
            # Проверяем статус абонемента
            from utils.subscription_checker import SubscriptionChecker
            status_display = SubscriptionChecker.format_subscription_status(subscription)

            trainings_remaining = subscription.trainings_remaining or 0
            trainings_total = subscription.trainings_total or 0
            trainings = f"{trainings_remaining}/{trainings_total}" if trainings_total else "—/—"
            if subscription.total_restored > 0:
                trainings += f" (🔄 +{subscription.total_restored})"
            sub_type = "Месячный" if subscription.subscription_type == "monthly" else "Разовый" if subscription.subscription_type == "single" else "Тип не определен"
            end_date = subscription.end_date.strftime("%d.%m.%Y") if subscription.end_date else "—"

            # Добавляем информацию о том, когда истек
            if subscription.end_date and subscription.end_date < datetime.utcnow():
                days_expired = (datetime.utcnow() - subscription.end_date).days
                status_display = f"🔴 Истек {days_expired} дней назад"

            message += f"• Статус: {status_display}\n"
            message += f"• Тип: {sub_type}\n"
            message += f"• Тренировки: {trainings}\n"
            message += f"• Действует до: {end_date}\n"

            # Добавляем информацию о создании
            if subscription.created_at:
                message += f"• Активирован: {subscription.created_at.strftime('%d.%m.%Y')}\n"
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
    """Показать детальную информацию об абонементе или список абонементов"""
    query = update.callback_query
    await query.answer()

    # Парсим callback_data: subscription_athlete_123 или subscription_123
    callback_data = query.data.replace("subscription_", "")
    if callback_data.startswith("athlete_"):
        athlete_id = int(callback_data.replace("athlete_", ""))
        subscription_id = None
    else:
        # Если передан subscription_id напрямую
        try:
            subscription_id = int(callback_data)
            subscription = None
        except ValueError:
            athlete_id = int(callback_data)
            subscription_id = None

    session = Session()
    try:
        user = get_user_by_telegram_id(session, query.from_user.id)

        if not user or user.role not in ['coach', 'admin']:
            await query.edit_message_text("❌ У вас нет доступа")
            return

        # Если передан subscription_id, получаем абонемент напрямую
        if subscription_id:
            from database.models import Subscription
            subscription = session.query(Subscription).filter_by(id=subscription_id).first()
            if not subscription:
                await query.edit_message_text("❌ Абонемент не найден")
                return
            athlete_id = subscription.athlete_id
            athlete = subscription.athlete
        else:
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

        # Если нет конкретного абонемента, показываем список всех абонементов
        if not subscription:
            from services.subscription_service import SubscriptionService
            all_subscriptions = SubscriptionService.get_athlete_subscriptions(session, athlete_id)
            
            if not all_subscriptions:
                # Если нет абонементов, показываем кнопку создания абонемента
                keyboard = [
                    [InlineKeyboardButton("✅ Активировать", callback_data=f"activate_sub_new_{athlete_id}")],
                    [InlineKeyboardButton("🔙 Назад к карточке", callback_data=f"athlete_{athlete_id}")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)

                await query.edit_message_text(
                    f"👤 <b>{html.escape(athlete.full_name)}</b>\n\n"
                    f"❌ У спортсмена нет абонемента.\n\n"
                    f"Нажмите 'Активировать' для создания и активации абонемента.",
                    reply_markup=reply_markup,
                    parse_mode='HTML'
                )
                return
            
            # Показываем список абонементов
            message = f"👤 <b>{html.escape(athlete.full_name)}</b>\n\n"
            message += f"🎫 <b>АБОНЕМЕНТЫ</b>\n\n"
            
            keyboard = []
            from utils.subscription_checker import SubscriptionChecker
            for sub in sorted(all_subscriptions, key=lambda s: s.created_at or datetime.min, reverse=True):
                status = SubscriptionChecker.get_subscription_status(sub)
                status_icon = "🟢" if status == "active" else "🔴" if status == "expired" else "⚪"
                sport_type_display = sub.sport_type or "—"
                sub_type = "Месячный" if sub.subscription_type == "monthly" else "Разовый"
                start_date_str = sub.start_date.strftime('%d.%m.%Y') if sub.start_date else "—"
                
                button_text = f"{status_icon} {sport_type_display} | {sub_type} | {start_date_str}"
                if len(button_text) > 64:
                    button_text = f"{status_icon} {sport_type_display} | {sub_type}"
                
                keyboard.append([
                    InlineKeyboardButton(button_text, callback_data=f"subscription_{sub.id}")
                ])
            
            keyboard.append([
                InlineKeyboardButton("📜 История абонемента", callback_data=f"subscription_history_{athlete_id}")
            ])
            keyboard.append([
                InlineKeyboardButton("🔙 Назад к карточке", callback_data=f"athlete_{athlete_id}")
            ])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(message, reply_markup=reply_markup, parse_mode='HTML')
            return

        # Автоматически списываем тренировки по расписанию для активного абонемента
        if subscription.is_active:
            from database.db_utils import auto_deduct_daily_trainings, migrate_existing_subscription
            migrate_existing_subscription(session, subscription.id)
            auto_deduct_daily_trainings(session)

        # Получаем статистику использованных/неиспользованных тренировок
        from database.models import Attendance
        used_trainings = session.query(Attendance).filter_by(
            subscription_id=subscription.id,
            attended=True
        ).count()
        
        unused_trainings = session.query(Attendance).filter_by(
            subscription_id=subscription.id,
            attended=False
        ).count()
        
        # Расчет прогресса использования
        total_deducted = used_trainings + unused_trainings
        trainings_total = subscription.trainings_total or 0
        trainings_remaining = subscription.trainings_remaining or 0
        usage_percent = round((total_deducted / trainings_total) * 100, 1) if trainings_total > 0 else 0

        # Формируем сообщение
        message = f"🎫 <b>АБОНЕМЕНТ СПОРТСМЕНА</b>\n\n"
        message += f"👤 <b>{html.escape(athlete.full_name)}</b>\n\n"

        message += f"<b>📋 ОСНОВНАЯ ИНФОРМАЦИЯ</b>\n"
        message += f"• Вид спорта: {subscription.sport_type or '—'}\n"
        if subscription.subscription_type:
            sub_type_display = "Месячный" if subscription.subscription_type == "monthly" else "Разовый"
        else:
            sub_type_display = "Тип не определен"
        message += f"• Тип: {sub_type_display}\n"

        # Используем наш новый checker для статуса
        from utils.subscription_checker import SubscriptionChecker
        status_display = SubscriptionChecker.format_subscription_status(subscription)
        message += f"• Статус: {status_display}\n"

        # Улучшенное отображение дат действия
        start_date_str = subscription.start_date.strftime('%d.%m.%Y %H:%M') if subscription.start_date else "—"
        message += f"• Дата начала: {start_date_str}\n"
        
        if subscription.end_date:
            end_date_str = subscription.end_date.strftime('%d.%m.%Y %H:%M')
            days_left = (subscription.end_date - datetime.utcnow()).days
            message += f"• Дата окончания: {end_date_str}\n"
            
            # Период действия
            if subscription.start_date:
                period_days = (subscription.end_date - subscription.start_date).days
                message += f"• Период действия: {period_days} дней\n"
            
            # Осталось дней
            if days_left > 0:
                message += f"• ⏰ Осталось дней: {days_left}\n"
            elif days_left == 0:
                message += f"• ⚠️ Истекает сегодня\n"
            else:
                expired_days = abs(days_left)
                message += f"• 🔴 Истек {expired_days} дн. назад\n"
        else:
            message += f"• Дата окончания: —\n"
        
        # Дата создания абонемента
        if subscription.created_at:
            created_str = subscription.created_at.strftime('%d.%m.%Y %H:%M')
            message += f"• Создан: {created_str}\n"

        message += f"\n<b>🏋️ ТРЕНИРОВКИ</b>\n"
        if trainings_total is not None:
            message += f"• Всего: {trainings_total}\n"
            message += f"• Использовано: {used_trainings}\n"
            message += f"• Неиспользовано: {unused_trainings}\n"
            message += f"• Осталось: {trainings_remaining}\n"
        else:
            message += f"• Всего: —\n"
            message += f"• Использовано: {used_trainings}\n"
            message += f"• Неиспользовано: {unused_trainings}\n"
            message += f"• Осталось: —\n"

        if subscription.total_restored > 0:
            message += f"• Восстановлено: {subscription.total_restored}\n"
            if subscription.restored_this_month > 0:
                message += f"• Восстановлено в этом месяце: {subscription.restored_this_month}\n"

        # Создаем инлайн клавиатуру
        keyboard = []

        # Кнопка активации (только если абонемент неактивен)
        if not subscription.is_active:
            keyboard.append([
                InlineKeyboardButton("✅ Активировать", callback_data=f"activate_sub_{subscription.id}")
            ])

        # Кнопка истории абонементов
        keyboard.append([
            InlineKeyboardButton("📜 История абонемента", callback_data=f"subscription_history_{athlete_id}")
        ])

        keyboard.append([
            InlineKeyboardButton("🔙 Назад к карточке", callback_data=f"athlete_{athlete_id}")
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

    # Возвращаемся в последний выбранный фильтр (если был), иначе в экран категорий
    filter_key = context.user_data.get("athletes_list_filter")
    if filter_key in ("all", "children", "adults", "inactive", "active_children", "active_adults", "inactive_children", "inactive_adults"):
        from handlers.coach_handlers import show_athletes_list_by_filter
        await show_athletes_list_by_filter(update, context, filter_key)
    else:
        from handlers.coach_handlers import athletes_list
        await athletes_list(update, context)


async def handle_back_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Вернуться в главное меню"""
    query = update.callback_query
    await query.answer()

    from handlers.coach_handlers import coach_menu
    await coach_menu(update, context)


async def show_my_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать абонемент спортсмена (для самого спортсмена)"""
    query = update.callback_query
    message = update.message
    
    user_id = query.from_user.id if query else update.effective_user.id
    
    if query:
        await query.answer()
    
    session = Session()
    try:
        user = get_user_by_telegram_id(session, user_id)
        
        if not user:
            error_msg = "❌ Пользователь не найден"
            if query:
                await query.edit_message_text(error_msg)
            elif message:
                await message.reply_text(error_msg)
            return
        
        # Проверяем, что это спортсмен
        if user.role not in ['athlete']:
            error_msg = "❌ Эта функция доступна только для спортсменов"
            if query:
                await query.edit_message_text(error_msg)
            elif message:
                await message.reply_text(error_msg)
            return
        
        # Получаем спортсмена по user_id
        athlete = session.query(Athlete).filter_by(user_id=user.id).first()
        
        if not athlete:
            error_msg = "❌ Профиль спортсмена не найден. Обратитесь к тренеру."
            if query:
                await query.edit_message_text(error_msg)
            elif message:
                await message.reply_text(error_msg)
            return
        
        # Получаем информацию об абонементе
        subscription = athlete.current_subscription
        
        # Автоматически списываем тренировки по расписанию для активного абонемента
        if subscription and subscription.is_active:
            from database.db_utils import auto_deduct_daily_trainings, migrate_existing_subscription
            # Применяем миграцию к существующим абонементам (если нужно)
            migrate_existing_subscription(session, subscription.id)
            # Автоматически списываем тренировки за сегодня
            auto_deduct_daily_trainings(session)
            # Обновляем subscription из БД
            session.refresh(subscription)
        
        # Формируем сообщение
        message_text = f"🎫 <b>МОЙ АБОНЕМЕНТ</b>\n\n"
        message_text += f"👤 <b>{html.escape(athlete.full_name)}</b>\n"
        message_text += f"🥊 {athlete.sport_type or 'Не указан'}\n\n"
        
        if not subscription:
            message_text += f"❌ У вас нет активного абонемента.\n\n"
            message_text += f"Обратитесь к тренеру для оформления абонемента."
            
            keyboard = [
                [InlineKeyboardButton("🏠 В меню", callback_data="athlete_back_to_menu")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            if query:
                await query.edit_message_text(message_text, reply_markup=reply_markup, parse_mode='HTML')
            elif message:
                await message.reply_text(message_text, reply_markup=reply_markup, parse_mode='HTML')
            return
        
        # Получаем статистику использованных/неиспользованных тренировок
        used_trainings = session.query(Attendance).filter_by(
            subscription_id=subscription.id,
            attended=True
        ).count()
        
        unused_trainings = session.query(Attendance).filter_by(
            subscription_id=subscription.id,
            attended=False
        ).count()
        
        # Расчет прогресса использования
        total_deducted = used_trainings + unused_trainings
        trainings_total = subscription.trainings_total or 0
        usage_percent = round((total_deducted / trainings_total) * 100, 1) if trainings_total > 0 else 0
        
        # Создаем визуальный прогресс-бар
        progress_length = 15
        filled = int(usage_percent * progress_length / 100)
        progress_bar = "█" * filled + "░" * (progress_length - filled)
        
        message_text += f"<b>📋 ОСНОВНАЯ ИНФОРМАЦИЯ</b>\n"
        if subscription.subscription_type:
            sub_type_display = "Месячный" if subscription.subscription_type == "monthly" else "Разовый"
        else:
            sub_type_display = "Тип не определен"
        message_text += f"• Тип: {sub_type_display}\n"
        
        # Используем checker для статуса
        from utils.subscription_checker import SubscriptionChecker
        status_display = SubscriptionChecker.format_subscription_status(subscription)
        message_text += f"• Статус: {status_display}\n"
        
        # Улучшенное отображение дат действия
        start_date_str = subscription.start_date.strftime('%d.%m.%Y %H:%M') if subscription.start_date else "—"
        message_text += f"• Дата начала: {start_date_str}\n"
        
        if subscription.end_date:
            end_date_str = subscription.end_date.strftime('%d.%m.%Y %H:%M')
            days_left = (subscription.end_date - datetime.utcnow()).days
            message_text += f"• Дата окончания: {end_date_str}\n"
            
            # Период действия
            if subscription.start_date:
                period_days = (subscription.end_date - subscription.start_date).days
                message_text += f"• Период действия: {period_days} дней\n"
            
            # Осталось дней
            if days_left > 0:
                message_text += f"• ⏰ Осталось дней: {days_left}\n"
            elif days_left == 0:
                message_text += f"• ⚠️ Истекает сегодня\n"
            else:
                expired_days = abs(days_left)
                message_text += f"• 🔴 Истек {expired_days} дн. назад\n"
        else:
            message_text += f"• Дата окончания: —\n"
        
        # Дата создания абонемента
        if subscription.created_at:
            created_str = subscription.created_at.strftime('%d.%m.%Y %H:%M')
            message_text += f"• Создан: {created_str}\n"
        
        message_text += f"\n<b>🏋️ ТРЕНИРОВКИ</b>\n"
        trainings_total = subscription.trainings_total or 0
        trainings_remaining = subscription.trainings_remaining or 0
        if trainings_total is not None:
            message_text += f"• Всего: {trainings_total}\n"
            message_text += f"• Использовано: {used_trainings}\n"
            message_text += f"• Осталось: {trainings_remaining}\n"
        else:
            message_text += f"• Всего: —\n"
            message_text += f"• Использовано: {used_trainings}\n"
            message_text += f"• Осталось: —\n"
        
        if subscription.total_restored > 0:
            message_text += f"• Восстановлено: {subscription.total_restored}\n"
        
        # Прогресс-бар использования
        message_text += f"\n<b>📊 ИСПОЛЬЗОВАНИЕ</b>\n"
        message_text += f"{progress_bar} {usage_percent}%\n"
        
        # Создаем инлайн клавиатуру
        keyboard = [
            [InlineKeyboardButton("📜 История абонемента", callback_data=f"subscription_history_athlete_{athlete.id}")],
            [InlineKeyboardButton("🔄 Обновить", callback_data="athlete_subscription_refresh")],
            [InlineKeyboardButton("🏠 В меню", callback_data="athlete_back_to_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if query:
            await query.edit_message_text(message_text, reply_markup=reply_markup, parse_mode='HTML')
        elif message:
            await message.reply_text(message_text, reply_markup=reply_markup, parse_mode='HTML')
    
    except Exception as e:
        logger.error(f"❌ ОШИБКА ПРИ ПОКАЗЕ АБОНЕМЕНТА СПОРТСМЕНА: {e}", exc_info=True)
        error_msg = "❌ Ошибка при загрузке информации об абонементе"
        if query:
            await query.edit_message_text(error_msg)
        elif message:
            await message.reply_text(error_msg)
    finally:
        session.close()


async def handle_athlete_back_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Вернуться в меню спортсмена"""
    query = update.callback_query
    await query.answer()
    
    from handlers.start import show_athlete_menu
    await show_athlete_menu(update, context)


async def show_subscription_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать историю абонементов спортсмена"""
    query = update.callback_query
    await query.answer()
    
    # Получаем athlete_id из callback_data: subscription_history_123 или subscription_history_athlete_123
    callback_data = query.data.replace("subscription_history_", "")
    if callback_data.startswith("athlete_"):
        athlete_id = int(callback_data.replace("athlete_", ""))
    else:
        athlete_id = int(callback_data)
    
    session = Session()
    try:
        user = get_user_by_telegram_id(session, query.from_user.id)
        
        if not user:
            await query.edit_message_text("❌ Пользователь не найден")
            return
        
        # Если спортсмен смотрит свою историю, получаем athlete_id из user_id
        if user.role == 'athlete' and callback_data.startswith("athlete_"):
            athlete = session.query(Athlete).filter_by(user_id=user.id).first()
            if not athlete:
                await query.edit_message_text("❌ Профиль спортсмена не найден")
                return
            athlete_id = athlete.id
        else:
            # Получаем спортсмена по переданному ID
            athlete = session.query(Athlete).filter_by(id=athlete_id).first()
            if not athlete:
                await query.edit_message_text("❌ Спортсмен не найден")
                return
        
        # Проверяем права доступа
        is_athlete_viewing_own = (user.role == 'athlete' and athlete.user_id == user.id)
        is_coach_viewing_athlete = (user.role in ['coach', 'admin'] and 
                                   (user.role == 'admin' or athlete.created_by == user.id))
        
        if not (is_athlete_viewing_own or is_coach_viewing_athlete):
            await query.edit_message_text("❌ У вас нет доступа")
            return
        
        # Получаем абонемент спортсмена (связь один-к-одному)
        # Также ищем старые деактивированные абонементы через прямой запрос
        from services.subscription_service import SubscriptionService
        current_subscription = athlete.subscription
        
        # Ищем все абонементы этого спортсмена (включая деактивированные) через прямой запрос
        all_subscriptions = session.query(Subscription).filter_by(athlete_id=athlete_id).all()
        subscriptions = sorted(all_subscriptions, key=lambda s: s.created_at or datetime.min, reverse=True)
        
        if not subscriptions:
            keyboard = [
                [InlineKeyboardButton("🔙 Назад", callback_data=f"subscription_{athlete_id}" if is_coach_viewing_athlete else "athlete_back_to_menu")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                f"👤 <b>{html.escape(athlete.full_name)}</b>\n\n"
                f"📜 <b>ИСТОРИЯ АБОНЕМЕНТА</b>\n\n"
                f"❌ История абонемента пуста.",
                reply_markup=reply_markup,
                parse_mode='HTML'
            )
            return
        
        # Формируем сообщение с историей
        message = f"👤 <b>{html.escape(athlete.full_name)}</b>\n\n"
        message += f"📜 <b>ИСТОРИЯ АБОНЕМЕНТА</b>\n\n"
        message += f"Всего записей в истории: {len(subscriptions)}\n\n"
        
        # Создаем клавиатуру с кнопками для каждого абонемента
        keyboard = []
        
        for idx, sub in enumerate(subscriptions[:10], 1):  # Показываем первые 10
            # Определяем статус
            from utils.subscription_checker import SubscriptionChecker
            status = SubscriptionChecker.get_subscription_status(sub)
            
            if status == "active":
                status_icon = "🟢"
            elif status == "expired":
                status_icon = "🔴"
            else:
                status_icon = "⚪"
            
            # Форматируем даты
            start_date_str = sub.start_date.strftime('%d.%m.%Y') if sub.start_date else "—"
            end_date_str = sub.end_date.strftime('%d.%m.%Y') if sub.end_date else "—"
            
            sub_type = "Месячный" if sub.subscription_type == "monthly" else "Разовый"
            
            # Формируем текст кнопки
            button_text = f"{status_icon} #{sub.id} | {sub_type} | {start_date_str}"
            if len(button_text) > 64:  # Ограничение Telegram на длину текста кнопки
                button_text = f"{status_icon} #{sub.id} | {sub_type}"
            
            keyboard.append([
                InlineKeyboardButton(
                    button_text,
                    callback_data=f"view_sub_{sub.id}"
                )
            ])
            
            # Добавляем информацию в сообщение
            message += f"<b>{idx}. Абонемент #{sub.id}</b> {status_icon}\n"
            message += f"   Тип: {sub_type}\n"
            message += f"   Период: {start_date_str} — {end_date_str}\n"
            
            if sub.is_active:
                message += f"   Статус: Активен\n"
            else:
                message += f"   Статус: Неактивен\n"
            
            trainings_remaining = sub.trainings_remaining or 0
            trainings_total = sub.trainings_total or 0
            if trainings_total is not None:
                message += f"   Тренировки: {trainings_remaining}/{trainings_total}\n"
            else:
                message += f"   Тренировки: —/—\n"
            
            if sub.created_at:
                created_str = sub.created_at.strftime('%d.%m.%Y')
                message += f"   Создан: {created_str}\n"
            
            message += "\n"
        
        if len(subscriptions) > 10:
            message += f"\n... и еще {len(subscriptions) - 10} абонементов\n"
        
        # Кнопка назад
        if is_coach_viewing_athlete:
            keyboard.append([
                InlineKeyboardButton("🔙 Назад к абонементу", callback_data=f"subscription_{athlete_id}")
            ])
        else:
            # Для спортсмена - возврат к своему абонементу
            keyboard.append([
                InlineKeyboardButton("🔙 Назад к абонементу", callback_data="athlete_subscription_refresh")
            ])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            message,
            reply_markup=reply_markup,
            parse_mode='HTML'
        )
    
    except Exception as e:
        logger.error(f"❌ ОШИБКА ПРИ ПОКАЗЕ ИСТОРИИ АБОНЕМЕНТОВ: {e}", exc_info=True)
        await query.edit_message_text("❌ Ошибка при загрузке истории абонементов")
    finally:
        session.close()


async def view_subscription_from_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать детальную информацию об абонементе из истории"""
    query = update.callback_query
    await query.answer()
    
    # Получаем subscription_id из callback_data: view_sub_123
    subscription_id = int(query.data.replace("view_sub_", ""))
    
    session = Session()
    try:
        user = get_user_by_telegram_id(session, query.from_user.id)
        
        if not user:
            await query.edit_message_text("❌ Пользователь не найден")
            return
        
        # Получаем абонемент
        from services.subscription_service import SubscriptionService
        subscription = SubscriptionService.get_subscription_or_raise(session, subscription_id)
        athlete = subscription.athlete
        
        # Проверяем права доступа
        is_athlete_viewing_own = (user.role == 'athlete' and athlete.user_id == user.id)
        is_coach_viewing_athlete = (user.role in ['coach', 'admin'] and 
                                   (user.role == 'admin' or athlete.created_by == user.id))
        
        if not (is_athlete_viewing_own or is_coach_viewing_athlete):
            await query.edit_message_text("❌ У вас нет доступа")
            return
        
        # Получаем статистику использованных/неиспользованных тренировок
        from database.models import Attendance
        used_trainings = session.query(Attendance).filter_by(
            subscription_id=subscription.id,
            attended=True
        ).count()
        
        unused_trainings = session.query(Attendance).filter_by(
            subscription_id=subscription.id,
            attended=False
        ).count()
        
        # Расчет прогресса использования
        total_deducted = used_trainings + unused_trainings
        trainings_total = subscription.trainings_total or 0
        usage_percent = round((total_deducted / trainings_total) * 100, 1) if trainings_total > 0 else 0
        
        # Создаем визуальный прогресс-бар
        progress_length = 15
        filled = int(usage_percent * progress_length / 100)
        progress_bar = "█" * filled + "░" * (progress_length - filled)
        
        # Формируем сообщение
        message = f"🎫 <b>АБОНЕМЕНТ #{subscription.id}</b>\n\n"
        message += f"👤 <b>{html.escape(athlete.full_name)}</b>\n\n"
        
        message += f"<b>📋 ОСНОВНАЯ ИНФОРМАЦИЯ</b>\n"
        if subscription.subscription_type:
            sub_type_display = "Месячный" if subscription.subscription_type == "monthly" else "Разовый"
        else:
            sub_type_display = "Тип не определен"
        message += f"• Тип: {sub_type_display}\n"
        
        # Используем checker для статуса
        from utils.subscription_checker import SubscriptionChecker
        status_display = SubscriptionChecker.format_subscription_status(subscription)
        message += f"• Статус: {status_display}\n"
        
        # Улучшенное отображение дат действия
        start_date_str = subscription.start_date.strftime('%d.%m.%Y %H:%M') if subscription.start_date else "—"
        message += f"• Дата начала: {start_date_str}\n"
        
        if subscription.end_date:
            end_date_str = subscription.end_date.strftime('%d.%m.%Y %H:%M')
            days_left = (subscription.end_date - datetime.utcnow()).days
            message += f"• Дата окончания: {end_date_str}\n"
            
            # Период действия
            if subscription.start_date:
                period_days = (subscription.end_date - subscription.start_date).days
                message += f"• Период действия: {period_days} дней\n"
            
            # Осталось дней
            if days_left > 0:
                message += f"• ⏰ Осталось дней: {days_left}\n"
            elif days_left == 0:
                message += f"• ⚠️ Истекает сегодня\n"
            else:
                expired_days = abs(days_left)
                message += f"• 🔴 Истек {expired_days} дн. назад\n"
        else:
            message += f"• Дата окончания: —\n"
        
        # Дата создания абонемента
        if subscription.created_at:
            created_str = subscription.created_at.strftime('%d.%m.%Y %H:%M')
            message += f"• Создан: {created_str}\n"
        
        message += f"\n<b>🏋️ ТРЕНИРОВКИ</b>\n"
        trainings_total = subscription.trainings_total or 0
        trainings_remaining = subscription.trainings_remaining or 0
        if trainings_total is not None:
            message += f"• Всего: {trainings_total}\n"
            message += f"• Использовано: {used_trainings}\n"
            message += f"• Неиспользовано: {unused_trainings}\n"
            message += f"• Осталось: {trainings_remaining}\n"
        else:
            message += f"• Всего: —\n"
            message += f"• Использовано: {used_trainings}\n"
            message += f"• Неиспользовано: {unused_trainings}\n"
            message += f"• Осталось: —\n"
        
        if subscription.total_restored > 0:
            message += f"• Восстановлено: {subscription.total_restored}\n"
            if subscription.restored_this_month > 0:
                message += f"• Восстановлено в этом месяце: {subscription.restored_this_month}\n"
        
        # Прогресс-бар использования
        message += f"\n<b>📊 ИСПОЛЬЗОВАНИЕ</b>\n"
        message += f"{progress_bar} {usage_percent}%\n"
        
        # Создаем инлайн клавиатуру
        keyboard = []
        
        # Кнопка истории
        keyboard.append([
            InlineKeyboardButton("📜 История абонемента", callback_data=f"subscription_history_{athlete.id}")
        ])
        
        # Кнопка назад
        if is_coach_viewing_athlete:
            keyboard.append([
                InlineKeyboardButton("🔙 Назад к истории", callback_data=f"subscription_history_{athlete.id}")
            ])
        else:
            # Для спортсмена - возврат к истории или к абонементу
            keyboard.append([
                InlineKeyboardButton("🔙 Назад к истории", callback_data=f"subscription_history_athlete_{athlete.id}")
            ])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            message,
            reply_markup=reply_markup,
            parse_mode='HTML'
        )
    
    except Exception as e:
        logger.error(f"❌ ОШИБКА ПРИ ПОКАЗЕ АБОНЕМЕНТА ИЗ ИСТОРИИ: {e}", exc_info=True)
        await query.edit_message_text("❌ Ошибка при загрузке абонемента")
    finally:
        session.close()


async def handle_activate_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Активировать абонемент - показать выбор типа или активировать существующий"""
    query = update.callback_query
    await query.answer()
    
    callback_data = query.data.replace("activate_sub_", "")
    
    session = Session()
    try:
        user = get_user_by_telegram_id(session, query.from_user.id)
        if not user or user.role not in ['coach', 'admin']:
            await query.edit_message_text("❌ У вас нет доступа")
            return
        
        # Если это создание нового абонемента (activate_sub_new_123)
        if callback_data.startswith("new_"):
            athlete_id = int(callback_data.replace("new_", ""))
            athlete = session.query(Athlete).filter_by(id=athlete_id).first()
            if not athlete:
                await query.edit_message_text("❌ Спортсмен не найден")
                return
            
            if user.role == 'coach' and athlete.created_by != user.id:
                await query.edit_message_text("❌ Вы не можете создавать абонемент для этого спортсмена")
                return
            
            # Показываем выбор типа абонемента
            keyboard = [
                [InlineKeyboardButton("Месячный", callback_data=f"activate_sub_type_{athlete_id}_monthly")],
                [InlineKeyboardButton("Разовый", callback_data=f"activate_sub_type_{athlete_id}_single")],
                [InlineKeyboardButton("🔙 Назад", callback_data=f"subscription_athlete_{athlete_id}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                f"👤 <b>{html.escape(athlete.full_name)}</b>\n\n"
                f"🎫 <b>АКТИВАЦИЯ АБОНЕМЕНТА</b>\n\n"
                f"Выберите тип абонемента:",
                reply_markup=reply_markup,
                parse_mode='HTML'
            )
            return
        
        # Если это выбор типа для нового абонемента (activate_sub_type_123_monthly)
        if callback_data.startswith("type_"):
            # Проверяем, это новый абонемент или существующий
            if callback_data.startswith("type_existing_"):
                # Активация существующего абонемента с выбором типа
                parts = callback_data.replace("type_existing_", "").split("_")
                subscription_id = int(parts[0])
                subscription_type = parts[1]  # monthly или single
                
                subscription = session.query(Subscription).filter_by(id=subscription_id).first()
                if not subscription:
                    await query.edit_message_text("❌ Абонемент не найден")
                    return
                
                athlete = subscription.athlete
                if user.role == 'coach' and athlete.created_by != user.id:
                    await query.edit_message_text("❌ Вы не можете изменять этот абонемент")
                    return
                
                # Устанавливаем тип абонемента и рассчитываем количество тренировок
                subscription.subscription_type = subscription_type
                if subscription_type == "monthly":
                    subscription.trainings_total = 12
                    subscription.trainings_remaining = 12
                elif subscription_type == "single":
                    subscription.trainings_total = 1
                    subscription.trainings_remaining = 1
                
                # Активируем абонемент и устанавливаем даты
                from database.db_utils import _calculate_end_date
                from datetime import timedelta
                
                start_date = datetime.utcnow()
                if subscription_type == "monthly":
                    end_date = _calculate_end_date(start_date, months=1)
                elif subscription_type == "single":
                    end_date = start_date + timedelta(days=1)
                else:
                    end_date = start_date + timedelta(days=30)
                
                subscription.is_active = True
                subscription.start_date = start_date
                subscription.end_date = end_date
                
                # Для месячных абонементов создаем тренировки по расписанию
                if subscription_type == "monthly" and athlete.sport_type and athlete.age_group:
                    from database.db_utils import _create_and_deduct_scheduled_trainings
                    _create_and_deduct_scheduled_trainings(session, subscription, athlete, start_date, end_date)
                
                session.commit()
                
                subscription_type_ru = "Месячный" if subscription_type == "monthly" else "Разовый"
                await query.answer(f"✅ Абонемент ({subscription_type_ru}) активирован", show_alert=True)
                
                # Показываем карточку абонемента
                await show_subscription_card(update, context)
                return
            else:
                # Создание нового абонемента с выбором типа
                parts = callback_data.replace("type_", "").split("_")
                athlete_id = int(parts[0])
                subscription_type = parts[1]  # monthly или single
                
                athlete = session.query(Athlete).filter_by(id=athlete_id).first()
                if not athlete:
                    await query.edit_message_text("❌ Спортсмен не найден")
                    return
                
                if user.role == 'coach' and athlete.created_by != user.id:
                    await query.edit_message_text("❌ Вы не можете создавать абонемент для этого спортсмена")
                    return
                
                # Создаем абонемент и сразу активируем его
                from services.subscription_service import SubscriptionService
                from database.db_utils import _calculate_end_date
                from datetime import timedelta
                
                subscription = SubscriptionService.create_subscription(
                    session=session,
                    athlete_id=athlete_id,
                    subscription_type=subscription_type,
                    sport_type=athlete.sport_type
                )
                
                # Активируем абонемент и устанавливаем даты
                start_date = datetime.utcnow()
                
                if subscription_type == "monthly":
                    end_date = _calculate_end_date(start_date, months=1)
                elif subscription_type == "single":
                    end_date = start_date + timedelta(days=1)
                else:
                    end_date = start_date + timedelta(days=30)
                
                subscription.is_active = True
                subscription.start_date = start_date
                subscription.end_date = end_date
                
                # Для месячных абонементов создаем тренировки по расписанию
                if subscription_type == "monthly" and athlete.sport_type and athlete.age_group:
                    from database.db_utils import _create_and_deduct_scheduled_trainings
                    _create_and_deduct_scheduled_trainings(session, subscription, athlete, start_date, end_date)
                
                session.commit()
                
                subscription_type_ru = "Месячный" if subscription_type == "monthly" else "Разовый"
                await query.answer(f"✅ Абонемент ({subscription_type_ru}) создан и активирован", show_alert=True)
                
                # Показываем карточку абонемента
                await show_subscription_card(update, context)
                return
        
        # Если это активация существующего абонемента
        subscription_id = int(callback_data)
        subscription = session.query(Subscription).filter_by(id=subscription_id).first()
        if not subscription:
            await query.edit_message_text("❌ Абонемент не найден")
            return
        
        athlete = subscription.athlete
        if user.role == 'coach' and athlete.created_by != user.id:
            await query.edit_message_text("❌ Вы не можете изменять этот абонемент")
            return
        
        # Если у абонемента нет типа, показываем выбор типа
        if subscription.subscription_type is None:
            keyboard = [
                [InlineKeyboardButton("Месячный", callback_data=f"activate_sub_type_existing_{subscription.id}_monthly")],
                [InlineKeyboardButton("Разовый", callback_data=f"activate_sub_type_existing_{subscription.id}_single")],
                [InlineKeyboardButton("🔙 Назад", callback_data=f"subscription_{subscription.id}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                f"👤 <b>{html.escape(athlete.full_name)}</b>\n\n"
                f"🎫 <b>АКТИВАЦИЯ АБОНЕМЕНТА</b>\n\n"
                f"Выберите тип абонемента:",
                reply_markup=reply_markup,
                parse_mode='HTML'
            )
            return
        
        # Если тип уже определен, активируем абонемент
        from database.db_utils import _calculate_end_date
        from datetime import timedelta
        
        start_date = datetime.utcnow()
        
        if subscription.subscription_type == "monthly":
            end_date = _calculate_end_date(start_date, months=1)
        elif subscription.subscription_type == "single":
            end_date = start_date + timedelta(days=1)
        else:
            end_date = start_date + timedelta(days=30)  # По умолчанию 30 дней
        
        subscription.is_active = True
        subscription.start_date = start_date
        subscription.end_date = end_date
        
        # Для месячных абонементов создаем тренировки по расписанию
        if subscription.subscription_type == "monthly" and athlete.sport_type and athlete.age_group:
            from database.db_utils import _create_and_deduct_scheduled_trainings
            _create_and_deduct_scheduled_trainings(session, subscription, athlete, start_date, end_date)
        
        session.commit()
        
        await query.answer("✅ Абонемент активирован", show_alert=True)
        
        # Обновляем карточку абонемента
        await show_subscription_card(update, context)
        
    except Exception as e:
        logger.error(f"❌ ОШИБКА ПРИ АКТИВАЦИИ АБОНЕМЕНТА: {e}", exc_info=True)
        await query.edit_message_text("❌ Ошибка при активации абонемента")
    finally:
        session.close()


def deactivate_subscription(session, subscription_id: int) -> bool:
    """
    Деактивировать абонемент (внутренняя функция для автоматического использования).
    
    Args:
        session: Сессия базы данных
        subscription_id: ID абонемента
        
    Returns:
        True если успешно, False если ошибка
    """
    try:
        subscription = session.query(Subscription).filter_by(id=subscription_id).first()
        if not subscription:
            logger.error(f"❌ Абонемент с id={subscription_id} не найден")
            return False
        
        subscription.is_active = False
        session.commit()
        logger.info(f"✅ Абонемент #{subscription_id} автоматически деактивирован")
        return True
    except Exception as e:
        logger.error(f"❌ ОШИБКА ПРИ ДЕАКТИВАЦИИ АБОНЕМЕНТА #{subscription_id}: {e}", exc_info=True)
        session.rollback()
        return False