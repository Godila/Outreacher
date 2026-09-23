from sqlalchemy import select

from app.config import Settings
from app.mail.sender import SmtpSender, send_pending
from app.models import B24Job, Campaign, Contact, Enrollment, Message, Sender, Suppression


class FakeSmtp:
    def __init__(self, fail_for: set[str] | None = None, transient_for: set[str] | None = None):
        self.fail_for = fail_for or set()
        self.transient_for = transient_for or set()
        self.sent: list = []

    async def send(self, sender, mime):
        to = mime["To"]
        if to in self.fail_for:
            class Refused(Exception):
                code = 550

            raise Refused(f"550 5.1.1 <{to}>: recipient address rejected")
        if to in self.transient_for:
            class Greylisted(Exception):
                code = 450

            raise Greylisted("450 4.2.1 greylisted")
        self.sent.append(mime)


def _settings():
    return Settings(app_secret="sec", base_url="https://out.example.ru",
                    legal_entity="T", legal_address="A", legal_phone="P")


async def _seed(db_session, emails=("cli@x.ru",)):
    campaign = Campaign(name="C", slug="camp1", status="active")
    db_session.add(campaign)
    await db_session.flush()
    sender = Sender(email="anna@news.example.ru", display_name="Анна", smtp_host="s", smtp_user="u", smtp_pass="p",
                    imap_host="i", imap_user="u", imap_pass="p")
    db_session.add(sender)
    messages = []
    for email in emails:
        contact = Contact(b24_contact_id=len(emails) - emails.index(email), email=email, name="К")
        db_session.add(contact)
        await db_session.flush()
        enrollment = Enrollment(campaign_id=campaign.id, contact_id=contact.id, status="active", current_step=1)
        db_session.add(enrollment)
        await db_session.flush()
        msg = Message(enrollment_id=enrollment.id, step_pos=1, message_id=f"<m{len(messages)}@outreacher>",
                      sender_email=sender.email, subject="S", body="B", status="queued")
        db_session.add(msg)
        messages.append(msg)
    await db_session.flush()
    return campaign, sender, messages


async def test_send_success_marks_sent_and_queues_b24(db_session):
    campaign, sender, messages = await _seed(db_session)
    smtp = FakeSmtp()
    stats = await send_pending(db_session, _settings(), smtp)
    assert stats.sent == 1
    assert smtp.sent[0]["List-Unsubscribe"].startswith("<https://out.example.ru/unsub/")
    assert smtp.sent[0]["Feedback-ID"] == f"camp1:1:{sender.email}"
    fresh = await db_session.get(Message, messages[0].id)
    assert fresh.status == "sent" and fresh.sent_at is not None
    jobs = (await db_session.execute(select(B24Job))).scalars().all()
    assert len(jobs) == 1 and jobs[0].kind == "log_activity"


async def test_hard_bounce_suppresses(db_session):
    campaign, sender, messages = await _seed(db_session, emails=("bad@x.ru",))
    stats = await send_pending(db_session, _settings(), FakeSmtp(fail_for={"bad@x.ru"}))
    assert stats.hard_bounced == 1
    msg = (await db_session.execute(select(Message))).scalars().one()
    assert msg.status == "bounced"
    sup = (await db_session.execute(select(Suppression))).scalars().one()
    assert sup.reason == "hard_bounce" and sup.email == "bad@x.ru"
    enrollment = await db_session.get(Enrollment, msg.enrollment_id)
    assert enrollment.status == "bounced"


async def test_transient_retries_then_fails(db_session):
    campaign, sender, messages = await _seed(db_session, emails=("grey@x.ru",))
    smtp = FakeSmtp(transient_for={"grey@x.ru"})
    s1 = await send_pending(db_session, _settings(), smtp)
    assert s1.deferred == 1 and s1.sent == 0
    s2 = await send_pending(db_session, _settings(), smtp)
    s3 = await send_pending(db_session, _settings(), smtp)
    assert s3.failed == 1
    msg = (await db_session.execute(select(Message))).scalars().one()
    assert msg.status == "failed" and msg.retry_count == 2


async def test_idempotent_no_resend(db_session):
    campaign, sender, messages = await _seed(db_session)
    smtp = FakeSmtp()
    await send_pending(db_session, _settings(), smtp)
    await send_pending(db_session, _settings(), smtp)
    assert len(smtp.sent) == 1
