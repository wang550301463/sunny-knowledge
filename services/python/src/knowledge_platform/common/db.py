from contextlib import asynccontextmanager
from dataclasses import dataclass

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine


@dataclass
class Database:
    engine: AsyncEngine

    @asynccontextmanager
    async def session(self):
        # Each task has its own session; callers explicitly own transaction boundaries.
        factory = async_sessionmaker(self.engine, expire_on_commit=False)
        async with factory() as session:
            yield session

    async def create_schema(self, metadata: MetaData):
        async with self.engine.begin() as connection:
            await connection.run_sync(metadata.create_all)

    async def close(self):
        await self.engine.dispose()


def create_database(url: str) -> Database:
    if url.startswith('postgresql://'):
        url = url.replace('postgresql://', 'postgresql+psycopg://', 1)
    if not url.startswith('postgresql+psycopg://'):
        raise ValueError('A PostgreSQL psycopg connection is required')
    return Database(create_async_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5))