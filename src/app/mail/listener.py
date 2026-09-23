import logging
import re

from sqlalchemy import select

from app.classify import classify
from app.config import Settings
from app.mail.ndr import is_ndr, parse_ndr_code
from app.models import B24Job, Enrollment, Incoming, Message, Suppression, utcnow

logger = logging.getLogger("outreacher.listener")

_MSGID_RE = re.compile(r"<[^<>]+>")


def _extract_msgids(value: str) -> list[str]:
    return _MSGID_RE.findall(value or "")


def match_incoming(in_reply_to: str, references: str, known_message_ids: set[str]) -> str | None:
    """Ищет наш Message-ID среди In-Reply-To/References входящего."""
    for msgid in _extract_msgids(in_reply_to) + _extract_msgids(references):
        if msgid in known_message_ids:
            return msgid
    return None


async def process_incoming(session, settings: Settings, parsed: dict) -> Incoming | None:
    """Разбирает одно входящее письмо: дедуп → NDR или классификация → переходы + задачи Б24.

    parsed: {"message_id", "in_reply_to", "references", "from_email", "headers": {lower: value}, "body"}
    """
    message_id = parsed.get("message_id") or ""
    if not message_id:
        return None
    existing = (
        await session.execute(select(Incoming).where(Incoming.message_id == message_id))
    ).scalars().first()
    if existing is not None:
        return None

    from_email = (parsed.get("from_email") or "").lower()
    headers = parsed.get("headers") or {}
    body = parsed.get("body") or ""
    in_reply_to = parsed.get("in_reply_to") or ""

    known = {m.message_id for m in (await session.execute(select(Message))).scalars().all()}
    matched = match_incoming(in_reply_to, parsed.get("references") or "", known)

    inc = Incoming(
        message_id=message_id,
        in_reply_to=matched or in_reply_to,
        from_email=from_email,
        body_snippet=body[:500],
        received_at=utcnow(),
    )

    if is_ndr(from_email, headers):
        inc.is_ndr = True
        inc.classification = "bounce"
        code = parse_ndr_code(body)
        session.add(inc)
        await session.flush()
        if matched and code and code[0] == 5:
            await _apply_hard_bounce(session, matched)
        return inc

    # классификация ответа
    cls, ooo_until = classify(headers, body)
    inc.classification = cls
    session.add(inc)
    await session.flush()

    # привязка к enrollment: по треду или по адресу отправителя
    enrollment = await _resolve_enrollment(session, matched, from_email)
    if enrollment is None:
        await session.commit()
        return inc

    from app.b24map import apply_classification

    await apply_classification(session, settings, inc, enrollment, ooo_until=ooo_until)
    await session.commit()
    return inc


async def _resolve_enrollment(session, matched_msgid: str | None, from_email: str) -> Enrollment | None:
    if matched_msgid:
        msg = (
            await session.execute(select(Message).where(Message.message_id == matched_msgid))
        ).scalars().first()
        if msg is not None:
            return await session.get(Enrollment, msg.enrollment_id)
    # новый тред от адресата активной кампании
    from app.models import Contact

    contact = (
        await session.execute(select(Contact).where(Contact.email == from_email))
    ).scalars().first()
    if contact is None:
        return None
    return (
        await session.execute(
            select(Enrollment)
            .where(
                Enrollment.contact_id == contact.id,
                Enrollment.status.in_(("pending", "active", "paused_ooo")),
            )
            .order_by(Enrollment.id.desc())
        )
    ).scalars().first()


async def _apply_hard_bounce(session, matched_msgid: str) -> None:
    msg = (
        await session.execute(select(Message).where(Message.message_id == matched_msgid))
    ).scalars().first()
    if msg is None:
        return
    msg.status = "bounced"
    enrollment = await session.get(Enrollment, msg.enrollment_id)
    if enrollment is not None:
        enrollment.status = "bounced"
    from app.models import Contact

    contact = None
    if enrollment is not None:
        contact = (
            (await session.execute(select(Contact).where(Contact.id == enrollment.contact_id)))
            .scalars()
            .first()
        )
    if contact is not None:
        existing = (
            await session.execute(select(Suppression).where(Suppression.email == contact.email))
        ).scalars().first()
        if existing is None:
            session.add(Suppression(email=contact.email, reason="hard_bounce", created_at=utcnow()))


async def poll_mailbox(session_factory, settings: Settings, sender) -> None:
    """IMAP IDLE-цикл по одному ящику. Ошибки подключения гасятся до следующего цикла."""
    from imapclient import IMAPClient

    try:
        client = IMAPClient(sender.imap_host, port=sender.imap_port, ssl=True)
        client.login(sender.imap_user, sender.imap_pass)
        client.select_folder("INBOX")
        uids = client.search("UNSEEN")
        for uid in uids:
            raw = client.fetch([uid], ["RFC822"])[uid][b"RFC822"]
            parsed = _parse_raw_message(raw)
            if parsed is None:
                continue
            async with session_factory() as session:
                await process_incoming(session, settings, parsed)
        client.logout()
    except Exception:  # noqa: BLE001
        logger.exception("IMAP poll failed for %s", sender.imap_user)


def _parse_raw_message(raw: bytes) -> dict | None:
    from email import policy
    from email.parser import BytesParser

    msg = BytesParser(policy=policy.default).parsebytes(raw)
    headers = {k.lower(): str(v) for k, v in msg.items()}
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                try:
                    body = part.get_content()
                except Exception:
                    body = ""
                break
    else:
        try:
            body = msg.get_content()
        except Exception:
            body = ""
    from_addr = msg.get("From", "")
    from_email = _extract_addr(from_addr)
    return {
        "message_id": str(msg.get("Message-ID", "")).strip(),
        "in_reply_to": str(msg.get("In-Reply-To", "")).strip(),
        "references": str(msg.get("References", "")).strip(),
        "from_email": from_email,
        "headers": headers,
        "body": body or "",
    }


def _extract_addr(value: str) -> str:
    from email.utils import parseaddr

    return parseaddr(value)[1].lower()
