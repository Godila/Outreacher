import asyncio
import contextlib

from app.b24.tokenmanager import TokenManager
from app.models import B24State


def session_factory_from(db_session):
    """Фабрика как async_sessionmaker: каждый вызов возвращает свежий async-with контекст."""

    @contextlib.asynccontextmanager
    async def _ctx():
        yield db_session

    return lambda: _ctx()


async def test_concurrent_get_client_single_refresh(db_session, monkeypatch):
    state = {"refresh": "r0", "access": "a0", "calls": 0}

    async def fake_refresh(client_id, client_secret, refresh_token):
        state["calls"] += 1
        await asyncio.sleep(0.05)  # имитация сети: вторая корутина успевает дойти до лока
        if refresh_token != state["refresh"]:
            return {"error": "invalid_grant"}
        state["refresh"], state["access"] = f"r{state['calls']}", f"a{state['calls']}"
        return {
            "access_token": state["access"],
            "refresh_token": state["refresh"],
            "expires_in": 3600,
        }

    tm = TokenManager(session_factory_from(db_session), client_id="ci", client_secret="cs")
    monkeypatch.setattr(tm, "_oauth_refresh", fake_refresh)
    # expires_in=0 -> expires_at в прошлом -> нужен refresh
    await tm.install("portal.example.ru", {"access_token": "a0", "refresh_token": "r0", "expires_in": 0})

    _r1, _r2 = await asyncio.gather(tm._ensure_access(), tm._ensure_access())
    assert state["calls"] == 1  # один refresh под локом

    fresh = await db_session.get(B24State, 1)
    assert fresh.access_token == state["access"]
    assert fresh.refresh_token == state["refresh"]


async def test_fresh_token_skips_refresh(db_session, monkeypatch):
    calls = {"n": 0}

    async def fake_refresh(client_id, client_secret, refresh_token):
        calls["n"] += 1
        return {"access_token": "x", "refresh_token": "y", "expires_in": 3600}

    tm = TokenManager(session_factory_from(db_session), client_id="ci", client_secret="cs")
    monkeypatch.setattr(tm, "_oauth_refresh", fake_refresh)
    await tm.install(
        "portal.example.ru",
        {"access_token": "live", "refresh_token": "r0", "expires_in": 3600},
    )
    _client = await tm.get_client()
    assert calls["n"] == 0


async def test_application_token_kept(db_session):
    tm = TokenManager(session_factory_from(db_session), client_id="ci", client_secret="cs")
    await tm.install(
        "portal.example.ru",
        {"access_token": "a", "refresh_token": "r", "expires_in": 3600, "application_token": "AT1"},
    )
    # повторная установка без application_token не затирает захваченный
    await tm.install(
        "portal.example.ru",
        {"access_token": "a2", "refresh_token": "r2", "expires_in": 3600},
    )
    state = await db_session.get(B24State, 1)
    assert state.application_token == "AT1"
    assert state.access_token == "a2"
