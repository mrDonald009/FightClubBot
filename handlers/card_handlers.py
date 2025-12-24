import logging
from datetime import datetime, timedelta
import calendar as py_calendar
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes, CallbackQueryHandler, CommandHandler
from database.models import Session, Athlete, Subscription, Training, Attendance, Coach, Admin
from database.db_utils import get_user_by_telegram_id, get_user_role, get_athlete_card_info
from typing import Union
import html
from utils.training_manager import TrainingManager

logger = logging.getLogger(__name__)


def calculate_actual_trainings_remaining(session, subscription):
    """
    Пересчитать фактическое количество оставшихся тренировок на основе завершенных тренировок.
    
    Учитывает только:
    - Завершенные тренировки (начало + 1.5 часа < текущее время)
    - Не восстановленные (was_restored = False или NULL)
    
    Args:
        session: Сессия базы данных
        subscription: Объект Subscription
        
    Returns:
        Фактическое количество оставшихся тренировок
    """
    if subscription.trainings_total is None:
        return None
    
    from datetime import datetime, timedelta
    from sqlalchemy import or_
    from database.models import Attendance, Training
    
    current_time = datetime.utcnow()
    
    # Считаем количество завершенных и невосстановленных тренировок
    used_count = session.query(Attendance).join(
        Training, Attendance.training_id == Training.id
    ).filter(
        Attendance.subscription_id == subscription.id,
        # Тренировка завершилась (начало + 1.5 часа <= текущее время)
        Training.training_date + timedelta(hours=1.5) <= current_time,
        # Не восстановлена
        or_(Attendance.was_restored == False, Attendance.was_restored == None)
    ).count()
    
    # Рассчитываем фактическое количество оставшихся
    actual_remaining = max(subscription.trainings_total - used_count, 0)
    
    return actual_remaining


def get_coach_sport_type(user: Union[Coach, Admin]) -> str:
    """Получить вид спорта тренера из связи или строки (для обратной совместимости)"""
    if isinstance(user, Coach):
        if user.sport_type_rel:
            return user.sport_type_rel.name
        elif user.sport_type:
            return user.sport_type
    return None


def _format_subscription_type_ru(subscription_type: str) -> str:
    """Отобразить тип абонемента по-русски (включая неопределенный)."""
    if subscription_type == "monthly":
        return "Месячный"
    if subscription_type == "single":
        return "Разовый"
    return "Тип не определен"


def _get_schedule(sport_type: str, age_group: str):
    schedule = TrainingManager.TRAINING_SCHEDULE.get(sport_type, {}).get(age_group)
    return schedule


def _build_activation_calendar(
    subscription_id: int,
    sport_type: str,
    age_group: str,
    year: int,
    month: int
) -> InlineKeyboardMarkup:
    """
    Календарь выбора даты первой тренировки для активации абонемента.
    Визуально совпадает с "📅 Мой календарь" тренера:
    - строка дней недели
    - ровно 5 строк по 7 "квадратных" кнопок
    - навигация по месяцам + "Сегодня"
    Доступны для выбора только тренировочные дни по расписанию (сегодня и будущие даты).
    """
    schedule = _get_schedule(sport_type, age_group)
    training_days = set(schedule["days"]) if schedule else set()

    today = datetime.utcnow().date()

    # Создаем календарь (monthcalendar возвращает недели с понедельника как первый день)
    cal = py_calendar.monthcalendar(year, month)

    keyboard = []

    # Строка дней недели над календарем (как в "Мой календарь")
    day_names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    keyboard.append([InlineKeyboardButton(f"{d}.", callback_data="act_ignore") for d in day_names])

    # Ровно 5 недель (как в "Мой календарь")
    weeks_to_show = cal[:5]
    while len(weeks_to_show) < 5:
        weeks_to_show.append([0, 0, 0, 0, 0, 0, 0])

    for week in weeks_to_show:
        row = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(" ", callback_data="act_ignore"))
                continue

            date_obj = datetime(year, month, day).date()
            weekday = date_obj.weekday()

            has_scheduled_training = weekday in training_days
            # Разрешаем выбирать только сегодня и будущие даты
            is_future_or_today = date_obj >= today
            enabled = has_scheduled_training and is_future_or_today

            # Тот же стиль подсветки, что и в "Мой календарь"
            if date_obj == today:
                btn_text = f"[{day:2d}]"
            elif has_scheduled_training:
                btn_text = f"({day:2d})"
            else:
                btn_text = f"{day:2d}"

            cb = f"act_date_{subscription_id}_{year}_{month}_{day}" if enabled else "act_ignore"
            row.append(InlineKeyboardButton(btn_text, callback_data=cb))

        keyboard.append(row)

    # Навигация
    prev_year, prev_month = year, month - 1
    next_year, next_month = year, month + 1
    if prev_month == 0:
        prev_month = 12
        prev_year -= 1
    if next_month == 13:
        next_month = 1
        next_year += 1

    keyboard.append([
        InlineKeyboardButton("◀️ Предыдущий", callback_data=f"act_cal_{subscription_id}_{prev_year}_{prev_month}"),
        InlineKeyboardButton("Следующий ▶️", callback_data=f"act_cal_{subscription_id}_{next_year}_{next_month}"),
    ])

    # Кнопка "Сегодня"
    now = datetime.utcnow().date()
    if month != now.month or year != now.year:
        keyboard.append([
            InlineKeyboardButton("📅 Сегодня", callback_data=f"act_cal_{subscription_id}_{now.year}_{now.month}")
        ])

    # Навигация/выход
    keyboard.append([
        InlineKeyboardButton("🔙 Назад", callback_data=f"subscription_{subscription_id}"),
        InlineKeyboardButton("🏠 В меню", callback_data="back_to_menu_main"),
    ])

    return InlineKeyboardMarkup(keyboard)


