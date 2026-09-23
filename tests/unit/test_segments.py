import datetime as dt

from sqlalchemy import select

from app.models import Campaign, Contact, Enrollment, Segment, Suppression
from app.segments import enroll, preview, segment_conditions


async def _seed(db_session):
    now = dt.datetime(2026, 9, 23)
    contacts = [
        Contact(b24_contact_id=1, email="a@x.ru", name="A", last_deal_at=now - dt.timedelta(days=200),
                deals_count=2, last_deal_amount=5000),
        Contact(b24_contact_id=2, email="b@x.ru", name="B", last_deal_at=now - dt.timedelta(days=10),
                deals_count=1, last_deal_amount=500),
        Contact(b24_contact_id=3, email="c@x.ru", name="C", last_deal_at=now - dt.timedelta(days=400),
                deals_count=5, last_deal_amount=20000, has_active_deal=True),
        Contact(b24_contact_id=4, email="d@x.ru", name="D", last_deal_at=None, deals_count=0),
    ]
    db_session.add_all(contacts)
    db_session.add(Suppression(email="b@x.ru", reason="unsub"))
    await db_session.flush()
    return contacts


async def test_preview_excludes_suppressed(db_session):
    await _seed(db_session)
    rows = await preview(db_session, {"last_deal_days_gte": 100})
    emails = {c.email for c in rows}
    # A (200 дней, чист) и C (400 дней, но активная сделка — в preview остаётся, фильтра нет)
    assert emails == {"a@x.ru", "c@x.ru"}


async def test_enroll_exclusions(db_session):
    contacts = await _seed(db_session)
    segment = Segment(name="Спящие", filters={"last_deal_days_gte": 100})
    db_session.add(segment)
    campaign = Campaign(name="Реактивация", slug="react-1", status="active")
    db_session.add(campaign)
    await db_session.flush()

    created = await enroll(db_session, campaign.id, segment.filters)
    assert created == 1  # только A: B в стоп-листе, C с активной сделкой, D без истории

    # повторный enroll той же кампании не дублирует
    again = await enroll(db_session, campaign.id, segment.filters)
    rows = (await db_session.execute(select(Enrollment))).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "pending"

    # A числится в активной кампании → другая кампания его не берёт
    campaign2 = Campaign(name="Вторая", slug="react-2", status="active")
    db_session.add(campaign2)
    await db_session.flush()
    created2 = await enroll(db_session, campaign2.id, segment.filters)
    assert created2 == 0


def test_segment_conditions_types():
    conds = segment_conditions({"last_deal_days_gte": 180, "deals_count_gte": 1, "last_deal_amount_gte": 1000.5})
    assert len(conds) == 3
    assert segment_conditions({}) == []
