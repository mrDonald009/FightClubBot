import logging
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes, CallbackQueryHandler
from database.models import Session, Athlete, Subscription, Training, Attendance
from database.db_utils import get_user_by_telegram_id, get_user_role
import html

logger = logging.getLogger(__name__)


async def mark_attendance_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало процесса отметки посещения"""
    query = update.callback_query
    await query.answer()

    athlete_id = int(query.data.replace("mark_attendance_", ""))
    context.user_data['mark_attendance_athlete_id'] = athlete_id

    session = Session()
    try:
        athlete = session.query(Athlete).filter_by(id=athlete_id).first()

        if not athlete or not athlete.current_subscription:
            await query.edit_message_text("❌ У спортсмена нет активного абонемента")
            return

        # Получаем текущего пользователя (тренера), который отмечает посещение
        from database.db_utils import get_user_by_telegram_id
        user = get_user_by_telegram_id(session, query.from_user.id)
        
        # Получаем последние тренировки для отметки
        from utils.training_manager import TrainingManager

        # Рассчитываем дату неделю назад
        week_ago = datetime.utcnow() - timedelta(days=7)

        # Фильтруем тренировки по тренеру (если это тренер)
        query_filter = session.query(Training).filter(
            Training.sport_type == athlete.sport_type,
            Training.age_group == athlete.age_group,
            Training.training_date >= week_ago,
            Training.is_cancelled == False
        )
        
        # Если это тренер, показываем только его тренировки
        if user and get_user_role(user) == 'coach' and getattr(user, "id", None):
            query_filter = query_filter.filter(Training.coach_id == user.id)
        
        trainings = query_filter.order_by(Training.training_date.desc()).limit(5).all()

        if not trainings:
            await query.edit_message_text("❌ Нет тренировок для отметки за последнюю неделю")
            return

        message = f"📅 <b>ОТМЕТКА ПОСЕЩЕНИЯ</b>\n\n"
        message += f"👤 <b>{html.escape(athlete.full_name)}</b>\n"
        message += f"🥊 {athlete.sport_type} | {'Детская' if athlete.age_group == 'children' else 'Взрослая'}\n"
        message += f"🎫 Абонемент #{athlete.current_subscription.id}\n"
        message += f"🏋️ Осталось тренировок: {athlete.current_subscription.trainings_remaining}\n\n"
        message += f"<b>ВЫБЕРИТЕ ТРЕНИРОВКУ:</b>\n"

        keyboard = []

        for training in trainings:
            # Проверяем, не отмечена ли уже эта тренировка
            attendance = session.query(Attendance).filter(
                Attendance.athlete_id == athlete_id,
                Attendance.training_id == training.id
            ).first()

            if attendance:
                status = "✅ Посещена" if attendance.attended else "❌ Пропущена"
                btn_text = f"{status} - {training.training_date.strftime('%d.%m %H:%M')}"
                callback_data = f"view_attendance_{attendance.id}"
            else:
                btn_text = f"📅 {training.training_date.strftime('%d.%m %H:%M')}"
                callback_data = f"select_training_{training.id}"

            keyboard.append([InlineKeyboardButton(btn_text, callback_data=callback_data)])

        keyboard.append([
            InlineKeyboardButton("🔙 Назад", callback_data=f"subscription_athlete_{athlete_id}"),
            InlineKeyboardButton("🏠 В меню", callback_data="back_to_menu")
        ])

        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(
            message,
            reply_markup=reply_markup,
            parse_mode='HTML'
        )

    except Exception as e:
        logger.error(f"❌ ОШИБКА НАЧАЛА ОТМЕТКИ: {e}")
        await query.edit_message_text("❌ Ошибка при загрузке тренировок")
    finally:
        session.close()


async def handle_training_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка выбора тренировки для отметки"""
    query = update.callback_query
    await query.answer()

    training_id = int(query.data.replace("select_training_", ""))
    athlete_id = context.user_data.get('mark_attendance_athlete_id')

    if not athlete_id:
        await query.edit_message_text("❌ Данные сессии утеряны")
        return

    context.user_data['selected_training_id'] = training_id

    keyboard = [
        [
            InlineKeyboardButton("✅ Был на тренировке", callback_data="mark_present"),
            InlineKeyboardButton("❌ Не был на тренировке", callback_data="mark_absent")
        ],
        [InlineKeyboardButton("🔙 Назад", callback_data=f"mark_attendance_{athlete_id}")]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        "📝 <b>ОТМЕТКА ПОСЕЩЕНИЯ</b>\n\n"
        "Выберите статус посещения:",
        reply_markup=reply_markup,
        parse_mode='HTML'
    )


