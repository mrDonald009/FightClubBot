"""Сервис read-only аудита абонементов."""
from collections import Counter
from typing import Dict, List

from sqlalchemy.orm import Session

from sqlalchemy import or_

from database.models import Subscription, Athlete, GlobalFreeze, GlobalFreezeApplication, Training, Attendance
from database.db_utils import calculate_actual_trainings_remaining, _active_global_freeze_covers_training_exists
from utils.training_manager import TrainingManager
from utils.time_utils import now_moscow


def run_subscription_audit(session: Session) -> Dict:
    """
    Выполнить read-only аудит абонементов.
    Ничего не изменяет в БД, только собирает несоответствия.
    """
    issues: List[Dict] = []
    now = now_moscow()
    subscriptions = session.query(Subscription).all()

    # Глобальная проверка: активные массовые заморозки не должны пересекаться.
    active_freezes = (
        session.query(GlobalFreeze)
        .filter(GlobalFreeze.is_active == True)
        .order_by(GlobalFreeze.start_date.asc(), GlobalFreeze.id.asc())
        .all()
    )
    for idx, left in enumerate(active_freezes):
        for right in active_freezes[idx + 1:]:
            if right.start_date > left.end_date:
                break
            if left.start_date <= right.end_date and left.end_date >= right.start_date:
                issues.append(
                    {
                        "subscription_id": 0,
                        "athlete_name": "SYSTEM",
                        "code": "overlapping_global_freezes",
                        "severity": "critical",
                        "detail": (
                            f"Пересечение активных массовых заморозок: "
                            f"#{left.id} ({left.start_date.strftime('%d.%m.%Y')}—{left.end_date.strftime('%d.%m.%Y')}) и "
                            f"#{right.id} ({right.start_date.strftime('%d.%m.%Y')}—{right.end_date.strftime('%d.%m.%Y')})"
                        ),
                    }
                )

    for sub in subscriptions:
        athlete = sub.athlete
        athlete_name = athlete.full_name if athlete else "Неизвестный спортсмен"

        def add_issue(code: str, severity: str, detail: str) -> None:
            issues.append(
                {
                    "subscription_id": sub.id,
                    "athlete_name": athlete_name,
                    "code": code,
                    "severity": severity,
                    "detail": detail,
                }
            )

        if not athlete:
            add_issue("missing_athlete", "critical", "У абонемента отсутствует связанный спортсмен")
            continue

        if sub.subscription_type not in (None, "monthly", "single", "individual"):
            add_issue("bad_type", "critical", f"Недопустимый тип: {sub.subscription_type}")

        if sub.start_date and sub.end_date and sub.start_date > sub.end_date:
            add_issue(
                "bad_date_order",
                "critical",
                f"Дата начала позже даты окончания: {sub.start_date} > {sub.end_date}",
            )

        if sub.is_active and (not sub.start_date or not sub.end_date):
            add_issue("active_missing_dates", "critical", "Активный абонемент без start_date/end_date")

        if sub.is_active and sub.end_date and sub.end_date < now:
            add_issue(
                "active_but_expired",
                "warning",
                f"Активный, но уже истек: {sub.end_date.strftime('%d.%m.%Y %H:%M')}",
            )

        if sub.trainings_total is not None and sub.trainings_total < 0:
            add_issue("total_negative", "critical", f"trainings_total={sub.trainings_total}")
        if sub.trainings_remaining is not None and sub.trainings_remaining < 0:
            add_issue("remaining_negative", "critical", f"trainings_remaining={sub.trainings_remaining}")
        if (
            sub.trainings_total is not None
            and sub.trainings_remaining is not None
            and sub.trainings_remaining > sub.trainings_total
        ):
            add_issue(
                "remaining_gt_total",
                "critical",
                f"{sub.trainings_remaining}>{sub.trainings_total}",
            )

        if sub.subscription_type in ("single", "individual") and sub.trainings_total != 1:
            add_issue("single_total_not_1", "warning", f"trainings_total={sub.trainings_total}")

        if sub.is_frozen and (not sub.frozen_from or not sub.frozen_until):
            add_issue(
                "frozen_missing_bounds",
                "critical",
                f"frozen_from={sub.frozen_from}, frozen_until={sub.frozen_until}",
            )
        if sub.frozen_from and sub.frozen_until and sub.frozen_until < sub.frozen_from:
            add_issue(
                "frozen_bad_range",
                "critical",
                f"{sub.frozen_from} > {sub.frozen_until}",
            )
        if sub.frozen_from and sub.start_date and sub.frozen_from < sub.start_date:
            add_issue(
                "freeze_before_start",
                "warning",
                f"frozen_from={sub.frozen_from} раньше start_date={sub.start_date}",
            )

        try:
            calculated_remaining = calculate_actual_trainings_remaining(session, sub)
            if (
                calculated_remaining is not None
                and sub.trainings_remaining is not None
                and calculated_remaining != sub.trainings_remaining
            ):
                add_issue(
                    "remaining_mismatch",
                    "warning",
                    f"stored={sub.trainings_remaining}, calculated={calculated_remaining}",
                )
        except Exception as exc:  # pragma: no cover
            add_issue("remaining_calc_error", "critical", str(exc))

        if sub.is_active and sub.start_date:
            sport_type = sub.sport_type or athlete.sport_type
            age_group = athlete.age_group
            schedule = TrainingManager.TRAINING_SCHEDULE.get(sport_type, {}).get(age_group) if sport_type and age_group else None
            if not schedule:
                add_issue(
                    "missing_schedule",
                    "warning",
                    f"Нет расписания для {sport_type}/{age_group}",
                )
            else:
                expected_hour, expected_minute = TrainingManager.get_hour_minute_for_weekday(
                    schedule, sub.start_date.weekday()
                )
                if (sub.start_date.hour, sub.start_date.minute) != (expected_hour, expected_minute):
                    add_issue(
                        "start_time_mismatch",
                        "warning",
                        (
                            f"start_date={sub.start_date.strftime('%d.%m.%Y %H:%M')}, "
                            f"ожидалось {expected_hour:02d}:{expected_minute:02d}"
                        ),
                    )

        # Массовая заморозка уже применялась, но итоговая end_date у абонемента
        # стала меньше рассчитанной new_end_date из GlobalFreezeApplication.
        gf_apps = (
            session.query(GlobalFreezeApplication)
            .join(GlobalFreeze, GlobalFreezeApplication.global_freeze_id == GlobalFreeze.id)
            .filter(
                GlobalFreezeApplication.subscription_id == sub.id,
                GlobalFreezeApplication.training_days_added > 0,
                GlobalFreezeApplication.new_end_date.isnot(None),
                GlobalFreeze.is_active == True,
            )
            .all()
        )
        if gf_apps and sub.end_date:
            expected_end_date = max(app.new_end_date for app in gf_apps if app.new_end_date is not None)
            if expected_end_date and sub.end_date < expected_end_date:
                add_issue(
                    "global_freeze_lost_end_date",
                    "critical",
                    (
                        f"Текущий end_date={sub.end_date.strftime('%d.%m.%Y %H:%M')} "
                        f"меньше expected={expected_end_date.strftime('%d.%m.%Y %H:%M')}"
                    ),
                )

        # Авто-списание (marked_by IS NULL) внутри периода активной массовой заморозки —
        # расхождение с auto_deduct / migrate (раньше migrate не проверял global freeze).
        bad_gf = (
            session.query(Training.training_date)
            .join(Attendance, Attendance.training_id == Training.id)
            .filter(Attendance.subscription_id == sub.id)
            .filter(or_(Attendance.was_restored == False, Attendance.was_restored == None))
            .filter(Attendance.marked_by.is_(None))
            .filter(_active_global_freeze_covers_training_exists())
            .order_by(Training.training_date)
            .limit(5)
            .all()
        )
        if bad_gf:
            sample = ", ".join(d[0].strftime("%d.%m.%Y %H:%M") for d in bad_gf)
            add_issue(
                "auto_deduction_during_global_freeze",
                "critical",
                f"Авто-списание в окне массовой заморозки (примеры слотов: {sample})",
            )

        # Безопасность: ручные отметки в активной массовой заморозке
        # (чтобы не было неконсистентной статистики и разночтений по UI).
        manual_bad_gf = (
            session.query(Training.training_date)
            .join(Attendance, Attendance.training_id == Training.id)
            .filter(Attendance.subscription_id == sub.id)
            .filter(_active_global_freeze_covers_training_exists())
            .filter(Attendance.marked_by.isnot(None))
            .order_by(Training.training_date)
            .limit(5)
            .all()
        )
        if manual_bad_gf:
            sample = ", ".join(d[0].strftime("%d.%m.%Y %H:%M") for d in manual_bad_gf)
            add_issue(
                "manual_attendance_during_global_freeze",
                "warning",
                f"Ручные отметки в окне массовой заморозки (примеры слотов: {sample})",
            )

    severity_counts = Counter(issue["severity"] for issue in issues)
    code_counts = Counter(issue["code"] for issue in issues)

    return {
        "total_subscriptions": len(subscriptions),
        "issues_total": len(issues),
        "severity_counts": dict(severity_counts),
        "code_counts": dict(code_counts),
        "issues": issues,
        "checked_at": now,
    }


