import asyncio
import datetime as dt

import httpx
from sqlalchemy import select

from app.b24.client import Bitrix24Client, Bitrix24Error
from app.config import get_settings
from app.models import B24State, utcnow

OAUTH_URL = "https://oauth.bitrix24.tech/oauth/token/"


class TokenManager:
    """OAuth Б24 с ротацией refresh_token: конкурентные refresh под локом, перечитывание из БД."""

    def __init__(self, session_factory, client_id: str | None = None, client_secret: str | None = None):
        self._session_factory = session_factory
        settings = get_settings()
        self._client_id = client_id or settings.b24_client_id
        self._client_secret = client_secret or settings.b24_client_secret
        self._lock: asyncio.Lock | None = None

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def _load_state(self, session) -> B24State | None:
        return (await session.execute(select(B24State))).scalars().first()

    async def _load_by_domain(self, session, portal_domain: str) -> B24State | None:
        return (
            await session.execute(select(B24State).where(B24State.portal_domain == portal_domain))
        ).scalars().first()

    async def install(self, portal_domain: str, auth: dict) -> None:
        """Сохраняет/обновляет токены из ONAPPINSTALL. application_token захватываем только если пуст."""
        expires_in = int(auth.get("expires_in", 0) or 0)
        async with self._session_factory() as session:
            state = await self._load_by_domain(session, portal_domain)
            if state is None:
                state = B24State(portal_domain=portal_domain)
                session.add(state)
            state.access_token = auth.get("access_token", "")
            state.refresh_token = auth.get("refresh_token", "")
            state.member_id = auth.get("member_id", "")
            if not state.application_token and auth.get("application_token"):
                state.application_token = auth["application_token"]
            state.expires_at = utcnow() + dt.timedelta(seconds=expires_in - 60) if expires_in else None
            await session.commit()

    async def _oauth_refresh(self, client_id: str, client_secret: str, refresh_token: str) -> dict:
        """Переопределяется в тестах. Ротация: каждый вызов выдаёт новый refresh_token."""
        async with httpx.AsyncClient(timeout=30) as http:
            resp = await http.post(
                OAUTH_URL,
                data={
                    "grant_type": "refresh_token",
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "refresh_token": refresh_token,
                },
            )
            return resp.json()

    async def _ensure_access(self) -> tuple[str, str]:
        """Возвращает (portal_domain, access_token); при истечении — refresh под локом."""
        async with self._session_factory() as session:
            state = await self._load_state(session)
            if state is None:
                raise Bitrix24Error("not_installed", "B24 state not found — установите приложение")
            if state.expires_at is None or state.expires_at > utcnow():
                return state.portal_domain, state.access_token
            stale_refresh = state.refresh_token

        async with self._get_lock():
            # перечитали под локом: конкурентный refresh мог уже обновить токены
            async with self._session_factory() as session:
                state = await self._load_state(session)
                if state is None:
                    raise Bitrix24Error("not_installed", "B24 state not found")
                if state.expires_at is not None and state.expires_at > utcnow():
                    return state.portal_domain, state.access_token
                refresh_token = state.refresh_token

            token_resp = await self._oauth_refresh(self._client_id, self._client_secret, refresh_token)
            if (
                token_resp.get("error") == "invalid_grant"
                and refresh_token == stale_refresh
            ):
                # наш refresh устарел ещё до входа в лок — разовый ретрай с перечитанным токеном
                async with self._session_factory() as session:
                    state = await self._load_state(session)
                    if state and state.refresh_token != stale_refresh:
                        token_resp = await self._oauth_refresh(
                            self._client_id, self._client_secret, state.refresh_token
                        )
            if "access_token" not in token_resp:
                raise Bitrix24Error(token_resp.get("error", "oauth_failed"), str(token_resp)[:500])

            async with self._session_factory() as session:
                state = await self._load_state(session)
                assert state is not None
                state.access_token = token_resp["access_token"]
                state.refresh_token = token_resp.get("refresh_token", state.refresh_token)
                expires_in = int(token_resp.get("expires_in", 0) or 0)
                state.expires_at = utcnow() + dt.timedelta(seconds=expires_in - 60) if expires_in else None
                await session.commit()
            return state.portal_domain, state.access_token

    async def _transport(self, method: str, params: dict) -> dict:
        domain, access = await self._ensure_access()
        url = f"https://{domain}/rest/{method}"
        async with httpx.AsyncClient(timeout=60) as http:
            resp = await http.post(url, json={**params, "auth": access})
        if resp.status_code != 200:
            raise Bitrix24Error("invalid_response", f"HTTP {resp.status_code}")
        try:
            return resp.json()
        except ValueError:
            raise Bitrix24Error("invalid_response", resp.text[:200]) from None

    async def get_client(self) -> Bitrix24Client:
        return Bitrix24Client(self._transport)
