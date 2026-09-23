from sqlalchemy import select

from app.models import Contact, Suppression


async def test_contact_and_suppression_roundtrip(db_session):
    contact = Contact(b24_contact_id=1, email="a@b.ru", name="A")
    db_session.add(contact)
    await db_session.flush()

    db_session.add(Suppression(email="a@b.ru", reason="unsub"))
    await db_session.flush()

    got = (await db_session.execute(select(Contact))).scalars().first()
    assert got.id is not None
    assert got.has_active_deal is False
    assert got.deals_count == 0

    suppressed = (await db_session.execute(select(Suppression))).scalars().first()
    assert suppressed.reason == "unsub"
