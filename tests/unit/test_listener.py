import datetime as dt

from sqlalchemy import select

from app.config import Settings
from app.mail.listener import match_incoming, process_incoming
from app.mail.ndr import is_ndr, parse_ndr_code
from app.models import Campaign, Contact, Enrollment, Incoming, Message, Suppression

NOW = dt.datetime(2026, 9, 23, 10, 0)


def _settings():
    return Settings(tz_offset_hours=3, send_hour_from=8, send_hour_to=12,
                    app_secret="sec", base_url="https://o.ru",
                    legal_entity="T", legal_address="A", legal_phone="P")


def test_match_incoming_among_references():
    known = {"<a@outreacher>", "<b@outreacher>"}
    assert match_incoming("<zz@x.ru>", "<a@outreacher> <q@x.ru>", known) == "<a@outreacher>"
    assert match_incoming("", "<c@outreacher>", known) is None


def test_is_ndr():
    assert is_ndr("MAILER-DAEMON@mx.mail.ru", {"auto-submitted": "auto-generated"})
    assert is_ndr("robot@x.ru", {"x-failed-recipients": "a@b.ru"})
    assert not is_ndr("client@firm.ru", {"auto-submitted": "no"})
    assert not is_ndr("mailer-daemon@x.ru", {})


def test_parse_ndr_code():
    assert parse_ndr_code("Diagnostic-Code: smtp; 550 5.1.1 user unknown") == (5, 1, 1)
    assert parse_ndr_code("status 4.2.1 greylisted, try later") == (4, 2, 1)
    assert parse_ndr_code("no codes here") is None


async def _seed_reply(db_session):
    contact = Contact(b24_contact_id=7, email="cli@x.ru", name="Клиент", assigned_b24_user_id=5)
    campaign = Campaign(name="C", slug="camp", status="active")
    db_session.add_all([contact, campaign])
    await db_session.flush()
    enrollment = Enrollment(campaign_id=campaign.id, contact_id=contact.id, status="active", current_step=1)
    db_session.add(enrollment)
    await db_session.flush()
    db_session.add(Message(enrollment_id=enrollment.id, step_pos=1, message_id="<m1@outreacher>",
                           sender_email="a@n.ru", subject="S", body="B", status="sent",
                           sent_at=dt.datetime(2026, 9, 21)))
    await db_session.flush()
    return contact, enrollment


def _reply(message_id, body, in_reply_to="<m1@outreacher>", auto=False, frm="cli@x.ru"):
    headers = {"auto-submitted": "auto-replied"} if auto else {"auto-submitted": "no"}
    return {
        "message_id": message_id, "in_reply_to": in_reply_to, "references": in_reply_to,
        "from_email": frm, "headers": headers, "body": body,
    }


async def test_positive_reply_stops_and_queues_b24(db_session):
    contact, enrollment = await _seed_reply(db_session)
    inc = await process_incoming(db_session, _settings(), _reply("<r1@x.ru>", "Да, интересно! Давайте созвонимся"))
    assert inc is not None and inc.classification == "positive"
    assert enrollment.status == "replied" and enrollment.next_send_at is None
    from app.models import B24Job

    jobs = (await db_session.execute(select(B24Job))).scalars().all()
    kinds = {j.kind for j in jobs}
    assert kinds == {"log_reply", "create_task"}

    # дедуп: то же письмо не создаёт вторую запись
    again = await process_incoming(db_session, _settings(), _reply("<r1@x.ru>", "Да, интересно"))
    assert again is None


async def test_ooo_pauses_with_return_date(db_session):
    contact, enrollment = await _seed_reply(db_session)
    # сейчас сентябрь 2026 (fixture NOW внутри classify не параметризована — дата близкая к настоящей)
    inc = await process_incoming(
        db_session, _settings(),
        _reply("<r2@x.ru>", "В отпуске, вернусь 25 сентября, отвечу после", auto=True),
    )
    assert inc.classification == "ooo"
    assert enrollment.status == "paused_ooo"
    assert enrollment.ooo_until is not None and enrollment.next_send_at is not None
    assert enrollment.next_send_at > enrollment.ooo_until  # +1 раб. день после возврата


async def test_hard_bounce_ndr(db_session):
    contact, enrollment = await _seed_reply(db_session)
    inc = await process_incoming(
        db_session, _settings(),
        _reply("<ndr1@mx.ru>", "Delivery failure: 550 5.1.1 <cli@x.ru> user unknown",
               in_reply_to="<m1@outreacher>", auto=True, frm="mailer-daemon@mx.x.ru"),
    )
    assert inc.is_ndr is True
    msg = (await db_session.execute(select(Message))).scalars().one()
    assert msg.status == "bounced"
    assert enrollment.status == "bounced"
    sup = (await db_session.execute(select(Suppression))).scalars().one()
    assert sup.reason == "hard_bounce"
