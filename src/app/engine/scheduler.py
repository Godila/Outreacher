import datetime as dt
import uuid
from dataclasses import dataclass

from sqlalchemy import and_, exists, func, select

from app.config import Settings
from app.engine.render import render_step
from app.models import (
    B24User,
    Campaign,
    Contact,
    Enrollment,
    Message,
    Sender,
    SequenceStep,
    Suppression,
    utcnow,
)


@dataclass
class TickStats:
    created: int = 0
    excluded: int = 0
    deferred: int = 0
    resumed_ooo: int = 0


def _to_local(now_utc: dt.datetime, settings: Settings) -> dt.datetime:
    return now_utc + dt.timedelta(hours=settings.tz_offset_hours)


def _to_utc(local: dt.datetime, settings: Settings) -> dt.datetime:
    return local - dt.timedelta(hours=settings.tz_offset_hours)


def _clamp_to_window(local: dt.datetime, settings: Settings) -> dt.datetime:
    """Сдвигает локальное время в ближайшее разрешённое окно отправки (рабочий день, часы)."""
    start_h, end_h = settings.send_hour_from, settings.send_hour_to
    if local.hour >= end_h:
        local = (local + dt.timedelta(days=1)).replace(hour=start_h, minute=0, second=0, microsecond=0)
    elif local.hour < start_h:
        local = local.replace(hour=start_h, minute=0, second=0, microsecond=0)
    # внутри окна — время не трогаем (границы задают только часы окна)
    while local.weekday() >= 5:  # сб/вс → понедельник
        local = (local + dt.timedelta(days=1)).replace(hour=start_h, minute=0, second=0, microsecond=0)
    return local


def next_send_time(now_utc: dt.datetime, delay_days: int, settings: Settings) -> dt.datetime:
    """+delay_days рабочих дней, затем ближайшее окно. Возвращает UTC (naive)."""
    local = _to_local(now_utc, settings)
    remaining = delay_days
    while remaining > 0:
        local += dt.timedelta(days=1)
        if local.weekday() < 5:
            remaining -= 1
    return _to_utc(_clamp_to_window(local, settings), settings)


def _local_today(now_utc: dt.datetime, settings: Settings) -> str:
    return _to_local(now_utc, settings).strftime("%Y-%m-%d")


def _today_start_utc(now_utc: dt.datetime, settings: Settings) -> dt.datetime:
    local = _to_local(now_utc, settings).replace(hour=0, minute=0, second=0, microsecond=0)
    return _to_utc(local, settings)


async def _pick_sender(session, today: str) -> Sender | None:
    """Отправитель с остатком дневного лимита (ротация: наименьшая загрузка)."""
    rows = (await session.execute(select(Sender).where(Sender.health == "ok"))).scalars().all()
    eligible = []
    for sender in rows:
        if sender.sent_date != today:
            sender.sent_today = 0
            sender.sent_date = today
        if sender.sent_today < sender.daily_limit:
            eligible.append(sender)
    if not eligible:
        return None
    return min(eligible, key=lambda s: s.sent_today)


