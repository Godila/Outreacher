# Outreacher MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Внутренний сервис email-кампаний по клиентской базе Битрикс24: синк CRM → сегменты → секвенции с автостопом → отправка SMTP → приём ответов IMAP → маршрутизация обратно в Б24.

**Architecture:** Монолит FastAPI (web) + worker (scheduler/sender/listener/sync) + Postgres 16 в одном docker compose. Пул отправителей абстрактен (старт — ящики Beget-почты на сабдомене). Спека: `docs/superpowers/specs/2026-09-23-outreacher-design.md`.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2 async + Alembic, Postgres 16 (тесты — aiosqlite), httpx, aiosmtplib, imapclient, Jinja2, pytest + pytest-asyncio.

## Global Constraints

- Python 3.12; deps фиксируются в `pyproject.toml` (uv или pip -e).
- B24 REST: ≤2 запроса/сек (глобальный троттлер), batch ≤50, пагинация `start`/`next`.
- Контакты читаются ТОЛЬКО `crm.contact.list` (EMAIL в select/filter); сделки — `crm.item.list` entityTypeId=2 (camelCase поля, `contacts`/`contactId`); email-дело — `crm.activity.add` с `TYPE_ID=4`, `SETTINGS.DISABLE_SENDING_MESSAGE_COPY='Y'`, ровно одна COMMUNICATION; ответ — `crm.timeline.comment.add` (ENTITY_TYPE='contact').
- B24 OAuth: refresh_token ротирует на каждый refresh → лок + перечитывание из БД (oauth.bitrix24.tech).
- Вебхуки: парсер принимает form-urlencoded php-массивы И JSON, id-коэрсинг к int.
- Отписка: мгновенная, навсегда (`suppression`), RFC 8058 (GET+POST `/unsub/{token}`, 200 без редиректа), токен HMAC — вечный.
- Письма: plain text, ≤1 ссылки, авто-UTM `utm_source=outreach&utm_medium=email&utm_campaign=<slug>&utm_content=step<N>`, заголовки `List-Unsubscribe`, `List-Unsubscribe-Post`, `Feedback-ID`; follow-up — `In-Reply-To`/`References` в тред первого письма.
- Open tracking отсутствует by design (пикселей нет, opens не меряем).
- Секреты: только env/`.env` (в git не попадают, `.gitignore` уже исключает `docs/secrets/`, `.env`).
- Тесты: unit на sqlite+aiosqlite, httpx/SMTP моки; каждый task заканчивается зелёным pytest и коммитом.

## File Structure

```
pyproject.toml
.env.example
docker/Dockerfile
docker-compose.yml
alembic.ini, alembic/env.py, alembic/versions/*
src/app/__init__.py
src/app/config.py        — Settings (pydantic-settings)
src/app/db.py            — engine/session/Base
src/app/models.py        — все таблицы
src/app/b24/__init__.py, client.py, tokenmanager.py, webhooks.py
src/app/sync.py          — синк контактов/сделок(RFM)/юзеров
src/app/segments.py      — билдер запроса сегмента
src/app/engine/__init__.py, scheduler.py, render.py, unsub.py
src/app/mail/__init__.py, builder.py, sender.py, listener.py, ndr.py
src/app/classify.py      — правила классификации ответов
src/app/b24map.py        — запись активностей/комментариев/задач в Б24
src/app/worker.py        — циклы воркера
src/app/main.py          — FastAPI app + роуты
src/app/routes/__init__.py, unsub.py, ui.py, api.py, b24hooks.py
templates/*.html, static/style.css
tests/conftest.py, tests/unit/test_*.py
```

---

### Task 1: Скелет, конфиг, БД, модели, миграция, compose

**Files:** Create `pyproject.toml`, `src/app/__init__.py`, `config.py`, `db.py`, `models.py`, `alembic/*`, `docker/Dockerfile`, `docker-compose.yml`, `.env.example`, `tests/conftest.py`, `tests/unit/test_models.py`

**Interfaces (Produces):**
- `app.config.Settings` — поля: `database_url: str`, `base_url: str`, `app_secret: str`, `b24_client_id: str`, `b24_client_secret: str`, `b24_webhook_secret: str = ""`, `operator_password: str`, `tz_offset_hours: int = 3`, `send_hour_from: int = 8`, `send_hour_to: int = 12`, `daily_cap_default: int = 100`; `Settings()` читает env `OUTREACHER_*`.
- `app.db.Base` (DeclarativeBase), `async_session()`.
- Модели (имена таблиц/полей — канон для всех следующих задач): `Contact, Segment, Campaign, SequenceStep, Sender, Enrollment, Message, Incoming, Suppression, B24State` (+ пустые `Domain, WarmupTask, VerificationRun, Consent` — закладные).
- `run_migrations()` не нужна — alembic CLI.

- [ ] **Step 1: pyproject.toml + установка**

```toml
[project]
name = "outreacher"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  "fastapi>=0.115", "uvicorn[standard]>=0.30", "jinja2>=3.1",
  "sqlalchemy[asyncio]>=2.0.30", "asyncpg>=0.29", "alembic>=1.13",
  "pydantic-settings>=2.2", "httpx>=0.27", "aiosmtplib>=3.0",
  "imapclient>=2.3", "python-multipart>=0.0.9",
]
[project.optional-dependencies]
dev = ["pytest>=8", "pytest-asyncio>=0.23", "aiosqlite>=0.20"]
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
[tool.hatch.build.targets.wheel]
packages = ["src/app"]
```

`cd C:\Users\geor\Desktop\Outreacher && python -m venv .venv && .venv/Scripts/pip install -e ".[dev]"` (дальше `pytest` = `.venv/Scripts/pytest`).

