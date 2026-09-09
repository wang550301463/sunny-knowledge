"""Knowledge-owned PostgreSQL tables. Revisions, snapshots and audit are append-only."""
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class Page(Base):
    __tablename__ = 'knowledge_pages'
    id: Mapped[str] = mapped_column(String(512), primary_key=True)
    space_id: Mapped[str] = mapped_column(String(512), index=True)
    current_revision: Mapped[str | None] = mapped_column(String(512), nullable=True)
    revision_number: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[str] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Revision(Base):
    __tablename__ = 'knowledge_revisions'
    __table_args__ = (UniqueConstraint('page_id', 'number'),)
    id: Mapped[str] = mapped_column(String(512), primary_key=True, default=new_id)
    page_id: Mapped[str] = mapped_column(ForeignKey('knowledge_pages.id'), index=True)
    number: Mapped[int] = mapped_column(Integer)
    base_revision: Mapped[str | None] = mapped_column(String(512), nullable=True)
    content: Mapped[dict] = mapped_column(JSONB)
    created_by: Mapped[str] = mapped_column(String(512))
    publication_kind: Mapped[str] = mapped_column(String(64))
    proof: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(512), nullable=True, unique=True)
    request_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Proposal(Base):
    __tablename__ = 'knowledge_proposals'
    id: Mapped[str] = mapped_column(String(512), primary_key=True, default=new_id)
    page_id: Mapped[str] = mapped_column(ForeignKey('knowledge_pages.id'), index=True)
    base_revision: Mapped[str | None] = mapped_column(String(512), nullable=True)
    content: Mapped[dict] = mapped_column(JSONB)
    kind: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default='pending', index=True)
    proposed_by: Mapped[str] = mapped_column(String(512))
    reviewed_by: Mapped[str | None] = mapped_column(String(512), nullable=True)
    review_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_revision: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SourceSnapshot(Base):
    __tablename__ = 'knowledge_source_snapshots'
    __table_args__ = (UniqueConstraint('source_id', 'source_revision', 'path'),)
    id: Mapped[str] = mapped_column(String(512), primary_key=True, default=new_id)
    source_id: Mapped[str] = mapped_column(String(512), index=True)
    source_revision: Mapped[str] = mapped_column(String(512))
    resource_id: Mapped[str] = mapped_column(String(512), index=True)
    space_id: Mapped[str] = mapped_column(String(512), index=True)
    path: Mapped[str] = mapped_column(String(2048))
    kind: Mapped[str] = mapped_column(String(32))
    text: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    object_key: Mapped[str] = mapped_column(String(2048))
    registered_by: Mapped[str] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Audit(Base):
    __tablename__ = 'knowledge_audit'
    id: Mapped[str] = mapped_column(String(512), primary_key=True, default=new_id)
    action: Mapped[str] = mapped_column(String(64), index=True)
    actor_id: Mapped[str] = mapped_column(String(512))
    resource_id: Mapped[str] = mapped_column(String(512), index=True)
    details: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Outbox(Base):
    __tablename__ = 'knowledge_outbox'
    __table_args__ = (UniqueConstraint('page_id', 'version'),)
    id: Mapped[str] = mapped_column(String(512), primary_key=True, default=new_id)
    page_id: Mapped[str] = mapped_column(ForeignKey('knowledge_pages.id'), index=True)
    revision_id: Mapped[str] = mapped_column(ForeignKey('knowledge_revisions.id'))
    version: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(64), default='page.published')
    payload: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Delivery(Base):
    __tablename__ = 'knowledge_deliveries'
    __table_args__ = (Index('knowledge_delivery_pending', 'consumer', 'acked_at', 'lease_until'),)
    event_id: Mapped[str] = mapped_column(ForeignKey('knowledge_outbox.id'), primary_key=True)
    consumer: Mapped[str] = mapped_column(String(32), primary_key=True)
    lease_token: Mapped[str | None] = mapped_column(String(512), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    acked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


async def initialize(engine: AsyncEngine) -> None:
    """Bootstrap this service's own schema; never read/write any other service's DB."""
    if engine.dialect.name != 'postgresql':
        raise RuntimeError('Knowledge requires PostgreSQL; SQLite is not supported')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await connection.execute(text('''
            CREATE OR REPLACE FUNCTION knowledge_reject_mutation() RETURNS trigger AS $$
            BEGIN RAISE EXCEPTION 'canonical records are immutable'; END;
            $$ LANGUAGE plpgsql
        '''))
        for table in ('knowledge_revisions', 'knowledge_source_snapshots', 'knowledge_audit', 'knowledge_outbox'):
            # The advisory lock also serializes first startup by multiple replicas.
            await connection.execute(text("SELECT pg_advisory_xact_lock(hashtext('knowledge_schema_v1'))"))
            await connection.execute(text(f'DROP TRIGGER IF EXISTS immutable_record ON {table}'))
            await connection.execute(text(f'''
                CREATE TRIGGER immutable_record BEFORE UPDATE OR DELETE ON {table}
                FOR EACH ROW EXECUTE FUNCTION knowledge_reject_mutation()
            '''))