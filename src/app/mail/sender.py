import datetime as dt
from dataclasses import dataclass

from sqlalchemy import select

from app.config import Settings
from app.engine.unsub import make_token
from app.mail.builder import build_mime
from app.models import (
    B24Job,
    Campaign,
    Contact,
    Enrollment,
    Message,
    Sender,
    Suppression,
    utcnow,
)

RETRY_LIMIT = 3


@dataclass
class SendStats:
    sent: int = 0
    failed: int = 0
    deferred: int = 0
    hard_bounced: int = 0


class SmtpSender:
    """Реальная отправка через aiosmtplib (заменяется фейком в тестах)."""

    async def send(self, sender: Sender, mime) -> None:
        import aiosmtplib

        await aiosmtplib.send(
            mime,
            hostname=sender.smtp_host,
            port=sender.smtp_port,
            username=sender.smtp_user,
            password=sender.smtp_pass,
            use_tls=True,
            timeout=60,
        )


def _is_hard_refusal(exc: Exception) -> bool:
    """5xx на RCPT/команде — адрес не существует / окончательно отвергнут."""
    code = getattr(exc, "code", None)
    if code is None and hasattr(exc, "recipients"):
        # aiosmtplib SMTPRecipientsRefused: словарь адрес -> (code, msg)
        codes = [c for _addr, (c, _msg) in (getattr(exc, "recipients", {}) or {}).values()]
        code = codes[0] if codes else None
    if code is None:
        return not _is_transient(exc)
    return 500 <= int(code) < 600


def _is_transient(exc: Exception) -> bool:
    code = getattr(exc, "code", None)
    if code is None and hasattr(exc, "recipients"):
        codes = [c for _addr, (c, _msg) in (getattr(exc, "recipients", {}) or {}).values()]
        code = codes[0] if codes else None
    if code is not None:
        return 400 <= int(code) < 500
    # сетевые/коннектные ошибки — тоже транзиентные
    return True


async def send_pending(session, settings: Settings, smtp: SmtpSender | None = None) -> SendStats:
    """Outbox: забирает queued, шлёт, фиксирует статус; жёсткий отказ → suppression."""
    stats = SendStats()
    smtp = smtp or SmtpSender()

    messages = (
        await session.execute(select(Message).where(Message.status == "queued").order_by(Message.id))
    ).scalars().all()
    senders = {s.email: s for s in (await session.execute(select(Sender))).scalars().all()}

    for message in messages:
        enrollment = await session.get(Enrollment, message.enrollment_id)
        if enrollment is None:
            message.status = "failed"
            message.smtp_error = "enrollment not found"
            stats.failed += 1
            continue
        contact = await session.get(Contact, enrollment.contact_id)
        campaign = await session.get(Campaign, enrollment.campaign_id)
        sender = senders.get(message.sender_email)
        if contact is None or campaign is None or sender is None:
            message.status = "failed"
            message.smtp_error = "contact/campaign/sender not found"
            stats.failed += 1
            continue

        unsub_token = make_token(enrollment.id, contact.email, settings.app_secret)
        in_reply_to = None
        if message.step_pos > 1:
            prev = (
                await session.execute(
                    select(Message)
                    .where(Message.enrollment_id == enrollment.id, Message.step_pos == message.step_pos - 1)
                    .order_by(Message.id.desc())
                )
            ).scalars().first()
            in_reply_to = prev.message_id if prev else None

        mime = build_mime(
            from_email=sender.email,
            from_name=sender.display_name or sender.email.split("@")[0],
            to_email=contact.email,
            subject=message.subject,
            body=message.body,
            unsub_url=f"{settings.base_url}/unsub/{unsub_token}",
            feedback_id=f"{campaign.slug}:{enrollment.id}:{sender.email}",
            message_id=message.message_id,
            in_reply_to=in_reply_to,
            references=in_reply_to,
        )

        try:
            await smtp.send(sender, mime)
        except Exception as exc:  # noqa: BLE001 — классифицируем любые SMTP-сбои
            message.smtp_error = f"{type(exc).__name__}: {exc}"[:500]
            if _is_hard_refusal(exc):
                message.status = "bounced"
                stats.hard_bounced += 1
                existing = (
                    await session.execute(
                        select(Suppression).where(Suppression.email == contact.email)
                    )
                ).scalars().first()
                if existing is None:
                    session.add(Suppression(email=contact.email, reason="hard_bounce", created_at=utcnow()))
                enrollment.status = "bounced"
            elif message.retry_count + 1 >= RETRY_LIMIT:
                message.status = "failed"
                stats.failed += 1
            else:
                message.retry_count += 1
                stats.deferred += 1
            continue

        message.status = "sent"
        message.sent_at = utcnow()
        message.smtp_error = ""
        stats.sent += 1
        session.add(
            B24Job(
                kind="log_activity",
                payload={
                    "message_db_id": message.id,
                    "b24_contact_id": contact.b24_contact_id,
                    "email": contact.email,
                },
            )
        )

    await session.commit()
    return stats