def format_audit_report(report: Dict, *, max_items: int = 20) -> str:
    """Сформировать компактный текст отчета для Telegram."""
    severity_counts = report.get("severity_counts", {})
    critical = severity_counts.get("critical", 0)
    warning = severity_counts.get("warning", 0)
    info = severity_counts.get("info", 0)

    lines = [
        "🩺 <b>Ежесуточный аудит абонементов</b>",
        "",
        f"• Проверено: <b>{report.get('total_subscriptions', 0)}</b>",
        f"• Проблем: <b>{report.get('issues_total', 0)}</b>",
        f"• Critical: <b>{critical}</b> | Warning: <b>{warning}</b> | Info: <b>{info}</b>",
    ]

    issues = report.get("issues", [])
    if not issues:
        lines.append("")
        lines.append("✅ Нарушений не обнаружено.")
        return "\n".join(lines)

    lines.append("")
    lines.append("<b>Примеры:</b>")
    for issue in issues[:max_items]:
        lines.append(
            f"• #{issue['subscription_id']} | {issue['athlete_name']} | "
            f"<code>{issue['code']}</code> | {issue['detail']}"
        )

    truncated = len(issues) - max_items
    if truncated > 0:
        lines.append(f"… и еще {truncated} записей.")

    return "\n".join(lines)