async def tick(session, now_utc: dt.datetime, settings: Settings) -> TickStats:
    stats = TickStats()
    today = _local_today(now_utc, settings)

    # 1) resume из OOO-паузы
    ooo_rows = (
        await session.execute(
            select(Enrollment).where(Enrollment.status == "paused_ooo", Enrollment.next_send_at <= now_utc)
        )
    ).scalars().all()
    for enrollment in ooo_rows:
        enrollment.status = "active"
        stats.resumed_ooo += 1

    # 2) кандидаты на отправку
    rows = (
        await session.execute(
            select(Enrollment, Contact, Campaign)
            .join(Contact, Enrollment.contact_id == Contact.id)
            .join(Campaign, Enrollment.campaign_id == Campaign.id)
            .where(
                Enrollment.status.in_(("pending", "active")),
                Enrollment.next_send_at.is_not(None),
                Enrollment.next_send_at <= now_utc,
            )
        )
    ).all()

    for enrollment, contact, campaign in rows:
        # стоп-правила
        if campaign.status != "active":
            stats.deferred += 1
            continue
        if contact.has_active_deal:
            enrollment.status = "excluded"
            enrollment.paused_reason = "active_deal"
            stats.excluded += 1
            continue
        suppressed = (
            await session.execute(
                select(Suppression).where(Suppression.email == contact.email)
            )
        ).scalars().first()
        if suppressed:
            enrollment.status = "excluded"
            enrollment.paused_reason = "suppressed"
            stats.excluded += 1
            continue

        next_pos = enrollment.current_step + 1
        steps = (
            await session.execute(
                select(SequenceStep)
                .where(SequenceStep.campaign_id == campaign.id)
                .order_by(SequenceStep.position)
            )
        ).scalars().all()
        step = next((s for s in steps if s.position == next_pos), None)
        if step is None:
            enrollment.status = "done"
            continue

        # окно отправки
        local = _to_local(now_utc, settings)
        in_window = local.weekday() < 5 and settings.send_hour_from <= local.hour < settings.send_hour_to
        if not in_window:
            enrollment.next_send_at = _to_utc(_clamp_to_window(local + dt.timedelta(minutes=1), settings), settings)
            stats.deferred += 1
            continue

        # дневной лимит кампании
        sent_today = (
            await session.execute(
                select(func.count(Message.id))
                .join(Enrollment, Message.enrollment_id == Enrollment.id)
                .where(
                    Enrollment.campaign_id == campaign.id,
                    Message.status == "sent",
                    Message.sent_at >= _today_start_utc(now_utc, settings),
                )
            )
        ).scalar_one()
        if sent_today >= campaign.daily_cap:
            enrollment.next_send_at = next_send_time(now_utc + dt.timedelta(hours=24), 0, settings)
            stats.deferred += 1
            continue

        sender = await _pick_sender(session, today)
        if sender is None:
            enrollment.next_send_at = next_send_time(now_utc + dt.timedelta(hours=1), 0, settings)
            stats.deferred += 1
            continue

        # идемпотентность: шаг уже создан — просто двигаем указатель дальше
        already = (
            await session.execute(
                select(Message).where(
                    Message.enrollment_id == enrollment.id, Message.step_pos == step.position
                )
            )
        ).scalars().first()
        if already is not None:
            enrollment.current_step = step.position
            _schedule_next(enrollment, steps, step, now_utc, settings)
            continue

        # предыдущее письмо треда
        in_reply_to = None
        if step.threaded and step.position > 1:
            prev = (
                await session.execute(
                    select(Message)
                    .where(Message.enrollment_id == enrollment.id, Message.step_pos == step.position - 1)
                    .order_by(Message.id.desc())
                )
            ).scalars().first()
            in_reply_to = prev.message_id if prev else None

        manager = (
            await session.get(B24User, contact.assigned_b24_user_id)
            if contact.assigned_b24_user_id
            else None
        )
        rendered = render_step(step, campaign, contact, manager.name if manager else None, enrollment.id, settings)

        message = Message(
            enrollment_id=enrollment.id,
            step_pos=step.position,
            message_id=f"<{uuid.uuid4().hex}@outreacher>",
            sender_email=sender.email,
            subject=rendered.subject,
            body=rendered.body,
            status="queued",
            smtp_error="",
        )
        session.add(message)
        sender.sent_today += 1

        enrollment.current_step = step.position
        enrollment.status = "active"
        _schedule_next(enrollment, steps, step, now_utc, settings)
        stats.created += 1

    await session.commit()
    return stats


def _schedule_next(
    enrollment: Enrollment,
    steps: list[SequenceStep],
    sent_step: SequenceStep,
    now_utc: dt.datetime,
    settings: Settings,
) -> None:
    following = next((s for s in steps if s.position == sent_step.position + 1), None)
    if following is None:
        enrollment.next_send_at = None  # завершится на следующем tick (нет шага → done)
    else:
        enrollment.next_send_at = next_send_time(now_utc, following.delay_days, settings)
