import datetime as dt

import pytest
from sqlalchemy import select

from app.b24.client import Bitrix24Error
from app.b24map import log_activity_payload, process_b24_jobs
from app.config import Settings
from app.models import B24Job, Campaign, Contact, Enrollment, Incoming, Message


def _settings():
    return Settings(app_secret="sec", base_url="https://o.ru",
                    legal_entity="T", legal_address="A", legal_phone="P")


async def _seed(db_session):
    contact = Contact(b24_contact_id=7, email="cli@x.ru", name="К", assigned_b24_user_id=5)
    campaign = Campaign(name="C", slug="camp", status="active")
    db_session.add_all([contact, campaign])
    await db_session.flush()
    enrollment = Enrollment(campaign_id=campaign.id, contact_id=contact.id, status="active", current_step=1)
    db_session.add(enrollment)
    await db_session.flush()
    msg = Message(enrollment_id=enrollment.id, step_pos=1, message_id="<m@outreacher>",
                  sender_email="a@n.ru", subject="Тема письма", body="Тело", status="sent",
                  sent_at=dt.datetime(2026, 9, 21))
    db_session.add(msg)
    await db_session.flush()
    db_session.add(B24Job(kind="log_activity", payload={"message_db_id": msg.id}))
    await db_session.flush()
    return contact, enrollment, msg


class RecordingClient:
    def __init__(self, fail_first=0):
        self.calls: list[tuple[str, dict]] = []
        self.fail_first = fail_first

    async def call(self, method, params=None):
        self.calls.append((method, params or {}))
        if self.fail_first > 0:
            self.fail_first -= 1
            raise Bitrix24Error("QUERY_LIMIT_EXCEEDED", "too many")
        return 100 + len(self.calls)


async def test_log_activity_payload_fields(db_session):
    contact, enrollment, msg = await _seed(db_session)
    payload = await log_activity_payload(db_session, msg.id)
    assert payload["TYPE_ID"] == 4  # Письмо
    assert payload["OWNER_TYPE_ID"] == 3  # Контакт
    assert payload["OWNER_ID"] == 7
    assert payload["DIRECTION"] == 2 and payload["COMPLETED"] == "Y"
    assert payload["SETTINGS"] == {"DISABLE_SENDING_MESSAGE_COPY": "Y"}
    assert len(payload["COMMUNICATIONS"]) == 1
    assert payload["COMMUNICATIONS"][0]["VALUE"] == "cli@x.ru"


async def test_process_jobs_success(db_session):
    contact, enrollment, msg = await _seed(db_session)
    client = RecordingClient()
    done = await process_b24_jobs(db_session, client)
    assert done == 1
    method, params = client.calls[0]
    assert method == "crm.activity.add"
    assert params["fields"]["TYPE_ID"] == 4
    job = (await db_session.execute(select(B24Job))).scalars().one()
    assert job.done is True and job.attempts == 1
    assert msg.b24_activity_id is not None


async def test_process_jobs_retry_on_error(db_session):
    contact, enrollment, msg = await _seed(db_session)
    client = RecordingClient(fail_first=1)
    done = await process_b24_jobs(db_session, client)
    assert done == 0
    job = (await db_session.execute(select(B24Job))).scalars().one()
    assert job.done is False and job.attempts == 1 and "QUERY_LIMIT" in job.last_error
    # второй прогон закрывает задачу
    done2 = await process_b24_jobs(db_session, client)
    assert done2 == 1 and job.done is True
