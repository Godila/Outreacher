import contextlib

import httpx
import pytest

from app.config import Settings
from app.engine.unsub import make_token, parse_token
from app.main import create_app
from app.models import B24Job, Campaign, Contact, Enrollment, Suppression


def session_factory_from(db_session):
    @contextlib.asynccontextmanager
    async def _ctx():
        yield db_session

    return lambda: _ctx()


@pytest.fixture
def settings():
    return Settings(app_secret="test-secret", database_url="sqlite+aiosqlite://")


@pytest.fixture
def app(db_session, settings):
    return create_app(session_factory=session_factory_from(db_session), settings=settings)


@pytest.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def test_token_roundtrip_and_tamper():
    token = make_token(7, "user@x.ru", "sec")
    assert parse_token(token, "user@x.ru", "sec") == 7
    assert parse_token(token, "other@x.ru", "sec") is None
    assert parse_token("8.deadbeefdeadbeef", "user@x.ru", "sec") is None
    assert parse_token(token, "user@x.ru", "other-sec") is None


async def _seed(db_session):
    contact = Contact(b24_contact_id=1, email="user@x.ru", name="U")
    db_session.add(contact)
    campaign = Campaign(name="C", slug="c1", status="active")
    db_session.add(campaign)
    await db_session.flush()
    enrollment = Enrollment(campaign_id=campaign.id, contact_id=contact.id, status="active")
    db_session.add(enrollment)
    await db_session.flush()
    return contact, campaign, enrollment


async def test_get_unsub_suppresses_forever(db_session, client):
    contact, campaign, enrollment = await _seed(db_session)
    token = make_token(enrollment.id, contact.email, "test-secret")

    resp = await client.get(f"/unsub/{token}")
    assert resp.status_code == 200
    assert "отписаны" in resp.text

    # идемпотентность: повторный GET не создаёт дубль suppression
    await client.get(f"/unsub/{token}")
    rows = (await db_session.execute(__import__("sqlalchemy").select(Suppression))).scalars().all()
    assert len(rows) == 1 and rows[0].reason == "unsub"

    fresh = await db_session.get(Enrollment, enrollment.id)
    assert fresh.status == "unsubscribed"

    jobs = (await db_session.execute(__import__("sqlalchemy").select(B24Job))).scalars().all()
    assert len(jobs) == 1 and jobs[0].kind == "mark_unsubscribed"


async def test_post_one_click_returns_200_empty(db_session, client):
    contact, campaign, enrollment = await _seed(db_session)
    token = make_token(enrollment.id, contact.email, "test-secret")
    resp = await client.post(f"/unsub/{token}")
    assert resp.status_code == 200
    assert resp.content == b""


async def test_bad_token_404(db_session, client):
    await _seed(db_session)
    resp = await client.get("/unsub/999.nothex")
    assert resp.status_code == 404
