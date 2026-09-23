from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def make_engine(url: str | None = None):
    return create_async_engine(url or get_settings().database_url, pool_pre_ping=True)


def make_sessionmaker(url: str | None = None) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(make_engine(url), expire_on_commit=False)


SessionMaker = make_sessionmaker()