async def execute_mark_attendance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выполнить отметку посещения"""
    query = update.callback_query
    await query.answer()

    action = query.data  # "mark_present" или "mark_absent"
    athlete_id = context.user_data.get('mark_attendance_athlete_id')
    training_id = context.user_data.get('selected_training_id')

    if not athlete_id or not training_id:
        await query.edit_message_text("❌ Данные сессии утеряны")
        return

    attended = (action == "mark_present")

    session = Session()
    try:
        athlete = session.query(Athlete).filter_by(id=athlete_id).first()
        training = session.query(Training).filter_by(id=training_id).first()

        if not athlete or not training:
            await query.edit_message_text("❌ Спортсмен или тренировка не найдены")
            return

        # Проверяем, что тренер может отмечать посещения только для своих тренировок
        from database.db_utils import get_user_by_telegram_id
        user = get_user_by_telegram_id(session, query.from_user.id)
        if user and get_user_role(user) == 'coach' and training.coach_id and training.coach_id != user.id:
            await query.edit_message_text("❌ Вы можете отмечать посещения только для своих тренировок")
            return

        if not athlete.current_subscription:
            await query.edit_message_text("❌ У спортсмена нет активного абонемента")
            return

        subscription = athlete.current_subscription

        # Проверяем, что абонемент активен
        if not subscription.is_active:
            await query.edit_message_text("❌ Абонемент не активен")
            return

        # Если отмечаем присутствие - проверяем наличие тренировок
        if attended and subscription.trainings_remaining <= 0:
            await query.edit_message_text("❌ Нет доступных тренировок в абонементе")
            return

        # Проверяем, не отмечена ли уже эта тренировка
        existing_attendance = session.query(Attendance).filter(
            Attendance.athlete_id == athlete_id,
            Attendance.training_id == training_id
        ).first()

        if existing_attendance:
            # Обновляем существующую запись
            # Тренировка уже списана автоматически, поэтому просто обновляем статус
            old_status = existing_attendance.attended
            existing_attendance.attended = attended
            existing_attendance.marked_by = query.from_user.id

            message = f"✅ Статус обновлен: {'Присутствовал (использовано)' if attended else 'Отсутствовал (неиспользовано)'}"
        else:
            # Создаем новую запись (для старых абонементов без автоматического списания)
            attendance = Attendance(
                athlete_id=athlete_id,
                training_id=training_id,
                subscription_id=subscription.id,
                attended=attended,
                marked_by=query.from_user.id,
                created_at=datetime.utcnow()
            )

            # Списываем тренировку при создании записи (как "использовано" или "неиспользовано").
            # Важно: и присутствие, и отсутствие потребляют тренировку.
            if subscription.trainings_remaining is not None and subscription.trainings_remaining > 0:
                subscription.trainings_remaining -= 1

            session.add(attendance)
            message = f"✅ Посещение отмечено: {'Присутствовал' if attended else 'Отсутствовал'}"

        session.commit()

        # Формируем итоговое сообщение
        result_message = f"{message}\n"
        result_message += f"📅 Тренировка: {training.training_date.strftime('%d.%m.%Y %H:%M')}\n"
        result_message += f"👤 Спортсмен: {athlete.full_name}\n"
        result_message += f"🎫 Осталось тренировок: {subscription.trainings_remaining}\n"

        keyboard = [
            [
                InlineKeyboardButton("📅 Еще тренировка", callback_data=f"mark_attendance_{athlete_id}"),
                InlineKeyboardButton("🎫 К абонементу", callback_data=f"subscription_athlete_{athlete_id}")
            ],
            [InlineKeyboardButton("🏠 В меню", callback_data="back_to_menu")]
        ]

        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(
            result_message,
            reply_markup=reply_markup,
            parse_mode='HTML'
        )

        # Очищаем временные данные
        context.user_data.pop('mark_attendance_athlete_id', None)
        context.user_data.pop('selected_training_id', None)

    except Exception as e:
        logger.error(f"❌ ОШИБКА ОТМЕТКИ ПОСЕЩЕНИЯ: {e}")
        session.rollback()
        await query.edit_message_text(f"❌ Ошибка при отметке посещения: {str(e)}")
    finally:
        session.close()