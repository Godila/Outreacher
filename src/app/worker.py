"""Циклы воркера: scheduler → sender → IMAP-листенер → B24-jobs → sync. Запуск: python -m app.worker"""

import asyncio
import contextlib
import logging
import time

from sqlalchemy import select

from app.b24map import process_b24_jobs
from app.config import get_settings
from app.db import make_sessionmaker
from app.engine.scheduler import tick
from app.mail.listener import poll_mailbox
from app.mail.sender import send_pending
from app.models import Sender, WorkerHeartbeat, utcnow
from app.sync import sync_all

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("outreacher.worker")

TICK_SEC = 60
SEND_SEC = 30
MAILBOX_SEC = 60
B24_SEC = 30
SYNC_SEC = 900


async def heartbeat(session_factory, component: str) -> None:
    async with session_factory() as session:
        row = (
            await session.execute(select(WorkerHeartbeat).where(WorkerHeartbeat.component == component))
        ).scalars().first()
        if row is None:
            session.add(WorkerHeartbeat(component=component))
        else:
            row.updated_at = utcnow()
        await session.commit()


async def every(interval: int, name: str, session_factory, fn) -> None:
    while True:
        started = time.monotonic()
        try:
            async with session_factory() as session:
                await fn(session)
        except Exception:  # noqa: BLE001 — цикл живёт, ошибка логируется
            logger.exception("cycle %s failed", name)
        with contextlib.suppress(Exception):
            await heartbeat(session_factory, name)
        elapsed = time.monotonic() - started
        await asyncio.sleep(max(1.0, interval - elapsed))


async def main() -> None:
    settings = get_settings()
    session_factory = make_sessionmaker(settings.database_url)

    from app.b24.tokenmanager import TokenManager

    token_manager = TokenManager(session_factory)

    async def scheduler_fn(session):
        await tick(session, utcnow(), settings)

    async def sender_fn(session):
        await send_pending(session, settings)

    async def b24_jobs_fn(session):
        client = await token_manager.get_client()
        await process_b24_jobs(session, client)

    async def sync_fn(session):
        client = await token_manager.get_client()
        await sync_all(client, session)

    async def mailboxes_fn(session):
        senders = (await session.execute(select(Sender))).scalars().all()
        for sender in senders:
            await poll_mailbox(session_factory, settings, sender)

    loops = [
        every(TICK_SEC, "scheduler", session_factory, scheduler_fn),
        every(SEND_SEC, "sender", session_factory, sender_fn),
        every(MAILBOX_SEC, "listener", session_factory, mailboxes_fn),
        every(B24_SEC, "b24", session_factory, b24_jobs_fn),
        every(SYNC_SEC, "sync", session_factory, sync_fn),
    ]
    logger.info("worker started")
    await asyncio.gather(*loops)


if __name__ == "__main__":
    asyncio.run(main())
