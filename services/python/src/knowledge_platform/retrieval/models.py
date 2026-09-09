"""Owned, rebuildable projection catalog and durable outbox-consumer offsets."""
from datetime import UTC, datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now():
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Page(Base):
    __tablename__="retrieval_pages"
    page_id: Mapped[str]=mapped_column(String,primary_key=True)
    current_revision: Mapped[str]=mapped_column(String)
    current_version: Mapped[int]=mapped_column(BigInteger)


class Revision(Base):
    __tablename__="retrieval_revisions"
    __table_args__=(UniqueConstraint("page_id","version"),Index("retrieval_reconcile_order","checked_at","revision_id"),Index("retrieval_scope","space_id"))
    revision_id: Mapped[str]=mapped_column(String,primary_key=True)
    page_id: Mapped[str]=mapped_column(String,index=True)
    version: Mapped[int]=mapped_column(BigInteger)
    space_id: Mapped[str]=mapped_column(String)
    acl_domain: Mapped[str]=mapped_column(String)
    policy_fingerprint: Mapped[str]=mapped_column(String)
    acl_epoch: Mapped[int]=mapped_column(BigInteger)
    policies: Mapped[list]=mapped_column(JSONB)
    fragments: Mapped[list]=mapped_column(JSONB)
    is_current: Mapped[bool]=mapped_column(Boolean)
    generation: Mapped[int]=mapped_column(BigInteger)
    checked_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)


class Delivery(Base):
    __tablename__="retrieval_deliveries"
    event_id: Mapped[str]=mapped_column(String,primary_key=True)
    page_id: Mapped[str]=mapped_column(String)
    revision_id: Mapped[str]=mapped_column(String)
    generation: Mapped[int]=mapped_column(BigInteger)
    projected_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)


async def initialize(engine):
    async with engine.begin() as connection:
        await connection.execute(text("SELECT pg_advisory_xact_lock(728413)"))
        await connection.execute(text("CREATE SEQUENCE IF NOT EXISTS retrieval_projection_generation AS bigint START WITH 1"))
        await connection.run_sync(Base.metadata.create_all)