import datetime as dt

from sqlalchemy import select

from app.config import Settings
from app.engine.scheduler import next_send_time, tick
from app.models import Campaign, Contact, Enrollment, Message, Sender, SequenceStep, Suppression

MSK = 3


def _settings(**kw) -> Settings:
    defaults = dict(
        tz_offset_hours=MSK, send_hour_from=8, send_hour_to=12,
        app_secret="sec", base_url="https://out.example.ru",
        legal_entity="T", legal_address="A", legal_phone="P",
    )
    defaults.update(kw)
    return Settings(**defaults)


def utc(y, m, d, h, minute=0):
    return dt.datetime(y, m, d, h, minute)


# 2026-09-23 — среда; 2026-09-25 — пятница; 2026-09-28 — понедельник


def test_next_send_delay_over_weekend():
    # пятница 15:00 МСК (12:00 UTC), +2 раб.дня → вторник 15:00 → окно закрыто → среда 08:00
    got = next_send_time(utc(2026, 9, 25, 12), 2, _settings())
    assert got == utc(2026, 9, 30, 5)  # ср 08:00 МСК = 05:00 UTC


def test_next_send_zero_delay_in_window():
    # среда 10:00 МСК (07:00 UTC) — внутри окна, шаг уходит сейчас
    got = next_send_time(utc(2026, 9, 23, 7), 0, _settings())
    assert got == utc(2026, 9, 23, 7)


def test_next_send_before_window():
    # среда 07:30 МСК (04:30 UTC) → сегодня 08:00 МСК
    got = next_send_time(utc(2026, 9, 23, 4, 30), 0, _settings())
    assert got == utc(2026, 9, 23, 5)


def test_next_send_after_window():
    # среда 12:30 МСК (09:30 UTC) → четверг 08:00 МСК
    got = next_send_time(utc(2026, 9, 23, 9, 30), 0, _settings())
    assert got == utc(2026, 9, 24, 5)


def test_next_send_weekend_to_monday():
    # суббота 10:00 МСК (07:00 UTC) → понедельник 08:00 МСК
    got = next_send_time(utc(2026, 9, 26, 7), 0, _settings())
    assert got == utc(2026, 9, 28, 5)


async def _seed(db_session, **kw):
    contact = Contact(b24_contact_id=1, email="cli@x.ru", name="Клиент", **kw.pop("contact_kw", {}))
    campaign = Campaign(name="C", slug="c1", status=kw.pop("campaign_status", "active"), daily_cap=kw.pop("daily_cap", 100))
    db_session.add_all([contact, campaign])
    await db_session.flush()
    db_session.add_all([
        SequenceStep(campaign_id=campaign.id, position=1, delay_days=0, subject_tpl="Привет {{name}}", body_tpl="Тело"),
        SequenceStep(campaign_id=campaign.id, position=2, delay_days=3, subject_tpl="Re", body_tpl="Ещё"),
    ])
    enrollment = Enrollment(
        campaign_id=campaign.id, contact_id=contact.id, status="pending", next_send_at=utc(2026, 9, 23, 7),
    )
    db_session.add(enrollment)
    sender = Sender(
        email="anna@news.example.ru", smtp_host="smtp", smtp_user="u", smtp_pass="p",
        imap_host="imap", imap_user="u", imap_pass="p", daily_limit=50,
    )
    db_session.add(sender)
    await db_session.flush()
    return contact, campaign, enrollment, sender


async def test_tick_creates_message_and_schedules_next(db_session):
    _c, _camp, enrollment, sender = await _seed(db_session)
    stats = await tick(db_session, utc(2026, 9, 23, 7, 30), _settings())  # ср 10:30 МСК, окно
    assert stats.created == 1
    msg = (await db_session.execute(select(Message))).scalars().one()
    assert msg.status == "queued" and msg.step_pos == 1
    assert msg.message_id.startswith("<") and msg.message_id.endswith("@outreacher>")
    assert "Привет Клиент" == msg.subject
    assert sender.sent_today == 1
    assert enrollment.status == "active" and enrollment.current_step == 1
    # следующий шаг: +3 раб.дня от ср 10:30 (чт, пт, пн) → пн 28.09 10:30 МСК = 07:30 UTC
    assert enrollment.next_send_at == utc(2026, 9, 28, 7, 30)

    # идемпотентность: повторный tick не создаёт дубль
    stats2 = await tick(db_session, utc(2026, 9, 23, 7, 31), _settings())
    assert stats2.created == 0
    assert len((await db_session.execute(select(Message))).scalars().all()) == 1


async def test_tick_excludes_suppressed(db_session):
    _c, _camp, enrollment, _s = await _seed(db_session)
    db_session.add(Suppression(email="cli@x.ru", reason="unsub"))
    await db_session.flush()
    stats = await tick(db_session, utc(2026, 9, 23, 7, 30), _settings())
    assert stats.created == 0 and stats.excluded == 1
    assert enrollment.status == "excluded" and enrollment.paused_reason == "suppressed"


async def test_tick_excludes_active_deal(db_session):
    _c, _camp, enrollment, _s = await _seed(db_session, contact_kw={"has_active_deal": True})
    stats = await tick(db_session, utc(2026, 9, 23, 7, 30), _settings())
    assert stats.excluded == 1 and enrollment.status == "excluded"


async def test_tick_defers_paused_campaign(db_session):
    _c, _camp, enrollment, _s = await _seed(db_session, campaign_status="paused")
    stats = await tick(db_session, utc(2026, 9, 23, 7, 30), _settings())
    assert stats.created == 0 and stats.deferred == 1
    assert len((await db_session.execute(select(Message))).scalars().all()) == 0


async def test_tick_daily_cap(db_session):
    _c, camp, enrollment, _s = await _seed(db_session, daily_cap=1)
    stats = await tick(db_session, utc(2026, 9, 23, 7, 30), _settings())
    assert stats.created == 1
    # письмо ещё не отправлено (queued), лимит считает только sent — кап не сработает; отправим вручную
    msg = (await db_session.execute(select(Message))).scalars().one()
    msg.status = "sent"
    msg.sent_at = utc(2026, 9, 23, 7, 30)
    await db_session.commit()
    # второй контакт в той же кампании
    contact2 = Contact(b24_contact_id=2, email="c2@x.ru", name="Второй")
    db_session.add(contact2)
    await db_session.flush()
    db_session.add(Enrollment(campaign_id=camp.id, contact_id=contact2.id, status="pending", next_send_at=utc(2026, 9, 23, 7, 40)))
    await db_session.flush()
    stats2 = await tick(db_session, utc(2026, 9, 23, 7, 40), _settings())
    assert stats2.created == 0 and stats2.deferred == 1


async def test_tick_sender_limit_exhausted(db_session):
    _c, _camp, enrollment, sender = await _seed(db_session)
    sender.sent_today = 50
    sender.sent_date = "2026-09-23"
    await db_session.flush()
    stats = await tick(db_session, utc(2026, 9, 23, 7, 30), _settings())
    assert stats.created == 0 and stats.deferred == 1


async def test_tick_resumes_ooo(db_session):
    _c, _camp, enrollment, _s = await _seed(db_session)
    enrollment.status = "paused_ooo"
    enrollment.next_send_at = utc(2026, 9, 20, 5)
    await db_session.flush()
    stats = await tick(db_session, utc(2026, 9, 23, 7, 30), _settings())
    assert stats.resumed_ooo == 1
    # и сразу создаёт письмо (статус снова active, время пришло)
    assert stats.created == 1