async def handle_activation_calendar_nav(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Навигация по календарю выбора даты активации."""
    query = update.callback_query
    await query.answer()

    # act_cal_{subscription_id}_{YYYY}_{MM}
    parts = query.data.split("_")
    subscription_id = int(parts[2])
    year = int(parts[3])
    month = int(parts[4])

    session = Session()
    try:
        user = get_user_by_telegram_id(session, query.from_user.id)
        if not user or get_user_role(user) not in ['coach', 'admin']:
            await query.edit_message_text("❌ У вас нет доступа")
            return

        subscription = session.query(Subscription).filter_by(id=subscription_id).first()
        if not subscription:
            await query.edit_message_text("❌ Абонемент не найден")
            return

        athlete = subscription.athlete
        if isinstance(user, Coach) and athlete.created_by != user.id:
            await query.edit_message_text("❌ Вы не можете изменять этот абонемент")
            return

        sport_type = subscription.sport_type or athlete.sport_type
        age_group = athlete.age_group

        reply_markup = _build_activation_calendar(subscription_id, sport_type, age_group, year, month)
        await query.edit_message_reply_markup(reply_markup=reply_markup)
    finally:
        session.close()


async def handle_activation_date_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выбор даты первой тренировки для активации абонемента."""
    query = update.callback_query
    await query.answer()

    # act_date_{subscription_id}_{YYYY}_{MM}_{DD}
    parts = query.data.split("_")
    subscription_id = int(parts[2])
    year = int(parts[3])
    month = int(parts[4])
    day = int(parts[5])

    session = Session()
    try:
        user = get_user_by_telegram_id(session, query.from_user.id)
        if not user or get_user_role(user) not in ['coach', 'admin']:
            await query.edit_message_text("❌ У вас нет доступа")
            return

        subscription = session.query(Subscription).filter_by(id=subscription_id).first()
        if not subscription:
            await query.edit_message_text("❌ Абонемент не найден")
            return

        athlete = subscription.athlete
        if isinstance(user, Coach) and athlete.created_by != user.id:
            await query.edit_message_text("❌ Вы не можете изменять этот абонемент")
            return

        sport_type = subscription.sport_type or athlete.sport_type
        age_group = athlete.age_group

        schedule = _get_schedule(sport_type, age_group)
        if not schedule:
            await query.edit_message_text("❌ Расписание для этой группы не найдено. Обратитесь к администратору.")
            return

        # Дата, выбранная тренером (без времени)
        coach_selected_date = datetime(year, month, day, 0, 0, 0)
        
        # Находим ближайшую дату тренировки согласно расписанию
        from database.db_utils import _find_nearest_training_date
        start_date = _find_nearest_training_date(coach_selected_date, sport_type, age_group)

        # Рассчитываем end_date
        from database.db_utils import _calculate_12th_training_date, _calculate_end_date
        from datetime import timedelta as _td

        if subscription.subscription_type == "monthly":
            # Дата окончания = дата 12-й тренировки + 1,5 часа (окончание последней тренировки)
            end_date = _calculate_12th_training_date(start_date, sport_type, age_group)
        elif subscription.subscription_type == "single":
            # Дата окончания = дата начала + 1,5 часа (окончание тренировки)
            end_date = start_date + _td(hours=1.5)
        else:
            # Тип еще не выбран — просим вернуться назад
            await query.edit_message_text("❌ Сначала выберите тип абонемента.")
            return

        # Деактивируем другие активные абонементы
        for old_sub in [s for s in athlete.subscriptions if s.is_active and s.id != subscription.id]:
            old_sub.is_active = False

        subscription.is_active = True
        subscription.start_date = start_date
        subscription.end_date = end_date

        # Для месячных создаем тренировки по расписанию
        if subscription.subscription_type == "monthly" and subscription.sport_type and athlete.age_group:
            from database.db_utils import _create_and_deduct_scheduled_trainings
            _create_and_deduct_scheduled_trainings(session, subscription, athlete, start_date, end_date)
        elif subscription.subscription_type == "single":
            # Для разового: создаем/находим тренировку на выбранную дату
            coach_id = athlete.created_by if athlete.created_by else None
            training = session.query(Training).filter_by(
                sport_type=sport_type,
                age_group=age_group,
                training_date=start_date,
                is_cancelled=False
            ).first()
            if not training:
                training = Training(
                    sport_type=sport_type,
                    age_group=age_group,
                    training_date=start_date,
                    is_cancelled=False,
                    coach_id=coach_id
                )
                session.add(training)
                session.flush()
            elif coach_id and not getattr(training, "coach_id", None):
                training.coach_id = coach_id
                session.flush()
            # Важно: не списываем тренировку при активации.
            # Списание должно происходить по факту (авто-списание/отметка посещения),
            # а отображение в календаре делаем по активным абонементам и диапазону дат.

        session.commit()

        # Показать карточку абонемента
        await show_subscription_card(update, context, override_query_data=f"subscription_{subscription.id}")
    except Exception as e:
        logger.error(f"❌ ОШИБКА ВЫБОРА ДАТЫ АКТИВАЦИИ: {e}", exc_info=True)
        await query.edit_message_text("❌ Ошибка при активации абонемента")
    finally:
        session.close()


async def handle_activation_ignore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Игнор-кнопка для календаря (пустые клетки/дни недели)."""
    query = update.callback_query
    await query.answer()


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

        if not user or get_user_role(user) not in ['coach', 'admin']:
            if query:
                await query.edit_message_text("❌ У вас нет доступа")
            else:
                await update.message.reply_text("❌ У вас нет доступа")
            return

        # Получаем информацию для карточки
        # Если тренер смотрит карточку, выбираем абонемент по его виду спорта
        athlete = session.query(Athlete).filter_by(id=athlete_id).first()
        if not athlete:
            if query:
                await query.edit_message_text("❌ Спортсмен не найден")
            else:
                await update.message.reply_text("❌ Спортсмен не найден")
            return
        
        # Связь 1:1 - у спортсмена только один активный абонемент
        subscription = athlete.current_subscription
        
        # Получаем полную информацию для карточки
        card_info = get_athlete_card_info(session, athlete_id)
        if not card_info:
            if query:
                await query.edit_message_text("❌ Ошибка при загрузке карточки")
            else:
                await update.message.reply_text("❌ Ошибка при загрузке карточки")
            return
        
        stats = card_info['stats']

        # Проверяем права (тренер может видеть только своих спортсменов)
        if isinstance(user, Coach) and athlete.created_by != user.id:
            if query:
                await query.edit_message_text("❌ Вы не можете просматривать этого спортсмена")
            else:
                await update.message.reply_text("❌ Вы не можете просматривать этого спортсмена")
            return

        # Формируем сообщение (только базовая информация)
        message = f"👤 <b>КАРТОЧКА СПОРТСМЕНА</b>\n\n"
        message += f"<b>{html.escape(athlete.full_name)}</b>\n"
        message += f"📞 {athlete.phone or 'Не указан'}\n"
        
        # Дата рождения
        if athlete.birth_date:
            birth_date_str = athlete.birth_date.strftime('%d.%m.%Y')
            message += f"🎂 Дата рождения: {birth_date_str}\n"
        else:
            message += f"🎂 Дата рождения: Не указана\n"
        
        # Дата регистрации в зале
        if athlete.created_at:
            registration_date_str = athlete.created_at.strftime('%d.%m.%Y')
            message += f"📅 Дата регистрации: {registration_date_str}\n"
        
        message += f"\n"
        
        # Медицинская информация
        message += f"<b>🏥 МЕДИЦИНСКАЯ ИНФОРМАЦИЯ</b>\n"
        medical_info = athlete.medical_info or 'Не указана'
        message += f"{html.escape(medical_info)}"

        # Создаем инлайн клавиатуру
        keyboard = []

        # Первый ряд: основные действия
        keyboard.append([
            InlineKeyboardButton("🎫 Абонемент", callback_data=f"subscription_athlete_{athlete_id}"),
            InlineKeyboardButton("📅 Посещения", callback_data=f"visits_{athlete_id}")
        ])

        # Второй ряд: навигация
        keyboard.append([
            InlineKeyboardButton("📋 К списку", callback_data="back_to_list"),
        ])

        # Третий ряд: редактирование
        keyboard.append([
            InlineKeyboardButton("✏️ Редактировать", callback_data=f"edit_{athlete_id}"),
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


async def show_subscription_card(update: Update, context: ContextTypes.DEFAULT_TYPE, override_query_data: str = None):
    """Показать детальную информацию об абонементе или список абонементов"""
    query = update.callback_query
    await query.answer()

    # Парсим callback_data: subscription_athlete_123 или subscription_123
    query_data = override_query_data or query.data
    callback_data = query_data.replace("subscription_", "")
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

        if not user or get_user_role(user) not in ['coach', 'admin']:
            await query.edit_message_text("❌ У вас нет доступа")
            return

        # Если передан subscription_id, получаем абонемент напрямую
        if subscription_id:
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
            
            # Важно: у нового спортсмена абонемент часто НЕактивный, а current_subscription возвращает только активный.
            # Поэтому при открытии "🎫 Абонемент" мы должны показать последний абонемент даже если он неактивен,
            # иначе пользователь попадает на экран "АБОНЕМЕНТЫ" (список), что не нужно для нового спортсмена.
            if not subscription:
                all_subs = session.query(Subscription).filter_by(athlete_id=athlete_id).all()
                if all_subs:
                    def _sub_sort_key(s: Subscription):
                        return (s.created_at or datetime.min, s.id)

                    all_subs_sorted = sorted(all_subs, key=_sub_sort_key, reverse=True)

                    if isinstance(user, Coach):
                        coach_sport_type = get_coach_sport_type(user)
                        if coach_sport_type:
                            matching = [s for s in all_subs_sorted if s.sport_type == coach_sport_type]
                            if matching:
                                active_matching = [s for s in matching if s.is_active]
                                subscription = sorted(active_matching or matching, key=_sub_sort_key, reverse=True)[0]
                            else:
                                subscription = all_subs_sorted[0]
                        else:
                            subscription = all_subs_sorted[0]
                    else:
                        subscription = all_subs_sorted[0]

        # Проверяем права
        if isinstance(user, Coach) and athlete.created_by != user.id:
            await query.edit_message_text("❌ Вы не можете просматривать этого спортсмена")
            return

        # Если нет конкретного абонемента, показываем список всех абонементов
        if not subscription:
            from services.subscription_service import SubscriptionService
            all_subscriptions = SubscriptionService.get_athlete_subscriptions(session, athlete_id)
            
            # Проверяем, есть ли абонемент по виду спорта текущего тренера
            coach_sport_sub = None
            coach_sport_type = get_coach_sport_type(user) if isinstance(user, Coach) else None
            if isinstance(user, Coach) and coach_sport_type:
                coach_sport_sub = next((s for s in all_subscriptions if s.sport_type == coach_sport_type), None)
            
            if not all_subscriptions:
                # Если нет абонементов, показываем кнопку создания абонемента
                keyboard = [
                    [InlineKeyboardButton("✅ Создать абонемент", callback_data=f"activate_sub_new_{athlete_id}")],
                    [InlineKeyboardButton("🔙 Назад к карточке", callback_data=f"athlete_{athlete_id}")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)

                await query.edit_message_text(
                    f"👤 <b>{html.escape(athlete.full_name)}</b>\n\n"
                    f"❌ У спортсмена нет абонемента.\n\n"
                    f"Нажмите 'Создать абонемент' для создания и активации абонемента.",
                    reply_markup=reply_markup,
                    parse_mode='HTML'
                )
                return
            
            # Если есть абонементы, но нет абонемента по виду спорта тренера - предлагаем создать
            if isinstance(user, Coach) and coach_sport_type and not coach_sport_sub:
                # Добавляем кнопку создания абонемента по виду спорта тренера
                message = f"👤 <b>{html.escape(athlete.full_name)}</b>\n\n"
                message += f"🎫 <b>АБОНЕМЕНТЫ</b>\n\n"
                message += f"⚠️ У спортсмена нет абонемента по виду спорта <b>{coach_sport_type}</b>\n\n"
                message += f"Выберите существующий абонемент или создайте новый:\n\n"
                
                keyboard = []
                from utils.subscription_checker import SubscriptionChecker
                for sub in sorted(all_subscriptions, key=lambda s: s.created_at or datetime.min, reverse=True):
                    status = SubscriptionChecker.get_subscription_status(sub)
                    status_icon = "🟢" if status == "active" else "🔴" if status == "expired" else "⚪"
                    sport_type_display = sub.sport_type or "—"
                    sub_type = _format_subscription_type_ru(sub.subscription_type)
                    start_date_str = sub.start_date.strftime('%d.%m.%Y') if sub.start_date else "—"
                    
                    button_text = f"{status_icon} {sport_type_display} | {sub_type} | {start_date_str}"
                    if len(button_text) > 64:
                        button_text = f"{status_icon} {sport_type_display} | {sub_type}"
                    
                    keyboard.append([
                        InlineKeyboardButton(button_text, callback_data=f"subscription_{sub.id}")
                    ])
                
                # Кнопка создания нового абонемента по виду спорта тренера
                keyboard.append([
                    InlineKeyboardButton(f"✅ Создать абонемент ({coach_sport_type})", callback_data=f"activate_sub_new_{athlete_id}")
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
            
            # Проверяем, есть ли абонемент по виду спорта текущего тренера
            coach_sport_sub = None
            coach_sport_type = get_coach_sport_type(user) if isinstance(user, Coach) else None
            if isinstance(user, Coach) and coach_sport_type:
                coach_sport_sub = next((s for s in all_subscriptions if s.sport_type == coach_sport_type), None)
            
            # Показываем список абонементов
            message = f"👤 <b>{html.escape(athlete.full_name)}</b>\n\n"
            message += f"🎫 <b>АБОНЕМЕНТЫ</b>\n\n"
            
            keyboard = []
            from utils.subscription_checker import SubscriptionChecker
            for sub in sorted(all_subscriptions, key=lambda s: s.created_at or datetime.min, reverse=True):
                status = SubscriptionChecker.get_subscription_status(sub)
                status_icon = "🟢" if status == "active" else "🔴" if status == "expired" else "⚪"
                sport_type_display = sub.sport_type or "—"
                sub_type = _format_subscription_type_ru(sub.subscription_type)
                start_date_str = sub.start_date.strftime('%d.%m.%Y') if sub.start_date else "—"
                
                button_text = f"{status_icon} {sport_type_display} | {sub_type} | {start_date_str}"
                if len(button_text) > 64:
                    button_text = f"{status_icon} {sport_type_display} | {sub_type}"
                
                keyboard.append([
                    InlineKeyboardButton(button_text, callback_data=f"subscription_{sub.id}")
                ])
            
            # Если тренер смотрит и у спортсмена нет абонемента по его виду спорта - предлагаем создать
            if isinstance(user, Coach) and coach_sport_type and not coach_sport_sub:
                keyboard.append([
                    InlineKeyboardButton(f"✅ Создать абонемент ({coach_sport_type})", callback_data=f"activate_sub_new_{athlete_id}")
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

        # Получаем возрастную группу спортсмена
        age_group_display = "Детская" if athlete.age_group == "children" else "Взрослая" if athlete.age_group else "Не указана"

        # Формируем сообщение (только необходимая информация)
        message = f"🎫 <b>АБОНЕМЕНТ</b>\n\n"
        message += f"👤 <b>{html.escape(athlete.full_name)}</b>\n\n"

        message += f"<b>📋 ИНФОРМАЦИЯ</b>\n"
        message += f"• Вид спорта: {subscription.sport_type or '—'}\n"
        message += f"• Группа: {age_group_display}\n"
        
        sub_type_display = _format_subscription_type_ru(subscription.subscription_type)
        message += f"• Тип абонемента: {sub_type_display}\n"

        # Статус
        from utils.subscription_checker import SubscriptionChecker
        status_display = SubscriptionChecker.format_subscription_status(subscription)
        message += f"• Статус: {status_display}\n"
        
        # Статус заморозки
        if subscription.is_frozen and subscription.frozen_until:
            frozen_until_str = subscription.frozen_until.strftime('%d.%m.%Y %H:%M')
            message += f"• ❄️ Заморожен до: {frozen_until_str}\n"
            if subscription.frozen_from:
                frozen_from_str = subscription.frozen_from.strftime('%d.%m.%Y %H:%M')
                message += f"• ❄️ Заморожен с: {frozen_from_str}\n"
            if subscription.frozen_count:
                message += f"• ❄️ Заморожен раз: {subscription.frozen_count}\n"

        # Даты (до активации не показываем "дату начала", даже если она случайно заполнена в БД)
        start_date_str = subscription.start_date.strftime('%d.%m.%Y') if (subscription.is_active and subscription.start_date) else "—"
        message += f"• Дата начала: {start_date_str}\n"
        
        if subscription.is_active and subscription.end_date:
            end_date_str = subscription.end_date.strftime('%d.%m.%Y %H:%M')
            days_left = (subscription.end_date - datetime.utcnow()).days
            message += f"• Дата окончания: {end_date_str}\n"
            
            # Осталось тренировок (пересчитываем на лету для актуальности)
            if subscription.trainings_total is None:
                message += f"• Осталось тренировок: —\n"
            else:
                actual_remaining = calculate_actual_trainings_remaining(session, subscription)
                if actual_remaining is not None:
                    message += f"• Осталось тренировок: {actual_remaining}/{subscription.trainings_total}\n"
                    # Обновляем значение в БД для синхронизации
                    if subscription.trainings_remaining != actual_remaining:
                        subscription.trainings_remaining = actual_remaining
                        session.commit()
                else:
                    message += f"• Осталось тренировок: {subscription.trainings_remaining}/{subscription.trainings_total}\n"
        else:
            message += f"• Дата окончания: —\n"
            message += f"• Осталось тренировок: —\n"

        # Создаем инлайн клавиатуру
        keyboard = []

        # Кнопка активации (только если абонемент неактивен)
        if not subscription.is_active:
            keyboard.append([
                InlineKeyboardButton("✅ Активировать", callback_data=f"activate_sub_{subscription.id}")
            ])
        
        # Кнопки заморозки/разморозки (только для активных абонементов)
        if subscription.is_active:
            if subscription.is_frozen:
                keyboard.append([
                    InlineKeyboardButton("❄️ Разморозить", callback_data=f"unfreeze_sub_{subscription.id}")
                ])
            else:
                keyboard.append([
                    InlineKeyboardButton("❄️ Заморозить", callback_data=f"freeze_sub_{subscription.id}")
                ])

        # История абонемента: показываем только если есть хотя бы 2 абонемента
        has_history = session.query(Subscription).filter_by(athlete_id=athlete_id).count() > 1
        if has_history:
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
        if get_user_role(user) != 'athlete':
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
        sub_type_display = _format_subscription_type_ru(subscription.subscription_type)
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
            
            # Осталось тренировок (пересчитываем на лету для актуальности)
            if subscription.trainings_total is None:
                message_text += f"• Осталось тренировок: —\n"
            else:
                actual_remaining = calculate_actual_trainings_remaining(session, subscription)
                if actual_remaining is not None:
                    message_text += f"• Осталось тренировок: {actual_remaining}/{subscription.trainings_total}\n"
                    # Обновляем значение в БД для синхронизации
                    if subscription.trainings_remaining != actual_remaining:
                        subscription.trainings_remaining = actual_remaining
                        session.commit()
                else:
                    message_text += f"• Осталось тренировок: {subscription.trainings_remaining}/{subscription.trainings_total}\n"
        else:
            message_text += f"• Дата окончания: —\n"
            message_text += f"• Осталось тренировок: —\n"
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


async def show_my_athlete_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать карточку спортсмена (для самого спортсмена)"""
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
        if get_user_role(user) != 'athlete':
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
        
        # Получаем информацию для карточки
        card_info = get_athlete_card_info(session, athlete.id)
        
        if not card_info:
            error_msg = "❌ Ошибка при загрузке карточки"
            if query:
                await query.edit_message_text(error_msg)
            elif message:
                await message.reply_text(error_msg)
            return
        
        athlete = card_info['athlete']
        subscription = card_info['subscription']
        stats = card_info['stats']
        
        # Формируем сообщение
        message_text = f"👤 <b>МОЯ КАРТОЧКА</b>\n\n"
        message_text += f"<b>{html.escape(athlete.full_name)}</b>\n"
        message_text += f"📞 {athlete.phone or 'Не указан'}\n"
        message_text += f"🥊 {athlete.sport_type or 'Не указан'} | {card_info['age_group_display']}\n"
        message_text += f"👨‍🏫 Тренер: {athlete.coach.first_name if athlete.coach else 'Не указан'}\n"
        message_text += f"📅 В клубе с: {athlete.created_at.strftime('%d.%m.%Y')}\n\n"
        
        message_text += f"<b>📊 СТАТИСТИКА (30 дней)</b>\n"
        message_text += f"• Посещено: {stats['attended_trainings']}/{stats['total_trainings']}\n"
        message_text += f"• Пропущено: {stats['missed_trainings']}\n"
        message_text += f"• Посещаемость: {stats['attendance_rate']}%\n\n"
        
        message_text += f"<b>🏥 МЕДИЦИНСКАЯ ИНФОРМАЦИЯ</b>\n"
        message_text += f"{card_info['medical_display'] or '—'}\n\n"
        
        message_text += f"<b>🎫 АБОНЕМЕНТ</b>\n"
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
            
            message_text += f"• Статус: {status_display}\n"
            message_text += f"• Тип: {sub_type}\n"
            message_text += f"• Тренировки: {trainings}\n"
            message_text += f"• Действует до: {end_date}\n"
            
            # Добавляем информацию о создании
            if subscription.created_at:
                message_text += f"• Активирован: {subscription.created_at.strftime('%d.%m.%Y')}\n"
        else:
            message_text += f"• ❌ Нет активного абонемента\n"
        
        message_text += f"\n🆔 ID: {athlete.id}"
        
        # Создаем инлайн клавиатуру
        keyboard = []
        
        # Первый ряд: основные действия
        keyboard.append([
            InlineKeyboardButton("🎫 Мой абонемент", callback_data="athlete_subscription_refresh"),
            InlineKeyboardButton("📊 Статистика", callback_data=f"stats_athlete_{athlete.id}")
        ])
        
        # Второй ряд: навигация
        keyboard.append([
            InlineKeyboardButton("🏠 В меню", callback_data="athlete_back_to_menu")
        ])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if query:
            await query.edit_message_text(
                message_text,
                reply_markup=reply_markup,
                parse_mode='HTML'
            )
        else:
            await message.reply_text(
                message_text,
                reply_markup=reply_markup,
                parse_mode='HTML'
            )
    
    except Exception as e:
        logger.error(f"❌ ОШИБКА ПРИ ПОКАЗЕ КАРТОЧКИ СПОРТСМЕНА: {e}", exc_info=True)
        error_msg = "❌ Ошибка при загрузке карточки"
        if query:
            await query.edit_message_text(error_msg)
        elif message:
            await message.reply_text(error_msg)
    finally:
        session.close()


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
        if isinstance(user, Athlete) and callback_data.startswith("athlete_"):
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
        is_athlete_viewing_own = (isinstance(user, Athlete) and athlete.telegram_id == user.telegram_id)
        is_coach_viewing_athlete = ((isinstance(user, Coach) or isinstance(user, Admin)) and 
                                   (isinstance(user, Admin) or athlete.created_by == user.id))
        
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
                [InlineKeyboardButton("🔙 Назад", callback_data=f"subscription_athlete_{athlete_id}" if is_coach_viewing_athlete else "athlete_back_to_menu")]
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
            
            sub_type = _format_subscription_type_ru(sub.subscription_type)
            
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
                InlineKeyboardButton("🔙 Назад к абонементу", callback_data=f"subscription_athlete_{athlete_id}")
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
        is_athlete_viewing_own = (isinstance(user, Athlete) and athlete.telegram_id == user.telegram_id)
        is_coach_viewing_athlete = ((isinstance(user, Coach) or isinstance(user, Admin)) and 
                                   (isinstance(user, Admin) or athlete.created_by == user.id))
        
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
        sub_type_display = _format_subscription_type_ru(subscription.subscription_type)
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
            
            # Осталось тренировок (пересчитываем на лету для актуальности)
            if subscription.trainings_total is None:
                message += f"• Осталось тренировок: —\n"
            else:
                actual_remaining = calculate_actual_trainings_remaining(session, subscription)
                if actual_remaining is not None:
                    message += f"• Осталось тренировок: {actual_remaining}/{subscription.trainings_total}\n"
                    # Обновляем значение в БД для синхронизации
                    if subscription.trainings_remaining != actual_remaining:
                        subscription.trainings_remaining = actual_remaining
                        session.commit()
                else:
                    message += f"• Осталось тренировок: {subscription.trainings_remaining}/{subscription.trainings_total}\n"
        else:
            message += f"• Дата окончания: —\n"
            message += f"• Осталось тренировок: —\n"
        
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
    
    logger.info(f"[activate_sub] raw_query_data={getattr(query, 'data', None)} user_id={getattr(query.from_user, 'id', None)}")
    
    callback_data = query.data.replace("activate_sub_", "")
    logger.info(f"[activate_sub] parsed_callback_data={callback_data}")
    
    session = Session()
    try:
        user = get_user_by_telegram_id(session, query.from_user.id)
        if not user or get_user_role(user) not in ['coach', 'admin']:
            await query.edit_message_text("❌ У вас нет доступа")
            return
        
        # Если это создание нового абонемента (activate_sub_new_123)
        if callback_data.startswith("new_"):
            logger.info("[activate_sub] branch=new_subscription_choose_type")
            athlete_id = int(callback_data.replace("new_", ""))
            athlete = session.query(Athlete).filter_by(id=athlete_id).first()
            if not athlete:
                await query.edit_message_text("❌ Спортсмен не найден")
                return
            
            # Убираем ограничение - любой тренер может создать абонемент
            # Но проверяем, что у тренера указан вид спорта
            sport_type_for_sub = None
            if isinstance(user, Coach):
                sport_type_for_sub = get_coach_sport_type(user)
                if not sport_type_for_sub:
                    await query.edit_message_text("❌ У вас не указан вид спорта. Обратитесь к администратору.")
                    return
            elif isinstance(user, Admin):
                # Для админа можно выбрать вид спорта из существующих абонементов или использовать из спортсмена
                sport_type_for_sub = athlete.sport_type
            
            # Показываем выбор типа абонемента
            keyboard = [
                [
                    InlineKeyboardButton("Месячный", callback_data=f"activate_sub_type_{athlete_id}_monthly"),
                    InlineKeyboardButton("Разовый", callback_data=f"activate_sub_type_{athlete_id}_single"),
                ],
                [InlineKeyboardButton("🔙 Назад", callback_data=f"subscription_athlete_{athlete_id}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            sport_type_display = sport_type_for_sub or "не указан"
            await query.edit_message_text(
                f"👤 <b>{html.escape(athlete.full_name)}</b>\n\n"
                f"🎫 <b>СОЗДАНИЕ АБОНЕМЕНТА</b>\n\n"
                f"Вид спорта: <b>{sport_type_display}</b>\n\n"
                f"Выберите тип абонемента:",
                reply_markup=reply_markup,
                parse_mode='HTML'
            )
            # Сохраняем вид спорта в контексте для использования при создании
            context.user_data['new_subscription_sport_type'] = sport_type_for_sub
            return
        
        # Если это выбор типа для нового абонемента (activate_sub_type_123_monthly)
        if callback_data.startswith("type_"):
            # Проверяем, это новый абонемент или существующий
            if callback_data.startswith("type_existing_"):
                logger.info("[activate_sub] branch=activate_existing_with_type_choice")
                # Активация существующего абонемента с выбором типа
                parts = callback_data.replace("type_existing_", "").split("_")
                subscription_id = int(parts[0])
                subscription_type = parts[1]  # monthly или single
                logger.info(f"[activate_sub] existing_subscription_id={subscription_id} chosen_type={subscription_type}")
                
                subscription = session.query(Subscription).filter_by(id=subscription_id).first()
                if not subscription:
                    await query.edit_message_text("❌ Абонемент не найден")
                    return
                
                athlete = subscription.athlete
                if isinstance(user, Coach) and athlete.created_by != user.id:
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

                # До выбора даты НЕ активируем и НЕ ставим даты
                subscription.is_active = False
                subscription.start_date = None
                subscription.end_date = None

                session.commit()
                logger.info(f"[activate_sub] type_selected_existing subscription_id={subscription.id} type={subscription.subscription_type}")

                # Показываем календарь выбора даты первой тренировки (дата = дата активации)
                now = datetime.utcnow()
                sport_type = subscription.sport_type or athlete.sport_type
                reply_markup = _build_activation_calendar(subscription.id, sport_type, athlete.age_group, now.year, now.month)

                subscription_type_ru = "Месячный" if subscription_type == "monthly" else "Разовый"
                await query.edit_message_text(
                    f"👤 <b>{html.escape(athlete.full_name)}</b>\n\n"
                    f"🎫 <b>АКТИВАЦИЯ АБОНЕМЕНТА</b>\n\n"
                    f"Тип: <b>{subscription_type_ru}</b>\n\n"
                    f"Выберите дату <b>первой тренировки</b> (она будет датой активации):",
                    reply_markup=reply_markup,
                    parse_mode="HTML"
                )
                return
            else:
                logger.info("[activate_sub] branch=create_new_with_type_choice")
                # Создание нового абонемента с выбором типа
                parts = callback_data.replace("type_", "").split("_")
                athlete_id = int(parts[0])
                subscription_type = parts[1]  # monthly или single
                logger.info(f"[activate_sub] athlete_id={athlete_id} chosen_type={subscription_type}")
                
                athlete = session.query(Athlete).filter_by(id=athlete_id).first()
                if not athlete:
                    await query.edit_message_text("❌ Спортсмен не найден")
                    return
                
                # Убираем ограничение - любой тренер может создать абонемент
                # Получаем вид спорта из контекста (сохранен при выборе типа)
                sport_type_for_sub = context.user_data.get('new_subscription_sport_type')
                
                # Если не сохранен в контексте, берем из профиля тренера
                if not sport_type_for_sub:
                    if isinstance(user, Coach):
                        sport_type_for_sub = get_coach_sport_type(user)
                    if not sport_type_for_sub:
                        sport_type_for_sub = athlete.sport_type
                
                # Связь 1:1 - деактивируем все старые активные абонементы
                active_subs = [s for s in athlete.subscriptions if s.is_active]
                for old_sub in active_subs:
                    old_sub.is_active = False
                
                # Создаем абонемент (пока НЕ активируем, дату выберем в календаре)
                from services.subscription_service import SubscriptionService
                
                subscription = SubscriptionService.create_subscription(
                    session=session,
                    athlete_id=athlete_id,
                    subscription_type=subscription_type,
                    sport_type=sport_type_for_sub  # Используем вид спорта из профиля тренера
                )

                # Явно фиксируем "неактивен до выбора даты"
                subscription.is_active = False
                subscription.start_date = None
                subscription.end_date = None

                session.commit()
                logger.info(f"[activate_sub] type_selected_new subscription_id={subscription.id} type={subscription.subscription_type}")

                # Очищаем сохраненный вид спорта из контекста
                context.user_data.pop('new_subscription_sport_type', None)

                now = datetime.utcnow()
                reply_markup = _build_activation_calendar(subscription.id, subscription.sport_type or athlete.sport_type, athlete.age_group, now.year, now.month)

                subscription_type_ru = "Месячный" if subscription_type == "monthly" else "Разовый"
                await query.edit_message_text(
                    f"👤 <b>{html.escape(athlete.full_name)}</b>\n\n"
                    f"🎫 <b>АКТИВАЦИЯ АБОНЕМЕНТА</b>\n\n"
                    f"Тип: <b>{subscription_type_ru}</b>\n\n"
                    f"Выберите дату <b>первой тренировки</b> (она будет датой активации):",
                    reply_markup=reply_markup,
                    parse_mode="HTML"
                )
                return
        
        # Если это активация существующего абонемента
        subscription_id = int(callback_data)
        logger.info(f"[activate_sub] branch=activate_existing subscription_id={subscription_id}")
        subscription = session.query(Subscription).filter_by(id=subscription_id).first()
        if not subscription:
            await query.edit_message_text("❌ Абонемент не найден")
            return
        
        athlete = subscription.athlete
        if isinstance(user, Coach) and athlete.created_by != user.id:
            await query.edit_message_text("❌ Вы не можете изменять этот абонемент")
            return
        
        # Для неактивного абонемента всегда показываем выбор типа (чтобы можно было выбрать monthly/single)
        if not subscription.is_active:
            logger.info(f"[activate_sub] existing_not_active show_type_choice current_type={subscription.subscription_type}")
            current_type = subscription.subscription_type
            if current_type == "monthly":
                current_type_display = "Месячный"
            elif current_type == "single":
                current_type_display = "Разовый"
            else:
                current_type_display = "Не определен"

            keyboard = [
                [
                    InlineKeyboardButton("Месячный", callback_data=f"activate_sub_type_existing_{subscription.id}_monthly"),
                    InlineKeyboardButton("Разовый", callback_data=f"activate_sub_type_existing_{subscription.id}_single"),
                ],
                [InlineKeyboardButton("🔙 Назад", callback_data=f"subscription_{subscription.id}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await query.edit_message_text(
                f"👤 <b>{html.escape(athlete.full_name)}</b>\n\n"
                f"🎫 <b>АКТИВАЦИЯ АБОНЕМЕНТА</b>\n\n"
                f"Текущий тип: <b>{current_type_display}</b>\n\n"
                f"Выберите тип абонемента:",
                reply_markup=reply_markup,
                parse_mode='HTML'
            )
            return
        
        # Если тип уже определен, активируем абонемент
        from database.db_utils import _calculate_end_date, _find_nearest_training_date
        from datetime import timedelta
        
        # Находим ближайшую дату тренировки согласно расписанию, начиная с текущей даты
        sport_type = subscription.sport_type or athlete.sport_type
        age_group = athlete.age_group
        coach_selected_date = datetime.utcnow()
        start_date = _find_nearest_training_date(coach_selected_date, sport_type, age_group)
        
        if subscription.subscription_type == "monthly":
            # Дата окончания = дата 12-й тренировки + 1,5 часа (окончание последней тренировки)
            from database.db_utils import _calculate_12th_training_date
            end_date = _calculate_12th_training_date(start_date, sport_type, age_group)
        elif subscription.subscription_type == "single":
            # Дата окончания = дата начала + 1,5 часа (окончание тренировки)
            end_date = start_date + timedelta(hours=1.5)
        else:
            end_date = start_date + timedelta(days=30)  # По умолчанию 30 дней
        
        # Связь 1:1 - деактивируем все старые активные абонементы
        active_subs = [s for s in athlete.subscriptions if s.is_active and s.id != subscription.id]
        for old_sub in active_subs:
            old_sub.is_active = False
        
        subscription.is_active = True
        subscription.start_date = start_date
        subscription.end_date = end_date
        
        # Для месячных абонементов создаем тренировки по расписанию
        if subscription.subscription_type == "monthly" and subscription.sport_type and athlete.age_group:
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


async def show_athlete_visits(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать историю посещений спортсмена"""
    query = update.callback_query
    await query.answer()
    
    athlete_id = int(query.data.replace("visits_", ""))
    
    session = Session()
    try:
        user = get_user_by_telegram_id(session, query.from_user.id)
        
        if not user or get_user_role(user) not in ['coach', 'admin']:
            await query.edit_message_text("❌ У вас нет доступа")
            return
        
        athlete = session.query(Athlete).filter_by(id=athlete_id).first()
        if not athlete:
            await query.edit_message_text("❌ Спортсмен не найден")
            return
        
        # Проверяем права
        if isinstance(user, Coach) and athlete.created_by != user.id:
            await query.edit_message_text("❌ Вы не можете просматривать этого спортсмена")
            return
        
        # Получаем последние 20 посещений
        attendances = session.query(Attendance).filter_by(
            athlete_id=athlete_id
        ).order_by(Attendance.created_at.desc()).limit(20).all()
        
        message = f"📅 <b>ИСТОРИЯ ПОСЕЩЕНИЙ</b>\n\n"
        message += f"👤 <b>{html.escape(athlete.full_name)}</b>\n\n"
        
        if not attendances:
            message += "❌ Нет записей о посещениях"
        else:
            message += f"Последние {len(attendances)} записей:\n\n"
            
            for idx, att in enumerate(attendances, 1):
                status = "✅" if att.attended else "❌"
                training_date = att.training.training_date.strftime('%d.%m.%Y %H:%M') if att.training else "—"
                marked_date = att.created_at.strftime('%d.%m.%Y') if att.created_at else "—"
                
                message += f"{idx}. {status} {training_date}\n"
                message += f"   Отмечено: {marked_date}\n"
                if att.was_restored:
                    message += f"   🔄 Восстановлено\n"
                message += "\n"
        
        keyboard = [
            [InlineKeyboardButton("🔙 Назад к карточке", callback_data=f"athlete_{athlete_id}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            message,
            reply_markup=reply_markup,
            parse_mode='HTML'
        )
    
    except Exception as e:
        logger.error(f"❌ ОШИБКА ПРИ ПОКАЗЕ ПОСЕЩЕНИЙ: {e}", exc_info=True)
        await query.edit_message_text("❌ Ошибка при загрузке посещений")
    finally:
        session.close()


async def show_athlete_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать детальную статистику спортсмена"""
    query = update.callback_query
    await query.answer()
    
    athlete_id = int(query.data.replace("stats_", ""))
    
    session = Session()
    try:
        user = get_user_by_telegram_id(session, query.from_user.id)
        
        if not user or get_user_role(user) not in ['coach', 'admin']:
            await query.edit_message_text("❌ У вас нет доступа")
            return
        
        athlete = session.query(Athlete).filter_by(id=athlete_id).first()
        if not athlete:
            await query.edit_message_text("❌ Спортсмен не найден")
            return
        
        # Проверяем права
        if isinstance(user, Coach) and athlete.created_by != user.id:
            await query.edit_message_text("❌ Вы не можете просматривать этого спортсмена")
            return
        
        # Получаем статистику за разные периоды
        now = datetime.utcnow()
        week_ago = now - timedelta(days=7)
        month_ago = now - timedelta(days=30)
        three_months_ago = now - timedelta(days=90)
        
        # Статистика за неделю
        week_trainings = session.query(Training).filter(
            Training.sport_type == athlete.sport_type,
            Training.age_group == athlete.age_group,
            Training.training_date >= week_ago,
            Training.is_cancelled == False
        ).count()
        
        week_attended = session.query(Attendance).filter(
            Attendance.athlete_id == athlete_id,
            Attendance.attended == True,
            Attendance.training.has(Training.training_date >= week_ago)
        ).count()
        
        # Статистика за месяц
        month_trainings = session.query(Training).filter(
            Training.sport_type == athlete.sport_type,
            Training.age_group == athlete.age_group,
            Training.training_date >= month_ago,
            Training.is_cancelled == False
        ).count()
        
        month_attended = session.query(Attendance).filter(
            Attendance.athlete_id == athlete_id,
            Attendance.attended == True,
            Attendance.training.has(Training.training_date >= month_ago)
        ).count()
        
        # Статистика за 3 месяца
        three_months_trainings = session.query(Training).filter(
            Training.sport_type == athlete.sport_type,
            Training.age_group == athlete.age_group,
            Training.training_date >= three_months_ago,
            Training.is_cancelled == False
        ).count()
        
        three_months_attended = session.query(Attendance).filter(
            Attendance.athlete_id == athlete_id,
            Attendance.attended == True,
            Attendance.training.has(Training.training_date >= three_months_ago)
        ).count()
        
        # Общая статистика
        total_attended = session.query(Attendance).filter_by(
            athlete_id=athlete_id,
            attended=True
        ).count()
        
        total_missed = session.query(Attendance).filter_by(
            athlete_id=athlete_id,
            attended=False,
            was_restored=False
        ).count()
        
        message = f"📊 <b>СТАТИСТИКА СПОРТСМЕНА</b>\n\n"
        message += f"👤 <b>{html.escape(athlete.full_name)}</b>\n\n"
        
        message += f"<b>📈 ПО ПЕРИОДАМ</b>\n"
        message += f"<b>Неделя:</b>\n"
        message += f"• Посещено: {week_attended}/{week_trainings}\n"
        week_rate = round((week_attended / week_trainings * 100), 1) if week_trainings > 0 else 0
        message += f"• Посещаемость: {week_rate}%\n\n"
        
        message += f"<b>Месяц:</b>\n"
        message += f"• Посещено: {month_attended}/{month_trainings}\n"
        month_rate = round((month_attended / month_trainings * 100), 1) if month_trainings > 0 else 0
        message += f"• Посещаемость: {month_rate}%\n\n"
        
        message += f"<b>3 месяца:</b>\n"
        message += f"• Посещено: {three_months_attended}/{three_months_trainings}\n"
        three_months_rate = round((three_months_attended / three_months_trainings * 100), 1) if three_months_trainings > 0 else 0
        message += f"• Посещаемость: {three_months_rate}%\n\n"
        
        message += f"<b>📋 ОБЩАЯ СТАТИСТИКА</b>\n"
        message += f"• Всего посещено: {total_attended}\n"
        message += f"• Всего пропущено: {total_missed}\n"
        total_rate = round((total_attended / (total_attended + total_missed) * 100), 1) if (total_attended + total_missed) > 0 else 0
        message += f"• Общая посещаемость: {total_rate}%\n"
        
        keyboard = [
            [InlineKeyboardButton("🔙 Назад к карточке", callback_data=f"athlete_{athlete_id}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            message,
            reply_markup=reply_markup,
            parse_mode='HTML'
        )
    
    except Exception as e:
        logger.error(f"❌ ОШИБКА ПРИ ПОКАЗЕ СТАТИСТИКИ: {e}", exc_info=True)
        await query.edit_message_text("❌ Ошибка при загрузке статистики")
    finally:
        session.close()


async def show_restore_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать меню восстановления тренировок"""
    query = update.callback_query
    await query.answer()
    
    athlete_id = int(query.data.replace("restore_", ""))
    
    session = Session()
    try:
        user = get_user_by_telegram_id(session, query.from_user.id)
        
        if not user or get_user_role(user) not in ['coach', 'admin']:
            await query.edit_message_text("❌ У вас нет доступа")
            return
        
        athlete = session.query(Athlete).filter_by(id=athlete_id).first()
        if not athlete:
            await query.edit_message_text("❌ Спортсмен не найден")
            return
        
        # Проверяем права
        if isinstance(user, Coach) and athlete.created_by != user.id:
            await query.edit_message_text("❌ Вы не можете восстанавливать тренировки для этого спортсмена")
            return
        
        subscription = athlete.current_subscription
        if not subscription:
            await query.edit_message_text("❌ У спортсмена нет активного абонемента")
            return
        
        # Получаем пропущенные тренировки (неиспользованные, не восстановленные)
        missed_attendances = session.query(Attendance).filter(
            Attendance.athlete_id == athlete_id,
            Attendance.subscription_id == subscription.id,
            Attendance.attended == False,
            Attendance.was_restored == False
        ).order_by(Attendance.created_at.desc()).limit(10).all()
        
        message = f"🔄 <b>ВОССТАНОВЛЕНИЕ ТРЕНИРОВОК</b>\n\n"
        message += f"👤 <b>{html.escape(athlete.full_name)}</b>\n"
        message += f"🎫 Абонемент #{subscription.id}\n"
        message += f"🏋️ Осталось тренировок: {subscription.trainings_remaining}\n\n"
        
        if not missed_attendances:
            message += "❌ Нет пропущенных тренировок для восстановления"
        else:
            message += f"<b>Пропущенные тренировки (последние {len(missed_attendances)}):</b>\n\n"
            
            keyboard = []
            for att in missed_attendances:
                training_date = att.training.training_date.strftime('%d.%m.%Y %H:%M') if att.training else "—"
                button_text = f"📅 {training_date}"
                if len(button_text) > 64:
                    button_text = f"📅 {training_date[:50]}"
                keyboard.append([
                    InlineKeyboardButton(button_text, callback_data=f"restore_att_{att.id}")
                ])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                message,
                reply_markup=reply_markup,
                parse_mode='HTML'
            )
            return
        
        keyboard = [
            [InlineKeyboardButton("🔙 Назад к карточке", callback_data=f"athlete_{athlete_id}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            message,
            reply_markup=reply_markup,
            parse_mode='HTML'
        )
    
    except Exception as e:
        logger.error(f"❌ ОШИБКА ПРИ ПОКАЗЕ МЕНЮ ВОССТАНОВЛЕНИЯ: {e}", exc_info=True)
        await query.edit_message_text("❌ Ошибка при загрузке меню восстановления")
    finally:
        session.close()


async def execute_restore_training(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Восстановить конкретную тренировку"""
    query = update.callback_query
    await query.answer()
    
    attendance_id = int(query.data.replace("restore_att_", ""))
    
    session = Session()
    try:
        user = get_user_by_telegram_id(session, query.from_user.id)
        
        if not user or get_user_role(user) not in ['coach', 'admin']:
            await query.edit_message_text("❌ У вас нет доступа")
            return
        
        attendance = session.query(Attendance).filter_by(id=attendance_id).first()
        if not attendance:
            await query.edit_message_text("❌ Запись о посещении не найдена")
            return
        
        athlete = attendance.athlete
        subscription = attendance.subscription
        
        # Проверяем права
        if isinstance(user, Coach) and athlete.created_by != user.id:
            await query.edit_message_text("❌ Вы не можете восстанавливать тренировки для этого спортсмена")
            return
        
        # Проверяем, что тренировка еще не восстановлена
        if attendance.was_restored:
            await query.edit_message_text("❌ Эта тренировка уже была восстановлена")
            return
        
        # Проверяем, что это пропущенная тренировка
        if attendance.attended:
            await query.edit_message_text("❌ Можно восстановить только пропущенные тренировки")
            return
        
        # Восстанавливаем тренировку
        attendance.was_restored = True
        attendance.restoration_reason = "Восстановлено тренером"
        
        # Возвращаем тренировку в абонемент
        if subscription:
            subscription.trainings_remaining = (subscription.trainings_remaining or 0) + 1
            subscription.total_restored = (subscription.total_restored or 0) + 1
            
            # Обновляем счетчик восстановлений за месяц
            if attendance.created_at:
                now = datetime.utcnow()
                if attendance.created_at.year == now.year and attendance.created_at.month == now.month:
                    subscription.restored_this_month = (subscription.restored_this_month or 0) + 1
        
        session.commit()
        
        training_date = attendance.training.training_date.strftime('%d.%m.%Y %H:%M') if attendance.training else "—"
        
        message = f"✅ <b>ТРЕНИРОВКА ВОССТАНОВЛЕНА</b>\n\n"
        message += f"👤 <b>{html.escape(athlete.full_name)}</b>\n"
        message += f"📅 Тренировка: {training_date}\n"
        message += f"🎫 Осталось тренировок: {subscription.trainings_remaining if subscription else '—'}\n"
        
        keyboard = [
            [InlineKeyboardButton("🔄 Еще восстановить", callback_data=f"restore_{athlete.id}")],
            [InlineKeyboardButton("🔙 Назад к карточке", callback_data=f"athlete_{athlete.id}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            message,
            reply_markup=reply_markup,
            parse_mode='HTML'
        )
    
    except Exception as e:
        logger.error(f"❌ ОШИБКА ПРИ ВОССТАНОВЛЕНИИ ТРЕНИРОВКИ: {e}", exc_info=True)
        session.rollback()
        await query.edit_message_text("❌ Ошибка при восстановлении тренировки")
    finally:
        session.close()


async def select_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать список активных абонементов для выбора"""
    query = update.callback_query
    await query.answer()
    
    athlete_id = int(query.data.replace("select_sub_", ""))
    
    session = Session()
    try:
        user = get_user_by_telegram_id(session, query.from_user.id)
        
        if not user or get_user_role(user) not in ['coach', 'admin']:
            await query.edit_message_text("❌ У вас нет доступа")
            return
        
        athlete = session.query(Athlete).filter_by(id=athlete_id).first()
        if not athlete:
            await query.edit_message_text("❌ Спортсмен не найден")
            return
        
        # Проверяем права
        if isinstance(user, Coach) and athlete.created_by != user.id:
            await query.edit_message_text("❌ Вы не можете просматривать этого спортсмена")
            return
        
        # Получаем все активные абонементы
        active_subs = [s for s in athlete.subscriptions if s.is_active]
        
        if not active_subs:
            await query.edit_message_text("❌ Нет активных абонементов")
            return
        
        if len(active_subs) == 1:
            # Если только один абонемент, просто показываем карточку
            await show_athlete_card(update, context)
            return
        
        message = f"🔄 <b>ВЫБОР АБОНЕМЕНТА</b>\n\n"
        message += f"👤 <b>{html.escape(athlete.full_name)}</b>\n\n"
        message += f"Выберите абонемент для просмотра:\n\n"
        
        keyboard = []
        from utils.subscription_checker import SubscriptionChecker
        
        for sub in active_subs:
            status = SubscriptionChecker.get_subscription_status(sub)
            status_icon = "🟢" if status == "active" else "🟡" if status == "expiring_soon" else "🔴"
            sport_type_display = sub.sport_type or "—"
            trainings = f"{sub.trainings_remaining or 0}/{sub.trainings_total or 0}"
            
            button_text = f"{status_icon} {sport_type_display} ({trainings})"
            if len(button_text) > 64:
                button_text = f"{status_icon} {sport_type_display}"
            
            keyboard.append([
                InlineKeyboardButton(button_text, callback_data=f"view_sub_card_{sub.id}")
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
        logger.error(f"❌ ОШИБКА ПРИ ВЫБОРЕ АБОНЕМЕНТА: {e}", exc_info=True)
        await query.edit_message_text("❌ Ошибка при загрузке абонементов")
    finally:
        session.close()


async def view_subscription_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать карточку спортсмена с выбранным абонементом"""
    query = update.callback_query
    await query.answer()
    
    subscription_id = int(query.data.replace("view_sub_card_", ""))
    
    session = Session()
    try:
        user = get_user_by_telegram_id(session, query.from_user.id)
        
        if not user or get_user_role(user) not in ['coach', 'admin']:
            await query.edit_message_text("❌ У вас нет доступа")
            return
        
        subscription = session.query(Subscription).filter_by(id=subscription_id).first()
        if not subscription:
            await query.edit_message_text("❌ Абонемент не найден")
            return
        
        athlete = subscription.athlete
        
        # Проверяем права
        if isinstance(user, Coach) and athlete.created_by != user.id:
            await query.edit_message_text("❌ Вы не можете просматривать этого спортсмена")
            return
        
        # Сохраняем выбранный абонемент в контексте и показываем карточку
        context.user_data['selected_subscription_id'] = subscription_id
        
        # Показываем карточку спортсмена
        await show_athlete_card(update, context)
    
    except Exception as e:
        logger.error(f"❌ ОШИБКА ПРИ ПРОСМОТРЕ АБОНЕМЕНТА: {e}", exc_info=True)
        await query.edit_message_text("❌ Ошибка при загрузке")
    finally:
        session.close()


async def show_edit_athlete_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать меню редактирования данных спортсмена"""
    query = update.callback_query
    await query.answer()
    
    athlete_id = int(query.data.replace("edit_", ""))
    
    session = Session()
    try:
        user = get_user_by_telegram_id(session, query.from_user.id)
        
        if not user or get_user_role(user) not in ['coach', 'admin']:
            await query.edit_message_text("❌ У вас нет доступа")
            return
        
        athlete = session.query(Athlete).filter_by(id=athlete_id).first()
        if not athlete:
            await query.edit_message_text("❌ Спортсмен не найден")
            return
        
        # Проверяем права
        if isinstance(user, Coach) and athlete.created_by != user.id:
            await query.edit_message_text("❌ Вы не можете редактировать этого спортсмена")
            return
        
        message = f"✏️ <b>РЕДАКТИРОВАНИЕ ДАННЫХ</b>\n\n"
        message += f"👤 <b>{html.escape(athlete.full_name)}</b>\n\n"
        message += "Выберите, что хотите изменить:"
        
        keyboard = [
            [InlineKeyboardButton("📝 ФИО", callback_data=f"edit_name_{athlete_id}")],
            [InlineKeyboardButton("📞 Телефон", callback_data=f"edit_phone_{athlete_id}")],
            [InlineKeyboardButton("🏥 Медицинская информация", callback_data=f"edit_medical_{athlete_id}")],
            [InlineKeyboardButton("🔙 Назад к карточке", callback_data=f"athlete_{athlete_id}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            message,
            reply_markup=reply_markup,
            parse_mode='HTML'
        )
    
    except Exception as e:
        logger.error(f"❌ ОШИБКА ПРИ ПОКАЗЕ МЕНЮ РЕДАКТИРОВАНИЯ: {e}", exc_info=True)
        await query.edit_message_text("❌ Ошибка при загрузке меню редактирования")
    finally:
        session.close()


def _build_freeze_calendar(
    subscription_id: int,
    sport_type: str,
    age_group: str,
    year: int,
    month: int
) -> InlineKeyboardMarkup:
    """
    Календарь выбора даты окончания заморозки.
    Аналогичен календарю активации, но для выбора даты окончания заморозки.
    """
    schedule = _get_schedule(sport_type, age_group)
    training_days = set(schedule["days"]) if schedule else set()

    today = datetime.utcnow().date()

    cal = py_calendar.monthcalendar(year, month)

    keyboard = []

    # Строка дней недели
    day_names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    keyboard.append([InlineKeyboardButton(f"{d}.", callback_data="freeze_ignore") for d in day_names])

    # Ровно 5 недель
    weeks_to_show = cal[:5]
    while len(weeks_to_show) < 5:
        weeks_to_show.append([0, 0, 0, 0, 0, 0, 0])

    for week in weeks_to_show:
        row = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(" ", callback_data="freeze_ignore"))
                continue

            date_obj = datetime(year, month, day).date()
            weekday = date_obj.weekday()

            has_scheduled_training = weekday in training_days
            # Разрешаем выбирать только будущие даты (после сегодня)
            is_future = date_obj > today
            enabled = has_scheduled_training and is_future

            if date_obj == today:
                btn_text = f"[{day:2d}]"
            elif has_scheduled_training:
                btn_text = f"({day:2d})"
            else:
                btn_text = f"{day:2d}"

            cb = f"freeze_date_{subscription_id}_{year}_{month}_{day}" if enabled else "freeze_ignore"
            row.append(InlineKeyboardButton(btn_text, callback_data=cb))

        keyboard.append(row)

    # Навигация
    prev_year, prev_month = year, month - 1
    next_year, next_month = year, month + 1
    if prev_month == 0:
        prev_month = 12
        prev_year -= 1
    if next_month == 13:
        next_month = 1
        next_year += 1

    keyboard.append([
        InlineKeyboardButton("◀️ Предыдущий", callback_data=f"freeze_cal_{subscription_id}_{prev_year}_{prev_month}"),
        InlineKeyboardButton("Следующий ▶️", callback_data=f"freeze_cal_{subscription_id}_{next_year}_{next_month}"),
    ])

    # Кнопка "Сегодня"
    now = datetime.utcnow().date()
    if month != now.month or year != now.year:
        keyboard.append([
            InlineKeyboardButton("📅 Сегодня", callback_data=f"freeze_cal_{subscription_id}_{now.year}_{now.month}")
        ])

    # Навигация/выход
    keyboard.append([
        InlineKeyboardButton("🔙 Назад", callback_data=f"subscription_{subscription_id}"),
        InlineKeyboardButton("🏠 В меню", callback_data="back_to_menu_main"),
    ])

    return InlineKeyboardMarkup(keyboard)


async def handle_freeze_subscription_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало процесса заморозки абонемента - показываем календарь"""
    query = update.callback_query
    await query.answer()

    subscription_id = int(query.data.replace("freeze_sub_", ""))

    session = Session()
    try:
        user = get_user_by_telegram_id(session, query.from_user.id)
        if not user or get_user_role(user) not in ['coach', 'admin']:
            await query.edit_message_text("❌ У вас нет доступа")
            return

        subscription = session.query(Subscription).filter_by(id=subscription_id).first()
        if not subscription:
            await query.edit_message_text("❌ Абонемент не найден")
            return

        athlete = subscription.athlete
        if isinstance(user, Coach) and athlete.created_by != user.id:
            await query.edit_message_text("❌ Вы не можете изменять этот абонемент")
            return

        if not subscription.is_active:
            await query.edit_message_text("❌ Можно заморозить только активный абонемент")
            return

        if subscription.is_frozen:
            await query.edit_message_text("❌ Абонемент уже заморожен")
            return

        sport_type = subscription.sport_type or athlete.sport_type
        age_group = athlete.age_group

        now = datetime.utcnow()
        reply_markup = _build_freeze_calendar(subscription_id, sport_type, age_group, now.year, now.month)

        await query.edit_message_text(
            f"👤 <b>{html.escape(athlete.full_name)}</b>\n\n"
            f"❄️ <b>ЗАМОРОЗКА АБОНЕМЕНТА</b>\n\n"
            f"Выберите дату <b>окончания заморозки</b> (тренировочный день):",
            reply_markup=reply_markup,
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"❌ ОШИБКА ПРИ НАЧАЛЕ ЗАМОРОЗКИ: {e}", exc_info=True)
        await query.edit_message_text("❌ Ошибка при загрузке календаря заморозки")
    finally:
        session.close()


async def handle_freeze_calendar_nav(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Навигация по календарю заморозки"""
    query = update.callback_query
    await query.answer()

    # freeze_cal_{subscription_id}_{YYYY}_{MM}
    parts = query.data.split("_")
    subscription_id = int(parts[2])
    year = int(parts[3])
    month = int(parts[4])

    session = Session()
    try:
        user = get_user_by_telegram_id(session, query.from_user.id)
        if not user or get_user_role(user) not in ['coach', 'admin']:
            await query.edit_message_text("❌ У вас нет доступа")
            return

        subscription = session.query(Subscription).filter_by(id=subscription_id).first()
        if not subscription:
            await query.edit_message_text("❌ Абонемент не найден")
            return

        athlete = subscription.athlete
        sport_type = subscription.sport_type or athlete.sport_type
        age_group = athlete.age_group

        reply_markup = _build_freeze_calendar(subscription_id, sport_type, age_group, year, month)
        await query.edit_message_reply_markup(reply_markup=reply_markup)
    finally:
        session.close()


async def handle_freeze_date_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выбор даты окончания заморозки"""
    query = update.callback_query
    await query.answer()

    # freeze_date_{subscription_id}_{YYYY}_{MM}_{DD}
    parts = query.data.split("_")
    subscription_id = int(parts[2])
    year = int(parts[3])
    month = int(parts[4])
    day = int(parts[5])

    session = Session()
    try:
        user = get_user_by_telegram_id(session, query.from_user.id)
        if not user or get_user_role(user) not in ['coach', 'admin']:
            await query.edit_message_text("❌ У вас нет доступа")
            return

        subscription = session.query(Subscription).filter_by(id=subscription_id).first()
        if not subscription:
            await query.edit_message_text("❌ Абонемент не найден")
            return

        athlete = subscription.athlete
        if isinstance(user, Coach) and athlete.created_by != user.id:
            await query.edit_message_text("❌ Вы не можете изменять этот абонемент")
            return

        # Дата, выбранная тренером (без времени)
        selected_date = datetime(year, month, day, 0, 0, 0)

        # Замораживаем абонемент
        from database.db_utils import freeze_subscription
        result = freeze_subscription(session, subscription_id, selected_date)

        if not result["success"]:
            await query.edit_message_text(f"❌ {result['message']}")
            return

        # Показываем карточку абонемента
        await show_subscription_card(update, context, override_query_data=f"subscription_{subscription.id}")
    except Exception as e:
        logger.error(f"❌ ОШИБКА ПРИ ЗАМОРОЗКЕ: {e}", exc_info=True)
        await query.edit_message_text("❌ Ошибка при заморозке абонемента")
    finally:
        session.close()


async def handle_freeze_ignore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Игнор-кнопка для календаря заморозки"""
    query = update.callback_query
    await query.answer()


async def handle_unfreeze_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Разморозить абонемент"""
    query = update.callback_query
    await query.answer()

    subscription_id = int(query.data.replace("unfreeze_sub_", ""))

    session = Session()
    try:
        user = get_user_by_telegram_id(session, query.from_user.id)
        if not user or get_user_role(user) not in ['coach', 'admin']:
            await query.edit_message_text("❌ У вас нет доступа")
            return

        subscription = session.query(Subscription).filter_by(id=subscription_id).first()
        if not subscription:
            await query.edit_message_text("❌ Абонемент не найден")
            return

        athlete = subscription.athlete
        if isinstance(user, Coach) and athlete.created_by != user.id:
            await query.edit_message_text("❌ Вы не можете изменять этот абонемент")
            return

        # Размораживаем абонемент
        from database.db_utils import unfreeze_subscription
        result = unfreeze_subscription(session, subscription_id)

        if not result["success"]:
            await query.edit_message_text(f"❌ {result['message']}")
            return

        await query.answer("✅ Абонемент разморожен", show_alert=True)

        # Показываем карточку абонемента
        await show_subscription_card(update, context, override_query_data=f"subscription_{subscription.id}")
    except Exception as e:
        logger.error(f"❌ ОШИБКА ПРИ РАЗМОРОЗКЕ: {e}", exc_info=True)
        await query.edit_message_text("❌ Ошибка при разморозке абонемента")
    finally:
        session.close()