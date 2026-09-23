import datetime as dt

from sqlalchemy import select

from app.config import Settings
from app.engine.scheduler import next_send_time
from app.models import (
    B24Job,
    Contact,
    Enrollment,
    Incoming,
    Message,
    Suppression,
    utcnow,
)


async def _contact_of(session, enrollment: Enrollment) -> Contact | None:
    return await session.get(Contact, enrollment.contact_id)


async def apply_classification(session, settings: Settings, inc: Incoming, enrollment: Enrollment, ooo_until=None) -> None:
    """Переходы состояния enrollment по классу ответа + постановка задач записи в Б24 (best-effort)."""
    contact = await _contact_of(session, enrollment)
    snippet = (inc.body_snippet or "").replace("\n", " ")[:200]
    b24_id = contact.b24_contact_id if contact else None

    if inc.classification == "positive":
        enrollment.status = "replied"
        enrollment.next_send_at = None
        session.add(B24Job(kind="log_reply", payload={"b24_contact_id": b24_id, "classification": "positive", "snippet": snippet, "message_db_id": inc.id}))
        session.add(B24Job(kind="create_task", payload={"b24_contact_id": b24_id, "assigned_b24_user_id": contact.assigned_b24_user_id if contact else 0, "snippet": snippet}))
    elif inc.classification in ("negative", "referral"):
        enrollment.status = "replied"
        enrollment.next_send_at = None
        session.add(B24Job(kind="log_reply", payload={"b24_contact_id": b24_id, "classification": inc.classification, "snippet": snippet, "message_db_id": inc.id}))
    elif inc.classification == "unsub":
        enrollment.status = "unsubscribed"
        enrollment.next_send_at = None
        if contact is not None:
            existing = (
                await session.execute(select(Suppression).where(Suppression.email == contact.email))
            ).scalars().first()
            if existing is None:
                session.add(Suppression(email=contact.email, reason="unsub", created_at=utcnow()))
        session.add(B24Job(kind="mark_unsubscribed", payload={"b24_contact_id": b24_id, "email": contact.email if contact else ""}))
    elif inc.classification == "ooo":
        until = ooo_until or (inc.received_at or utcnow()) + dt.timedelta(days=5)
        enrollment.status = "paused_ooo"
        enrollment.ooo_until = until
        # возобновление: 1 рабочий день после даты возврата, дальше — окно из next_send_time
        enrollment.next_send_at = next_send_time(until, 1, settings)
    # unknown → без переходов; оператор разметит в инбоксе


async def log_activity_payload(session, message_db_id: int) -> dict | None:
    """Собирает payload для crm.activity.add (TYPE_ID=4, письмо не отправляет сам Б24)."""
    message = await session.get(Message, message_db_id)
    if message is None:
        return None
    enrollment = await session.get(Enrollment, message.enrollment_id)
    if enrollment is None:
        return None
    contact = await _contact_of(session, enrollment)
    if contact is None:
        return None
    return {
        "OWNER_TYPE_ID": 3,  # контакт
        "OWNER_ID": contact.b24_contact_id,
        "TYPE_ID": 4,  # Письмо (crm.enum.activitytype, подтверждено доками)
        "COMMUNICATIONS": [
            {"VALUE": contact.email, "ENTITY_ID": contact.b24_contact_id, "ENTITY_TYPE_ID": 3}
        ],
        "DIRECTION": 2,  # исходящее
        "COMPLETED": "Y",
        "SUBJECT": f"[Outreacher] {message.subject}",
        "DESCRIPTION": message.body,
        "DESCRIPTION_TYPE": 3,  # текст
        "SETTINGS": {"DISABLE_SENDING_MESSAGE_COPY": "Y"},  # Б24 не отправляет копию сам
        "RESPONSIBLE_ID": contact.assigned_b24_user_id or 1,
        "START_TIME": (message.sent_at or utcnow()).isoformat(),
        "_message_db_id": message.id,
    }


async def process_b24_jobs(session, client) -> int:
    """Исполняет накопленные задачи записи в Б24. Возвращает число успешно закрытых."""
    done = 0
    jobs = (
        await session.execute(
            select(B24Job).where(B24Job.done.is_(False), B24Job.attempts < 5).order_by(B24Job.id)
        )
    ).scalars().all()
    for job in jobs:
        job.attempts += 1
        try:
            if job.kind == "log_activity":
                payload = await log_activity_payload(session, job.payload["message_db_id"])
                if payload is None:
                    job.done = True
                    job.last_error = "message/enrollment/contact not found"
                    continue
                message_db_id = payload.pop("_message_db_id")
                activity_id = await client.call("crm.activity.add", {"fields": payload})
                message = await session.get(Message, message_db_id)
                if message is not None and isinstance(activity_id, int):
                    message.b24_activity_id = activity_id
            elif job.kind == "log_reply":
                p = job.payload
                comment_id = await client.call(
                    "crm.timeline.comment.add",
                    {
                        "fields": {
                            "ENTITY_TYPE": "contact",
                            "ENTITY_ID": p["b24_contact_id"],
                            "COMMENT": f"[Outreacher] Ответ клиента ({p['classification']}): {p['snippet']}",
                        }
                    },
                )
                inc = await session.get(Incoming, p["message_db_id"])
                if inc is not None and isinstance(comment_id, int):
                    inc.b24_comment_id = comment_id
            elif job.kind == "create_task":
                p = job.payload
                await client.call(
                    "crm.activity.add",
                    {
                        "fields": {
                            "OWNER_TYPE_ID": 3,
                            "OWNER_ID": p["b24_contact_id"],
                            "TYPE_ID": 3,  # Задача
                            "COMMUNICATIONS": [{"ENTITY_ID": p["b24_contact_id"], "ENTITY_TYPE_ID": 3}],
                            "SUBJECT": "Ответ клиента — связаться",
                            "DESCRIPTION": p["snippet"],
                            "DEADLINE": (utcnow() + dt.timedelta(hours=4)).isoformat(),
                            "COMPLETED": "N",
                            "RESPONSIBLE_ID": p["assigned_b24_user_id"] or 1,
                            "COLOR": "#f3b52c",
                        }
                    },
                )
            elif job.kind == "mark_unsubscribed":
                p = job.payload
                await client.call(
                    "crm.timeline.comment.add",
                    {
                        "fields": {
                            "ENTITY_TYPE": "contact",
                            "ENTITY_ID": p["b24_contact_id"],
                            "COMMENT": "[Outreacher] Клиент отписался от рассылок",
                        }
                    },
                )
            else:
                job.done = True
                job.last_error = f"unknown kind {job.kind}"
                continue
            job.done = True
            job.last_error = ""
            done += 1
        except Exception as exc:  # noqa: BLE001 — задачи ретраятся до attempts<5
            job.last_error = f"{type(exc).__name__}: {exc}"[:500]
    await session.commit()
    return done
