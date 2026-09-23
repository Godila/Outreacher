import contextlib

import httpx
import pytest

from app.b24.tokenmanager import TokenManager
from app.config import Settings
from app.main import create_app
from app.models import B24State


def session_factory_from(db_session):
    @contextlib.asynccontextmanager
    async def _ctx():
        yield db_session

    return lambda: _ctx()


@pytest.fixture
def settings():
    return Settings(b24_webhook_secret="whs-1", app_secret="test-secret")


@pytest.fixture
def app(db_session, settings):
    tm = TokenManager(session_factory_from(db_session), client_id="ci", client_secret="cs")
    return create_app(session_factory=session_factory_from(db_session), token_manager=tm, settings=settings)


@pytest.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_onappinstall_form_urlencoded(db_session, client):
    body = (
        b"event=ONAPPINSTALL"
        b"&auth[access_token]=at1&auth[refresh_token]=rt1&auth[member_id]=m1"
        b"&auth[domain]=testportal.bitrix24.ru&auth[expires_in]=3600"
        b"&auth[application_token]=APT"
    )
    resp = await client.post(
        "/b24/event",
        content=body,
        headers={"content-type": "application/x-www-form-urlencoded", "x-webhook-secret": "whs-1"},
    )
    assert resp.status_code == 200
    state = await db_session.get(B24State, 1)
    assert state.portal_domain == "testportal.bitrix24.ru"
    assert state.access_token == "at1"
    assert state.refresh_token == "rt1"
    assert state.application_token == "APT"


async def test_webhook_dedup(client):
    body = b"event=ONAPPINSTALL&auth[access_token]=at&auth[domain]=d.bitrix24.ru"
    headers = {"content-type": "application/x-www-form-urlencoded", "x-webhook-secret": "whs-1"}
    r1 = await client.post("/b24/event", content=body, headers=headers)
    r2 = await client.post("/b24/event", content=body, headers=headers)
    assert r1.status_code == 200
    assert r2.status_code == 200  # дубль молча проглочен


async def test_rejected_without_secret_and_token(db_session, client):
    # нет ни секрета, ни сохранённого application_token
    body = b"event=ONCRMCONTACTADD&data[FIELDS][ID]=1"
    resp = await client.post(
        "/b24/event",
        content=body,
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 403


async def test_accepted_via_application_token(db_session, client):
    db_session.add(B24State(portal_domain="p.bitrix24.ru", application_token="APT1", access_token="a", refresh_token="r"))
    await db_session.commit()
    body = b"event=ONCRMCONTACTADD&data[FIELDS][ID]=1&auth[application_token]=APT1"
    resp = await client.post(
        "/b24/event",
        content=body,
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 200