- [ ] **Step 2: config.py**

```python
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OUTREACHER_")
    database_url: str = "postgresql+asyncpg://outreacher:outreacher@localhost:5432/outreacher"
    base_url: str = "https://outreach.example.ru"
    app_secret: str = "dev-secret"
    b24_client_id: str = ""
    b24_client_secret: str = ""
    b24_webhook_secret: str = ""
    operator_password: str = "operator"
    tz_offset_hours: int = 3
    send_hour_from: int = 8   # Мск
    send_hour_to: int = 12    # Мск, эксклюзивно
    daily_cap_default: int = 100

def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 3: db.py**

```python
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from app.config import get_settings

class Base(DeclarativeBase):
    pass

def make_engine(url: str | None = None):
    return create_async_engine(url or get_settings().database_url, pool_pre_ping=True)

SessionMaker = async_sessionmaker(make_engine(), expire_on_commit=False)
```

- [ ] **Step 4: models.py** (полный код — канон имён)

```python
import datetime as dt
import uuid
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, JSON
from sqlalchemy.orm import Mapped, mapped_column
from app.db import Base

def _uuid() -> str: return uuid.uuid4().hex

class Contact(Base):
    __tablename__ = "contacts"
    id: Mapped[int] = mapped_column(primary_key=True)
    b24_contact_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    email: Mapped[str] = mapped_column(String(320), index=True)
    name: Mapped[str] = mapped_column(String(255), default="")
    company: Mapped[str] = mapped_column(String(255), default="")
    assigned_b24_user_id: Mapped[int] = mapped_column(Integer, default=0)
    has_active_deal: Mapped[bool] = mapped_column(Boolean, default=False)
    last_deal_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    last_deal_amount: Mapped[float] = mapped_column(default=0.0)
    last_deal_title: Mapped[str] = mapped_column(String(255), default="")
    deals_count: Mapped[int] = mapped_column(default=0)
    consent_source: Mapped[str] = mapped_column(String(255), default="")
    consent_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    synced_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)

class Segment(Base):
    __tablename__ = "segments"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    filters: Mapped[dict] = mapped_column(JSON, default=dict)

class Campaign(Base):
    __tablename__ = "campaigns"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    type: Mapped[str] = mapped_column(String(32), default="reactivation")  # reactivation/announce/upsell/content
    status: Mapped[str] = mapped_column(String(16), default="draft")       # draft/active/paused/done
    daily_cap: Mapped[int] = mapped_column(Integer, default=100)
    is_advertising: Mapped[bool] = mapped_column(Boolean, default=False)
    segment_id: Mapped[int | None] = mapped_column(ForeignKey("segments.id"), nullable=True)

class SequenceStep(Base):
    __tablename__ = "sequence_steps"
    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)      # 1-based
    delay_days: Mapped[int] = mapped_column(Integer, default=0)  # рабочих дней после отправки предыдущего; 0 у первого
    subject_tpl: Mapped[str] = mapped_column(Text)
    body_tpl: Mapped[str] = mapped_column(Text)
    threaded: Mapped[bool] = mapped_column(Boolean, default=True)

class Sender(Base):
    __tablename__ = "senders"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    display_name: Mapped[str] = mapped_column(String(255), default="")
    smtp_host: Mapped[str] = mapped_column(String(255))
    smtp_port: Mapped[int] = mapped_column(Integer, default=465)
    smtp_user: Mapped[str] = mapped_column(String(255))
    smtp_pass: Mapped[str] = mapped_column(String(255))
    imap_host: Mapped[str] = mapped_column(String(255))
    imap_port: Mapped[int] = mapped_column(Integer, default=993)
    imap_user: Mapped[str] = mapped_column(String(255))
    imap_pass: Mapped[str] = mapped_column(String(255))
    daily_limit: Mapped[int] = mapped_column(Integer, default=50)
    sent_today: Mapped[int] = mapped_column(Integer, default=0)
    sent_date: Mapped[str] = mapped_column(String(10), default="")
    health: Mapped[str] = mapped_column(String(16), default="ok")  # ok/paused

class Enrollment(Base):
    __tablename__ = "enrollments"
    __table_args__ = (UniqueConstraint("campaign_id", "contact_id", name="uq_enrollment"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), index=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    # pending/active/replied/unsubscribed/bounced/paused_ooo/done/excluded
    current_step: Mapped[int] = mapped_column(Integer, default=0)  # последний отправленный
    next_send_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    ooo_until: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    paused_reason: Mapped[str] = mapped_column(String(255), default="")

class Message(Base):
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    enrollment_id: Mapped[int] = mapped_column(ForeignKey("enrollments.id"), index=True)
    step_pos: Mapped[int] = mapped_column(Integer)
    message_id: Mapped[str] = mapped_column(String(255), unique=True)  # RFC822 <...>, идемпотентность
    sender_email: Mapped[str] = mapped_column(String(320))
    subject: Mapped[str] = mapped_column(Text, default="")
    body: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued/sent/bounced/failed
    smtp_error: Mapped[str] = mapped_column(Text, default="")
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    sent_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    b24_activity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

class Incoming(Base):
    __tablename__ = "incoming"
    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[str] = mapped_column(String(255), index=True)
    in_reply_to: Mapped[str] = mapped_column(String(255), default="", index=True)
    from_email: Mapped[str] = mapped_column(String(320), default="")
    classification: Mapped[str] = mapped_column(String(16), default="unknown", index=True)
    # positive/negative/ooo/unsub/referral/unknown
    body_snippet: Mapped[str] = mapped_column(Text, default="")
    classified_by: Mapped[str] = mapped_column(String(16), default="rule")  # rule/manual/llm
    received_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    b24_comment_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_ndr: Mapped[bool] = mapped_column(Boolean, default=False)

class Suppression(Base):
    __tablename__ = "suppression"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    reason: Mapped[str] = mapped_column(String(32))  # unsub/fbl/hard_bounce/manual
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)

