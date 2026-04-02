import logging
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from database.models import Session, Athlete, Subscription, Training, Attendance
from database.db_utils import get_user_by_telegram_id, get_user_role, is_training_in_global_freeze
from utils.time_utils import now_moscow, training_end_time
from sqlalchemy import func
import html

logger = logging.getLogger(__name__)


def _get_sport_type_name(user) -> str:
    """Нормализовать вид спорта пользователя (связь или строковое поле)."""
    if not user:
        return None
    if getattr(user, "sport_type_rel", None):
        return user.sport_type_rel.name
    return getattr(user, "sport_type", None)


async def select_training_for_attendance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Шаг 2: показать релевантных спортсменов для выбранной тренировки."""
    query = update.callback_query
    await query.answer()

    session = Session()
    try:
        created_training = False
        user = get_user_by_telegram_id(session, query.from_user.id)
        if not user or get_user_role(user) not in ["coach", "admin"]:
            await query.edit_message_text("❌ У вас нет доступа к этому меню")
            return

        data = query.data or ""
        training = None
        training_id = None
        if data.startswith("select_mark_training_virtual_"):
            token = data.replace("select_mark_training_virtual_", "")
            virtual_slots = context.user_data.get("attendance_virtual_slots", {})
            slot = virtual_slots.get(token)
            if not slot:
                await query.edit_message_text("❌ Некорректные данные тренировки")
                return
            sport_type = slot.get("sport_type")
            age_group = slot.get("age_group")
            hour = slot.get("hour")
            minute = slot.get("minute")
            coach_id = slot.get("coach_id")
            if (
                not sport_type
                or age_group not in ("children", "adults")
                or not isinstance(hour, int)
                or not isinstance(minute, int)
            ):
                await query.edit_message_text("❌ Некорректные данные тренировки")
                return
            today = now_moscow()
            training_dt = today.replace(hour=hour, minute=minute, second=0, microsecond=0)
            training_query = session.query(Training).filter_by(
                sport_type=sport_type,
                age_group=age_group,
                training_date=training_dt,
                is_cancelled=False,
            )
            if coach_id is not None:
                training_query = training_query.filter_by(coach_id=coach_id)
            training = training_query.first()
            if not training:
                training_payload = dict(
                    sport_type=sport_type,
                    age_group=age_group,
                    training_date=training_dt,
                    is_cancelled=False,
                )
                if coach_id is not None:
                    training_payload["coach_id"] = coach_id
                training = Training(**training_payload)
                session.add(training)
                session.flush()
                created_training = True
        else:
            training_id = int(data.replace("select_mark_training_", ""))
            training = session.query(Training).filter_by(id=training_id, is_cancelled=False).first()
            if not training:
                await query.edit_message_text("❌ Тренировка не найдена или отменена")
                return

        context.user_data["attendance_selected_training_id"] = training.id

        if get_user_role(user) == "coach":
            if training.coach_id != user.id:
                await query.edit_message_text("❌ Можно выбирать только свои тренировки")
                return
            coach_sport = _get_sport_type_name(user)
            if coach_sport and training.sport_type != coach_sport:
                await query.edit_message_text("❌ Эта тренировка не относится к вашему виду спорта")
                return

        training_day = training.training_date.date()
        athletes_query = (
            session.query(Athlete)
            .join(Subscription, Subscription.athlete_id == Athlete.id)
            .filter(
                Subscription.is_active == True,
                Subscription.sport_type == training.sport_type,
                Athlete.age_group == training.age_group,
                func.date(Subscription.start_date) <= training_day,
                func.date(Subscription.end_date) >= training_day,
            )
            .order_by(Athlete.full_name.asc())
        )
        athletes = athletes_query.all()
        athlete_ids = [a.id for a in athletes]
        attendance_map = {}
        if athlete_ids:
            existing = session.query(Attendance).filter(
                Attendance.training_id == training.id,
                Attendance.athlete_id.in_(athlete_ids),
            ).all()
            attendance_map = {a.athlete_id: a for a in existing}
        total_count = len(athletes)
        marked_count = len(attendance_map)
        attended_count = sum(1 for a in attendance_map.values() if a.attended)
        absent_count = marked_count - attended_count
        pending_count = total_count - marked_count

        age_group_ru = "Детская" if training.age_group == "children" else "Взрослая"
        message = (
            "📝 <b>ОТМЕТКА ПОСЕЩЕНИЯ</b>\n\n"
            f"📅 Тренировка: <b>{training.training_date.strftime('%d.%m.%Y %H:%M')}</b>\n"
            f"🥊 {training.sport_type} | {age_group_ru}\n"
            f"👥 Всего: <b>{total_count}</b> | Отмечено: <b>{marked_count}</b> | Осталось: <b>{pending_count}</b>\n"
            f"✅ Присутствовали: <b>{attended_count}</b> | ❌ Отсутствовали: <b>{absent_count}</b>\n\n"
            "<b>Шаг 2/2: выберите спортсмена</b>"
        )
        if not athletes:
            message += "\n\n📭 На эту тренировку нет активных спортсменов."

        keyboard = []
        for athlete in athletes[:20]:
            att = attendance_map.get(athlete.id)
            if att is None:
                icon = "⏳"
            else:
                icon = "✅" if att.attended else "❌"
            full_name = (athlete.full_name or "").strip()
            if len(full_name) > 24:
                full_name = full_name[:22] + ".."
            keyboard.append([
                InlineKeyboardButton(
                    f"{icon} {full_name}",
                    callback_data=f"mark_attendance_{athlete.id}_{training.id}",
                )
            ])

        if len(athletes) > 20:
            keyboard.append([InlineKeyboardButton(f"📝 Показано 20 из {len(athletes)}", callback_data="show_more_info")])

        keyboard.append([
            InlineKeyboardButton("🔙 К тренировкам", callback_data="attendance_training_list"),
            InlineKeyboardButton("🏠 В меню", callback_data="back_to_menu_main"),
        ])
        if created_training:
            session.commit()
        await query.edit_message_text(message, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    except Exception as e:
        logger.error("❌ ОШИБКА ВЫБОРА ТРЕНИРОВКИ ДЛЯ ОТМЕТКИ: %s", e, exc_info=True)
        await query.edit_message_text("❌ Ошибка при загрузке спортсменов")
    finally:
        session.close()


async def mark_attendance_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало процесса отметки посещения"""
    query = update.callback_query
    await query.answer()

    payload = query.data.replace("mark_attendance_", "")
    parts = payload.split("_")
    if not parts or not parts[0].isdigit():
        await query.edit_message_text("❌ Некорректный формат запроса")
        return

    athlete_id = int(parts[0])
    selected_training_id = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
    context.user_data['mark_attendance_athlete_id'] = athlete_id
    if selected_training_id:
        context.user_data['selected_training_id'] = selected_training_id

    session = Session()
    try:
        athlete = session.query(Athlete).filter_by(id=athlete_id).first()

        if not athlete or not athlete.current_subscription:
            await query.edit_message_text("❌ У спортсмена нет активного абонемента")
            return

        user = get_user_by_telegram_id(session, query.from_user.id)
        if user and get_user_role(user) == "coach":
            coach_sport = _get_sport_type_name(user)
            if coach_sport and athlete.sport_type != coach_sport:
                await query.edit_message_text("❌ Спортсмен не относится к вашему виду спорта")
                return

        if selected_training_id:
            training = session.query(Training).filter_by(id=selected_training_id, is_cancelled=False).first()
            if not training:
                await query.edit_message_text("❌ Выбранная тренировка не найдена")
                return
            if get_user_role(user) == "coach" and training.coach_id != user.id:
                await query.edit_message_text("❌ Можно отмечать только свои тренировки")
                return

            context.user_data['selected_training_id'] = training.id
            keyboard = [
                [
                    InlineKeyboardButton("✅ Был на тренировке", callback_data="mark_present"),
                    InlineKeyboardButton("❌ Не был на тренировке", callback_data="mark_absent")
                ],
                [InlineKeyboardButton("🔙 Назад", callback_data=f"select_mark_training_{training.id}")]
            ]
            await query.edit_message_text(
                "📝 <b>ОТМЕТКА ПОСЕЩЕНИЯ</b>\n\n"
                f"👤 <b>{html.escape(athlete.full_name)}</b>\n"
                f"📅 {training.training_date.strftime('%d.%m.%Y %H:%M')}\n\n"
                "Выберите статус посещения:",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='HTML'
            )
            return
        
        # Рассчитываем дату неделю назад
        week_ago = now_moscow() - timedelta(days=7)

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
            InlineKeyboardButton("🔙 К тренировкам", callback_data="attendance_training_list"),
            InlineKeyboardButton("🏠 В меню", callback_data="back_to_menu_main")
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
    action_signature = f"{athlete_id}:{training_id}:{action}"
    action_guard = context.user_data.get("attendance_click_guard")
    now_ts = now_moscow().timestamp()
    if (
        isinstance(action_guard, dict)
        and action_guard.get("signature") == action_signature
        and isinstance(action_guard.get("ts"), (int, float))
        and (now_ts - action_guard.get("ts")) < 3
    ):
        return
    context.user_data["attendance_click_guard"] = {"signature": action_signature, "ts": now_ts}

    session = Session()
    try:
        athlete = session.query(Athlete).filter_by(id=athlete_id).first()
        training = session.query(Training).filter_by(id=training_id).first()

        if not athlete or not training:
            await query.edit_message_text("❌ Спортсмен или тренировка не найдены")
            return

        # Массовая заморозка = период без списаний и с ограничением действий.
        # Блокируем любые изменения Attendance, чтобы не плодить неконсистентные записи.
        if is_training_in_global_freeze(session, training.training_date):
            await query.edit_message_text(
                "⛔️ В период массовой заморозки отметка посещений недоступна."
                "\n\nСписание тренировок в этот период не производится."
            )
            return

        user = get_user_by_telegram_id(session, query.from_user.id)
        if user and get_user_role(user) == 'coach':
            coach_sport = _get_sport_type_name(user)
            if coach_sport and training.sport_type != coach_sport:
                await query.edit_message_text("❌ Тренировка не относится к вашему виду спорта")
                return
            if training.coach_id != user.id:
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

        # Проверяем, что тренировка уже завершилась (начало + 1.5 часа)
        training_end_datetime = training_end_time(training.training_date)
        current_time = now_moscow()
        if current_time < training_end_datetime:
            await query.edit_message_text(
                f"⏳ Тренировка еще не завершилась.\n\n"
                f"Начало: {training.training_date.strftime('%d.%m.%Y %H:%M')}\n"
                f"Окончание: {training_end_datetime.strftime('%d.%m.%Y %H:%M')}\n\n"
                f"Отметка посещения возможна только после завершения тренировки."
            )
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
            if old_status == attended:
                logger.info(
                    "attendance_noop trainer_tg=%s athlete_id=%s training_id=%s status=%s",
                    query.from_user.id,
                    athlete_id,
                    training_id,
                    "present" if attended else "absent",
                )
                await query.edit_message_text(
                    "ℹ️ Этот статус уже установлен.\n\n"
                    f"📅 Тренировка: {training.training_date.strftime('%d.%m.%Y %H:%M')}\n"
                    f"👤 Спортсмен: {athlete.full_name}"
                )
                return
            existing_attendance.attended = attended
            existing_attendance.marked_by = query.from_user.id
            logger.info(
                "attendance_updated trainer_tg=%s athlete_id=%s training_id=%s old=%s new=%s",
                query.from_user.id,
                athlete_id,
                training_id,
                "present" if old_status else "absent",
                "present" if attended else "absent",
            )

            message = f"✅ Статус обновлен: {'Присутствовал (использовано)' if attended else 'Отсутствовал (неиспользовано)'}"
        else:
            # Создаем новую запись (для старых абонементов без автоматического списания)
            attendance = Attendance(
                athlete_id=athlete_id,
                training_id=training_id,
                subscription_id=subscription.id,
                attended=attended,
                marked_by=query.from_user.id,
                created_at=now_moscow()
            )

            # Списываем тренировку при создании записи (как "использовано" или "неиспользовано").
            # НЕ списываем, если тренировка пришлась на период заморозки абонемента.
            training_in_freeze = (
                subscription.is_frozen
                and subscription.frozen_from
                and subscription.frozen_until
                and subscription.frozen_from <= training.training_date <= subscription.frozen_until
            )
            if (
                not training_in_freeze
                and subscription.trainings_remaining is not None
                and subscription.trainings_remaining > 0
            ):
                subscription.trainings_remaining -= 1

            session.add(attendance)
            logger.info(
                "attendance_created trainer_tg=%s athlete_id=%s training_id=%s status=%s",
                query.from_user.id,
                athlete_id,
                training_id,
                "present" if attended else "absent",
            )
            message = f"✅ Посещение отмечено: {'Присутствовал' if attended else 'Отсутствовал'}"

        session.commit()

        # Формируем итоговое сообщение
        result_message = f"{message}\n"
        result_message += f"📅 Тренировка: {training.training_date.strftime('%d.%m.%Y %H:%M')}\n"
        result_message += f"👤 Спортсмен: {athlete.full_name}\n"
        result_message += f"🎫 Осталось тренировок: {subscription.trainings_remaining}\n"

        keyboard = [
            [
                InlineKeyboardButton("📅 К тренировке", callback_data=f"select_mark_training_{training_id}"),
                InlineKeyboardButton("🎫 К абонементу", callback_data=f"subscription_athlete_{athlete_id}")
            ],
            [InlineKeyboardButton("🏠 В меню", callback_data="back_to_menu_main")]
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
        context.user_data.pop('attendance_selected_training_id', None)

    except Exception as e:
        logger.error(f"❌ ОШИБКА ОТМЕТКИ ПОСЕЩЕНИЯ: {e}")
        session.rollback()
        await query.edit_message_text(f"❌ Ошибка при отметке посещения: {str(e)}")
    finally:
        context.user_data.pop("attendance_click_guard", None)
        session.close()