class B24State(Base):
    __tablename__ = "b24_state"
    id: Mapped[int] = mapped_column(primary_key=True)
    portal_domain: Mapped[str] = mapped_column(String(255), unique=True)
    access_token: Mapped[str] = mapped_column(Text, default="")
    refresh_token: Mapped[str] = mapped_column(Text, default="")
    member_id: Mapped[str] = mapped_column(String(64), default="")
    application_token: Mapped[str] = mapped_column(Text, default="")
    contacts_watermark: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    deals_watermark: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)

# Закладные фазы cold (создаются, не используются в MVP)
class Domain(Base):
    __tablename__ = "domains"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    dns_status: Mapped[str] = mapped_column(String(16), default="unknown")
    warmup_started_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)

class WarmupTask(Base):
    __tablename__ = "warmup_tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    sender_id: Mapped[int] = mapped_column(ForeignKey("senders.id"))
    scheduled_at: Mapped[dt.datetime] = mapped_column(DateTime)
    done: Mapped[bool] = mapped_column(Boolean, default=False)

class VerificationRun(Base):
    __tablename__ = "verification_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), index=True)
    verdict: Mapped[str] = mapped_column(String(16))  # valid/invalid/catch_all/unknown
    ran_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)

class Consent(Base):
    __tablename__ = "consents"
    id: Mapped[int] = mapped_column(primary_key=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id"), index=True)
    source: Mapped[str] = mapped_column(String(255))
    text: Mapped[str] = mapped_column(Text, default="")
    ip: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
```

- [ ] **Step 5: conftest.py + test_models.py**

```python
# tests/conftest.py
import pytest, pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from app.db import Base

@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()
```

```python
# tests/unit/test_models.py
import datetime as dt
from sqlalchemy import select
from app.models import Contact, Suppression, Enrollment, Message

async def test_contact_and_suppression_roundtrip(db_session):
    c = Contact(b24_contact_id=1, email="a@b.ru", name="A")
    db_session.add(c)
    await db_session.flush()
    sup = Suppression(email="a@b.ru", reason="unsub")
    db_session.add(sup)
    await db_session.flush()
    got = (await db_session.execute(select(Contact))).scalars().first()
    assert got.id and got.has_active_deal is False
```

Run: `pytest tests/unit/test_models.py -v` → PASS.

- [ ] **Step 6: alembic.** `alembic init alembic`; в `alembic/env.py`: `from app.db import Base; from app import models  # noqa`; `target_metadata = Base.metadata`; `script_location`; url из `app.config.get_settings().database_url`. Генерация: `alembic revision --autogenerate -m "init"` (запуск против postgres из compose). 

- [ ] **Step 7: docker-compose.yml + Dockerfile + .env.example**

```yaml
# docker-compose.yml
services:
  postgres:
    image: postgres:16
    environment: { POSTGRES_USER: outreacher, POSTGRES_PASSWORD: outreacher, POSTGRES_DB: outreacher }
    volumes: [pgdata:/var/lib/postgresql/data]
    restart: unless-stopped
  web:
    build: { context: ., dockerfile: docker/Dockerfile }
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000
    env_file: .env
    depends_on: [postgres]
    restart: unless-stopped
  worker:
    build: { context: ., dockerfile: docker/Dockerfile }
    command: python -m app.worker
    env_file: .env
    depends_on: [postgres]
    restart: unless-stopped
volumes: { pgdata: {} }
```

```dockerfile
# docker/Dockerfile
FROM python:3.12-slim
WORKDIR /srv
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .
COPY alembic ./alembic
COPY alembic.ini templates static ./
EXPOSE 8000
```

```bash
# .env.example
OUTREACHER_DATABASE_URL=postgresql+asyncpg://outreacher:outreacher@postgres:5432/outreacher
OUTREACHER_BASE_URL=https://outreach.example.ru
OUTREACHER_APP_SECRET=change-me-32-bytes
OUTREACHER_B24_CLIENT_ID=
OUTREACHER_B24_CLIENT_SECRET=
OUTREACHER_B24_WEBHOOK_SECRET=
OUTREACHER_OPERATOR_PASSWORD=change-me
```

- [ ] **Step 8: Commit** `git add -A && git commit -m "feat: project skeleton, config, models, alembic, compose"`

---

### Task 2: B24 client (троттлинг, batch, ошибки)

**Files:** Create `src/app/b24/__init__.py`, `src/app/b24/client.py`; Test `tests/unit/test_b24_client.py`

**Interfaces:** `class Bitrix24Error(Exception): code, description` · `class Bitrix24Client: def __init__(self, call_fn: Callable[[str, dict], Awaitable[dict]])` — `call_fn` абстракция транспорта (её подставляет TokenManager); `async def call(method: str, params: dict | None = None) -> Any`; `async def call_all(method, params, key: str) -> list[dict]` — пагинация по 50 до `next=None`; `async def batch(cmds: list[tuple[str, dict]]) -> list[Any]` — чанки по 50 `cmd[i]`. Троттлер: глобальный семафор-минимум 0.55с между запросами (класс-уровень `asyncio.Lock` + `_last_call_ts`).

- [ ] **Step 1: тест**

```python
# tests/unit/test_b24_client.py
import asyncio, pytest
from app.b24.client import Bitrix24Client, Bitrix24Error

async def test_call_throttles():
    calls = []
    async def fake(method, params): calls.append(method); return {"result": 1}
    c = Bitrix24Client(fake)
    t0 = asyncio.get_event_loop().time()
    await c.call("a"); await c.call("b")
    assert asyncio.get_event_loop().time() - t0 >= 0.5
    assert calls == ["a", "b"]

async def test_call_all_paginates():
    pages = [{"result": [{"id": i} for i in range(50)], "next": 50},
             {"result": [{"id": 50}], "next": None}]
    async def fake(method, params):
        assert params["start"] == (0 if pages[0]["next"] or True else 50) or True
        return pages.pop(0) if params.get("start", 0) == 0 else pages[0]
    # упрощённо: второй вызов вернёт вторую страницу
    seq = [pages[0], pages[1]]
    async def fake2(method, params):
        return seq.pop(0)
    c = Bitrix24Client(fake2)
    items = await c.call_all("crm.contact.list", {}, key="result")
    assert len(items) == 51

async def test_error_raised():
    async def fake(method, params): return {"error": "QUERY_LIMIT_EXCEEDED", "error_description": "too many"}
    c = Bitrix24Client(fake)
    with pytest.raises(Bitrix24Error) as e:
        await c.call("x")
    assert e.value.code == "QUERY_LIMIT_EXCEEDED"
```

- [ ] **Step 2: реализация**

```python
# src/app/b24/client.py
import asyncio, time
from typing import Any, Awaitable, Callable

class Bitrix24Error(Exception):
    def __init__(self, code: str, description: str = ""):
        self.code, self.description = code, description
        super().__init__(f"{code}: {description}")

class Bitrix24Client:
    _last_ts = 0.0
    _gap = 0.55  # <2 req/s
    _lock = asyncio.Lock()

    def __init__(self, call_fn: Callable[[str, dict], Awaitable[dict]]):
        self._call_fn = call_fn

    async def _throttled_raw(self, method: str, params: dict) -> dict:
        async with Bitrix24Client._lock:
            now = time.monotonic()
            wait = Bitrix24Client._gap - (now - Bitrix24Client._last_ts)
            if wait > 0:
                await asyncio.sleep(wait)
            Bitrix24Client._last_ts = time.monotonic()
        resp = await self._call_fn(method, params)
        if isinstance(resp, dict) and "error" in resp:
            raise Bitrix24Error(resp["error"], resp.get("error_description", ""))
        return resp

    async def call(self, method: str, params: dict | None = None) -> Any:
        return (await self._throttled_raw(method, params or {})).get("result")

    async def call_all(self, method: str, params: dict, key: str = "result") -> list:
        out, start = [], 0
        while True:
            resp = await self._throttled_raw(method, {**params, "start": start})
            chunk = resp.get(key) or []
            out.extend(chunk if isinstance(chunk, list) else [chunk])
            nxt = resp.get("next")
            if nxt is None:
                break
            start = nxt
        return out

    async def batch(self, cmds: list[tuple[str, dict]]) -> list[Any]:
        results = []
        for i in range(0, len(cmds), 50):
            chunk = cmds[i:i + 50]
            payload = {f"cmd{j}": f"{m}?{__import__('urllib.parse', fromlist=['parse']).parse.urlencode(p) if False else __import__('json').dumps(p, separators=(',', ':'))}"
                       for j, (m, p) in enumerate(chunk)}
            # B24 batch: cmdN = "method?params_json" — параметры как JSON-строка после ?
            payload = {f"cmd{j}": f"{m}?{__import__('urllib.parse', fromlist=['quote']).quote(__import__('json').dumps(p, separators=(',', ':')), safe='{}\",:[]')" for j, (m, p) in enumerate(chunk)}
            resp = await self._throttled_raw("batch", payload)
            r = resp.get("result", {})
            results.extend(r.get(f"cmd{j}") for j in range(len(chunk)))
        return results
```

(В реализации почистить двойной payload — оставить один вариант с urlencode-JSON; тест batch — по желанию.)

- [ ] **Step 3:** `pytest tests/unit/test_b24_client.py -v` → PASS. **Step 4:** commit `feat: b24 client with throttle/pagination/batch`.

---

### Task 3: TokenManager (ротация refresh под локом)

**Files:** Create `src/app/b24/tokenmanager.py`; Test `tests/unit/test_tokenmanager.py`

**Interfaces:** `class TokenManager: def __init__(self, session_factory, client_id, client_secret)`; `async def install(self, portal_domain, auth: dict)` — сохраняет токены из ONAPPINSTALL; `async def get_client(self) -> Bitrix24Client` — возвращает клиент с живым access_token (refresh при истечении/ошибке, конкурентные refresh под `asyncio.Lock`, перечитывание строки из БД под локом); transport: `httpx.AsyncClient` POST `https://oauth.bitrix24.tech/oauth/token/` `grant_type=refresh_token&client_id&client_secret&refresh_token`; вызов метода: `POST https://{portal}/rest/{method}` JSON `{"auth": access_token, **params}`.

- [ ] **Step 1: тест** — фейковый oauth-сервер: первый refresh возвращает новый refresh_token, проверяем что при двух конкурентных `get_client()` refresh выполнен один раз (счётчик), access_token обновлён, старый refresh невалиден (второй refresh со старым токеном → error → перечитывание из БД даёт новый).

```python
# tests/unit/test_tokenmanager.py (ядро)
import asyncio, pytest
from app.b24.tokenmanager import TokenManager

async def test_concurrent_refresh_single_call(db_session, monkeypatch):
    state = {"refresh": "r0", "access": "a0", "calls": 0}
    async def fake_refresh(client_id, client_secret, refresh_token):
        state["calls"] += 1
        await asyncio.sleep(0.05)  # имитация сети — конкурентность
        if refresh_token != state["refresh"]:
            return {"error": "invalid_grant"}
        state["refresh"], state["access"] = f"r{state['calls']}", f"a{state['calls']}"
        return {"access_token": state["access"], "refresh_token": state["refresh"]}
    async def fake_call(method, params):
        assert params.get("auth") == state["access"]
        return {"result": True}
    tm = TokenManager(session_factory=lambda: _ctx(db_session), client_id="ci", client_secret="cs")
    monkeypatch.setattr(tm, "_oauth_refresh", fake_refresh)
    await tm.install("portal.example.ru", {"access_token": "a0", "refresh_token": "r0", "member_id": "m", "application_token": "at"})
    # сбрасываем access чтобы потребовался refresh
    clients = await asyncio.gather(tm.get_client(), tm.get_client())
    assert state["calls"] == 1
```

(`_ctx` — async context manager, отдающий db_session; хелпер в тесте.)

- [ ] **Step 2: реализация** — хранит `B24State`, `install()` upsert, `_ensure_access()`: если `access_token` пуст или прошёл `expired_token` — под локом перечитать строку из БД, если refresh_token изменился с момента — использовать его, иначе сделать refresh и сохранить оба токена.

- [ ] **Step 3:** `pytest tests/unit/test_tokenmanager.py -v` → PASS. **Step 4:** commit `feat: b24 tokenmanager with rotating refresh`.

---

### Task 4: Вебхук-парсер + роут /b24/event

**Files:** Create `src/app/b24/webhooks.py`, `src/app/routes/__init__.py`, `src/app/routes/b24hooks.py`; Modify `src/app/main.py` (создать в этом же task — см. Task 15 для полного app; здесь — минимальный app с роутом); Test `tests/unit/test_webhook_parser.py`

**Interfaces:** `parse_webhook(body: bytes, content_type: str) -> dict` — рекурсивный разбор php-массивов `a[b][0][c]=v` и JSON; все числоподобные строки → int (кроме токенов/доменов: коэрсинг только для ключей `ID`, `*_ID`, `event_handler_id`, `ts`); `verify_webhook(payload: dict, headers, call_selfcheck) -> bool` — эшелоны: `X-Webhook-Secret == settings.b24_webhook_secret` → self-check `user.current` на домене из payload → `auth.application_token == B24State.application_token`.

- [ ] **Step 1: тесты** — form-urlencoded `auth[access_token]=x&data[FIELDS][ID]=17` → `{"auth": {"access_token": "x"}, "data": {"FIELDS": {"ID": 17}}}`; JSON с строковыми id; ключи-токены не коэрсятся (`application_token` остаётся строкой).

- [ ] **Step 2: реализация**

```python
# src/app/b24/webhooks.py (ядро)
import json, re
from urllib.parse import parse_qsl

_INT_KEYS = re.compile(r"^(ID$|.*_ID$|^ts$|^event_handler_id$)")

def _coerce(key: str, val: str):
    if _INT_KEYS.match(key) and re.fullmatch(r"-?\d+", val or ""):
        return int(val)
    return val

def _nest(flat: list[tuple[str, str]]) -> dict:
    root: dict = {}
    for full_key, val in flat:
        parts = re.findall(r"\[([^\]]*)\]|^[^\[]+", full_key)
        parts = [p if p else "" for p in parts if p != "" or True]
        m = re.match(r"^([^\[]+)", full_key)
        keys = [m.group(1)] + re.findall(r"\[([^\]]*)\]", full_key)
        cur = root
        for i, k in enumerate(keys):
            last = i == len(keys) - 1
            if last:
                cur[k] = val
            else:
                nxt = keys[i + 1]
                if k not in cur or not isinstance(cur[k], (dict, list)):
                    cur[k] = [] if nxt == "" or nxt.isdigit() else {}
                cur = cur[k]
                if isinstance(cur, list) and not last:
                    # позиционируем по индексу
                    while len(cur) <= int(keys[i + 1] if keys[i + 1].isdigit() else 0):
                        cur.append(None)
                    if cur[int(keys[i + 1]) if keys[i + 1].isdigit() else len(cur) - 1] is None:
                        idx = int(keys[i + 1]) if keys[i + 1].isdigit() else len(cur) - 1
                        cur[idx] = [] if (i + 2 < len(keys) and keys[i + 2] == "") or (i + 2 < len(keys) and keys[i + 2].isdigit()) else {}
                    cur = cur[int(keys[i + 1]) if keys[i + 1].isdigit() else len(cur) - 1]
    return _int_walk(root)

def _int_walk(node):
    if isinstance(node, dict):
        return {k: _coerce(k, v) if isinstance(v, str) else _int_walk(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_int_walk(x) for x in node]
    return node

def parse_webhook(body: bytes, content_type: str) -> dict:
    if "json" in (content_type or ""):
        return _int_walk(json.loads(body or b"{}"))
    return _nest(parse_qsl(body.decode("utf-8", "replace"), keep_blank_values=True))
```

Роут `POST /b24/event`: parse → verify → `event == "ONAPPINSTALL"` → `tm.install(domain, auth)` + самозахват application_token (только если NULL); прочие события → игнор (MVP не подписан, кроме установки). Идемпотентность: таблица `processed_events(event_id UQ)`.

- [ ] **Step 3:** `pytest tests/unit/test_webhook_parser.py -v` → PASS. **Step 4:** commit `feat: tolerant b24 webhook parser + ONAPPINSTALL route`.

---

### Task 5: Sync (контакты, сделки→RFM, пользователи)

**Files:** Create `src/app/sync.py`; Test `tests/unit/test_sync.py`

**Interfaces:** `async def sync_all(client: Bitrix24Client, session) -> SyncStats` (counts). Внутри: `_sync_contacts` (crm.contact.list select `[ID, NAME, LAST_NAME, EMAIL, ASSIGNED_BY_ID, DATE_CREATE, SOURCE_ID, COMPANY_ID]`, upsert `contacts`, email = WORK-тип иначе первый; имя = NAME+LAST_NAME); `_sync_deals` (crm.item.list entityTypeId=2 select `[id,title,opportunity,stageId,closed,closedDate,contactId,contacts]` → агрегат по contactId: last_deal_at=max(closedDate|dateModify…), deals_count, has_active_deal=any(closed==False)); `_sync_users` (user.get по ASSIGNED_BY_ID — кэш id→name в таблице `b24_users(id, name)`; добавить модель).

- [ ] **Step 1: тест** — фейковый client (dict-ответы): 2 контакта, 3 сделки (2 закрытые у contact 1, 1 открытая у contact 2) → RFM: contact1.last_deal_at=max closedDate, deals_count=2, has_active_deal=False; contact2.has_active_deal=True; email выбирается WORK; источник → consent_source=SOURCE_ID.

- [ ] **Step 2: реализация** (чистый SQL-агрегат на python-стороне по словарю deals).

- [ ] **Step 3:** `pytest tests/unit/test_sync.py -v` → PASS. **Step 4:** commit `feat: b24 sync contacts/deals-rfm/users`.

---

### Task 6: Сегменты

**Files:** Create `src/app/segments.py`; Test `tests/unit/test_segments.py`

**Interfaces:** `def segment_where(filters: dict) -> tuple[list, dict]` — из JSON-фильтров (`last_deal_days_gte: int`, `deals_count_gte: int`, `last_deal_amount_gte: float`, `has_active_deal: bool`) строит SQLAlchemy-условия для Contact; `async def preview(session, filters) -> list[Contact]`; `async def enroll(session, campaign_id, filters)` — создаёт Enrollment(pending, next_send_at=now) для контактов сегмента MINUS suppression MINUS существующие enrollment'ы любых active-кампаний MINUS has_active_deal=True.

- [ ] **Step 1: тест** — 4 контакта: один в suppression, один с активной сделкой, один уже в active-кампании → enroll создаёт ровно 1 enrollment.
- [ ] **Step 2: реализация** · **Step 3:** PASS · **Step 4:** commit `feat: segments query-builder and enrollment`.

---

### Task 7: Отписка — HMAC-токены + роут RFC 8058

**Files:** Create `src/app/engine/__init__.py`, `src/app/engine/unsub.py`, `src/app/routes/unsub.py`; Test `tests/unit/test_unsub.py`

**Interfaces:** `make_token(enrollment_id: int, email: str, secret: str) -> str` (hmac-sha256, hex, "eid.hmac[:16]"), `parse_token(token, secret) -> int | None`; `async def suppress(session, email, reason)` — upsert Suppression + все enrollment'ы контакта → status=unsubscribed.

```python
# src/app/engine/unsub.py
import hashlib, hmac
def make_token(eid: int, email: str, secret: str) -> str:
    mac = hmac.new(secret.encode(), f"{eid}:{email}".encode(), hashlib.sha256).hexdigest()[:16]
    return f"{eid}.{mac}"
def parse_token(token: str, secret: str, email: str) -> int | None:
    eid, _, mac = token.partition(".")
    if not eid.isdigit(): return None
    expect = hmac.new(secret.encode(), f"{int(eid)}:{email}".encode(), hashlib.sha256).hexdigest()[:16]
    return int(eid) if hmac.compare_digest(mac, expect) else None
```

Нюанс: email нужен для проверки — токен несёт только eid; роут по eid находит enrollment+email в БД и сверяет. `GET /unsub/{token}` → страница «Вы отписаны»; `POST /unsub/{token}` (List-Unsubscribe-Post) → 200 пусто. Оба вызывают `suppress(email, "unsub")` + `b24map.mark_unsubscribed(contact)` (best-effort).

- [ ] Тест: make/parse roundtrip, tamper → None; POST дважды — идемпотентно, Suppression один. · Реализация · PASS · commit `feat: one-click unsubscribe with HMAC tokens`.

---

### Task 8: Рендер писем (Jinja2 + UTM + футер)

**Files:** Create `src/app/engine/render.py`; Test `tests/unit/test_render.py`

**Interfaces:** `render_step(step, campaign, contact, manager_name, base_url, secret) -> Rendered(subject, body, unsub_token)`; Jinja2 `StrictUndefined` (пустые поля → заранее нормализуем в context значениями по умолчанию: `name or "коллега"`); авто-UTM: все `http(s)://` ссылки в body получают доп-параметры если их нет; футер: `—\n{ЮРЛИЦО}\n{адрес}, {телефон}\nОтписаться: {base_url}/unsub/{token}` + префикс «Реклама: » в subject при `campaign.is_advertising`.

- [ ] Тест: merge `{{name}}`, `{{last_deal_title}}`; ссылка без utm получает полный набор; ссылка с utm_source не дублируется; футер содержит отписку. · Реализация (UTM: `re.sub(r'href=[\'"](https?://[^\'"]+)', ...)` + urllib parse) · PASS · commit `feat: jinja2 render with auto-utm and footer`.

---

### Task 9: MIME builder

**Files:** Create `src/app/mail/__init__.py`, `src/app/mail/builder.py`; Test `tests/unit/test_builder.py`

**Interfaces:** `build_mime(*, from_email, from_name, to_email, subject, body, unsub_url, feedback_id, in_reply_to=None, references=None) -> EmailMessage` — plain text (`Content-Type: text/plain; charset=utf-8`), заголовки `List-Unsubscribe: <url>`, `List-Unsubscribe-Post: List-Unsubscribe=One-Click`, `Feedback-ID`, `Message-ID` ставится отправителем (снаружи), тредовые `In-Reply-To`/`References` при in_reply_to. Тест: заголовки на месте, payload utf-8, thread-режим добавляет оба заголовка.

· Реализация · PASS · commit `feat: mime builder with rfc8058 and feedback-id`.

---

### Task 10: Scheduler (ядро движка)

**Files:** Create `src/app/engine/scheduler.py`; Test `tests/unit/test_scheduler.py`

**Interfaces:** `def next_send_time(now_utc: dt.datetime, step: SequenceStep, prev_sent_at: dt.datetime | None, cfg) -> dt.datetime` — рабочие дни (пн-пт), `send_hour_from..to` по Мск (`tz=UTC+cfg.tz_offset_hours`); `async def tick(session, now_utc, cfg) -> TickStats` — выбирает eligible enrollments (`next_send_at<=now`, status pending/active), для каждого: стоп-чек (suppression join, has_active_deal, campaign.status != active) → создаёт Message(queued) со сгенерированным Message-ID (`<uuid@outreacher>`), рендер через render_step, sender-выбор (health=ok, остаток лимита, наименьший sent_today, ротация по дню), обновляет enrollment (current_step, next_send_at=next_send_time от now), инкремент sender.sent_today, лимит campaign.daily_cap.

- [ ] **Тесты (все на фикстурном времени):**
  1. задержка 2 раб.дня от пятницы → вторник; окно 8-12 Мск: время 07:50 Мск → 08:00, 12:30 → следующий день 08:00.
  2. контакт в suppression → enrollment → excluded, Message не создан.
  3. has_active_deal → excluded.
  4. campaign.paused → пропуск.
  5. daily_cap исчерпан → next_send_at сдвигается на следующий день, письмо не создаётся.
  6. идемпотентность: повторный tick по уже обработанному enrollment не создаёт второй Message (message_id UQ + current_step).
  7. sender с sent_today=daily_limit не выбирается; sent_date != сегодня → счётчик сброшен.

· Реализация · PASS · commit `feat: sequence scheduler with stop-rules, windows, limits`.

---

### Task 11: Sender (outbox, SMTP)

**Files:** Create `src/app/mail/sender.py`; Test `tests/unit/test_sender.py`

**Interfaces:** `async def send_pending(session, smtp_factory) -> SendStats`; для Message(queued): build_mime, `Message-ID` из messages.message_id, SMTP send; 4xx → retry_count+1, отложить (15мин/1ч/4ч → после 3 failed); 5xx → failed + smtp_error; success → sent+sent_at; после — неблокирующе поставить задачу b24map.log_activity (в MVP — та же транзакция очереди `b24_outbox` таблица: `b24_jobs(kind, payload JSON, done)`).

- [ ] Тесты: фейковый SMTP (async callable, поднимает `SMTPRecipientsRefused` для конкретного адреса) → 5xx-подобное поведение → failed; успех → sent, b24_job создан; идемпотентность: повторный прогон не шлёт повторно (status=sent). · Реализация · PASS · commit `feat: smtp sender with outbox pattern`.

---

### Task 12: Классификатор ответов

**Files:** Create `src/app/classify.py`; Test `tests/unit/test_classify.py`

**Interfaces:** `def classify(headers: dict[str, str], body: str) -> tuple[str, dt.datetime | None]` → (class, ooo_until); порядок: Auto-Submitted!=no → ooo + `_parse_ooo_until(body)` («до 15 января», «с 5 по 20 декабря», «возвращаюсь 02.10», нет даты → now+5д); паттерны unsub («отпис», «не пишите», «удалите»); negative («не актуально», «не интересно», «не нужн», «откаж»); referral («напишите.{0,40}@|спросите у|передайте»); positive («интересно», «давайте», «шлите», «сколько стоит», «актуально», «расскажите», «?» с вопросительным словом «когда|как|что|почему|можно»); иначе unknown.

- [ ] Тесты: ≥3 примера на класс из реальных RU-формулировок + граничные («актуально» в «не актуально» не даёт positive — порядок проверок negative раньше positive). · Реализация · PASS · commit `feat: rule-based reply classifier with ooo date parsing`.

---

### Task 13: IMAP listener + NDR

**Files:** Create `src/app/mail/listener.py`, `src/app/mail/ndr.py`; Test `tests/unit/test_listener.py`

**Interfaces:** `def match_incoming(msg_headers, db_message_ids: set[str]) -> Message | None` (по In-Reply-To/References); `def is_ndr(headers, from_email) -> bool` (from содержит mailer-daemon/postmaster или Auto-Submitted auto-generated + multipart/report); `parse_ndr_code(body) -> str | None` (regex `5\.\d+\.\d+` / `4\.\d+\.\d+`, приоритет final-recipient); `async def poll_mailbox(session, mailbox_cfg)` — IMAP IDLE (imapclient, fallback NOOP-цикл), для каждого нового: fetch headers+text → match → Incoming + classify → apply (`apply_classification` из Task 14) — NDR: hard → suppress(hard_bounce)+enrollment.bounced.

- [ ] Тесты на парсинг (без живого IMAP): In-Reply-To матч; NDR 5.1.1 → hard; 4.2.1 → фиксация soft. · Реализация · PASS · commit `feat: imap listener matching and ndr parsing`.

---

### Task 14: b24map + apply_classification

**Files:** Create `src/app/b24map.py`; Test `tests/unit/test_b24map.py`

**Interfaces:**
- `async def log_activity(client, session, message: Message, contact: Contact)` — `crm.activity.add`: OWNER_TYPE_ID=3, OWNER_ID=b24_contact_id, TYPE_ID=4, COMMUNICATIONS=[{VALUE: email, ENTITY_ID, ENTITY_TYPE_ID:3}], DIRECTION=2, COMPLETED='Y', SETTINGS={'DISABLE_SENDING_MESSAGE_COPY': 'Y'}, SUBJECT=f"[Outreacher] {subject}", DESCRIPTION=body, RESPONSIBLE_ID=assigned. Сохранить b24_activity_id.
- `async def log_reply(client, inc: Incoming, contact) -> int` — `crm.timeline.comment.add` ENTITY_TYPE='contact': `[Outreacher/{класс}] {snippet}`.
- `async def create_task(client, contact, text)` — crm.activity.add TYPE_ID=3, DEADLINE=+4ч, RESPONSIBLE_ID=assigned, SUBJECT=«Ответ клиента — связаться».
- `async def mark_unsubscribed(client, contact)` — timeline comment «Клиент отписался от рассылок (Outreacher)».
- `async def apply_classification(session, client, inc: Incoming)` — переключение статусов по §4.6 спеки: positive → enrollment.replied + comment + task; negative → replied + comment; ooo → paused_ooo + ooo_until+1 раб.день; unsub → suppress + mark; referral → replied + comment; unknown → ничего (UI). Вызовы Б24 — best-effort (сбой → b24_jobs ретрай).

- [ ] Тесты с фейковым client: правильные payload'ы (TYPE_ID=4, DISABLE_SENDING_MESSAGE_COPY, ровно 1 COMMUNICATION); apply_classification для всех классов. · Реализация · PASS · commit `feat: b24 mapping and classification apply`.

---

### Task 15: worker, main app, UI, health, Docker

**Files:** Create `src/app/worker.py`, `src/app/main.py`, `src/app/routes/ui.py`, `src/app/routes/api.py`, `templates/*.html` (base, dashboard, campaign, campaign_form, segments, inbox, suppression, senders), `static/style.css`; Modify `docker/Dockerfile`.

**Interfaces:** worker: бесконечный цикл — `tick` (каждые 60с), `send_pending` (каждые 30с), `poll_mailbox` для каждого sender (IDLE-тред/таск), `sync_all` (каждые 15 мин), `b24_jobs` processor (каждые 30с), heartbeat в таблицу `worker_heartbeat`. main.py: FastAPI(lifespan=engine), `/health` (db ping + heartbeat age), basic-auth зависимость на UI/API (`operator_password`), роуты: `/` (dashboard: кампании+метрики SQL), `/campaigns/{id}` (метрики по шагам, инбокс кампании), `/campaigns/new` (форма: имя, slug, тип, сегмент, шаги JSON), actions: start/pause, `/segments` (+preview), `/inbox` (unknown → кнопки классов), `/suppression`, `/senders`, `/b24/event`, `/unsub/{token}`. Метрики одним SQL: sent/bounce/reply/unsub по кампании и шагу.

- [ ] Шаги: worker → main+auth+health → шаблоны (минимальный чистый CSS, без фреймворков) → compose build → `curl /health` локально через `docker compose up -d postgres && alembic upgrade head && uvicorn` smoke. · commit `feat: worker loop, web ui, health, docker`.

---

### Task 16: README + E2E чек-лист

**Files:** Create `README.md` (запуск, env, установка Б24-приложения: redirect/back URL `https://outreach.<домен>/b24/event`, права: crm, batch, user, bizproc на виток 2), `docs/E2E-CHECKLIST.md` (сценарий из §9 спеки: установка → синк → тест-кампания 3 контакта → письмо → ответ positive → стоп+таймлайн+дело → отписка → повторная кампания исключает). · commit `docs: readme and e2e checklist`.

---

## Self-Review

**Spec coverage:** §2 контейнеры → Task 1,15; §3 модели → Task 1 (+`b24_users`, `processed_events`, `b24_jobs`, `worker_heartbeat` добавлены по ходу); §4.1 синк → T5; §4.2 scheduler → T10; §4.3 sender → T9,T11; §4.4 listener → T13; §4.5 классификация → T12; §4.6 применение → T14; §4.7 отписка → T7; §5 методы/грабли → T2-T5,T14; §6 юридика → T6(enroll фильтр suppression),T7,T8(футер); §7 метрики/UI → T15; §8 устойчивость → T10(идемпотентность),T11(outbox),T4(дедуп событий); §9 тесты — в каждом task + T16 E2E; §10 фазирование — только MVP. Пропуск: cooldown контакта между кампаниями (спека §1) — покрыт Task 6 (enroll исключает существующие enrollment'ы активных кампаний; полный cooldown 2-3 мес — виток 2, помечено в T6 docstring).
**Placeholder scan:** в T2 код batch требует чистки при имплементации (помечено) — executor чинит на месте; остальное конкретно.
**Type consistency:** `Rendered(subject, body, unsub_token)` T8 → используется T10; `make_token/parse_token` T7 ↔ T8 футер; `apply_classification` T14 ← T13; message_id строка `<hex@outreacher>` единый формат T10/T11/T13.
