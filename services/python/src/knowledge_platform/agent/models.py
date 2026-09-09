"""Agent-owned persistent configurations, runs, ordered events, feedback and digests."""
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now():
    return datetime.now(UTC)


def new_id():
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class Agent(Base):
    __tablename__ = 'agent_definitions'
    id: Mapped[str] = mapped_column(String(256), primary_key=True, default=new_id)
    owner_space_id: Mapped[str] = mapped_column(String(256), index=True)
    created_by: Mapped[str] = mapped_column(String(256))
    current_configuration: Mapped[str] = mapped_column(String(256))
    published_configuration: Mapped[str | None] = mapped_column(String(256), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Configuration(Base):
    __tablename__ = 'agent_configurations'
    __table_args__ = (UniqueConstraint('agent_id', 'version'),)
    id: Mapped[str] = mapped_column(String(256), primary_key=True, default=new_id)
    agent_id: Mapped[str] = mapped_column(ForeignKey('agent_definitions.id'), index=True)
    version: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text)
    config: Mapped[dict] = mapped_column(JSONB)
    created_by: Mapped[str] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Audit(Base):
    __tablename__ = 'agent_audit'
    id: Mapped[str] = mapped_column(String(256), primary_key=True, default=new_id)
    agent_id: Mapped[str] = mapped_column(ForeignKey('agent_definitions.id'), index=True)
    actor: Mapped[str] = mapped_column(String(256))
    action: Mapped[str] = mapped_column(String(64))
    configuration_id: Mapped[str] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Session(Base):
    __tablename__ = 'agent_sessions'
    id: Mapped[str] = mapped_column(String(256), primary_key=True, default=new_id)
    owner: Mapped[str] = mapped_column(String(256), index=True)
    title: Mapped[str] = mapped_column(String(160))
    cleared: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Run(Base):
    __tablename__ = 'agent_runs'
    __table_args__ = (
        UniqueConstraint('owner', 'idempotency_key'),
        CheckConstraint("status IN ('queued','running','completed','partial','failed','cancelled')"),
        CheckConstraint('event_seq >= 0'),
        Index('agent_session_active', 'session_id', unique=True, postgresql_where=text("status IN ('queued','running')")),
    )
    id: Mapped[str] = mapped_column(String(256), primary_key=True, default=new_id)
    owner: Mapped[str] = mapped_column(String(256), index=True)
    session_id: Mapped[str] = mapped_column(ForeignKey('agent_sessions.id'), index=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey('agent_definitions.id'))
    configuration_id: Mapped[str] = mapped_column(ForeignKey('agent_configurations.id'))
    configuration: Mapped[dict] = mapped_column(JSONB)
    request: Mapped[dict] = mapped_column(JSONB)
    actual_scope: Mapped[list] = mapped_column(JSONB)
    idempotency_key: Mapped[str] = mapped_column(String(256))
    request_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default='queued', index=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    encrypted_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    lease_token: Mapped[str | None] = mapped_column(String(256), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    event_seq: Mapped[int] = mapped_column(BigInteger, default=0)
    checkpoint: Mapped[dict] = mapped_column(JSONB, default=dict)
    dependencies: Mapped[list] = mapped_column(JSONB, default=list)
    citations: Mapped[dict] = mapped_column(JSONB, default=dict)
    answer: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    usage: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Event(Base):
    __tablename__ = 'agent_run_events'
    __table_args__ = (UniqueConstraint('run_id', 'seq'),)
    id: Mapped[str] = mapped_column(String(256), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey('agent_runs.id'), index=True)
    seq: Mapped[int] = mapped_column(BigInteger)
    type: Mapped[str] = mapped_column(String(64))
    data: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Feedback(Base):
    __tablename__ = 'agent_feedback'
    __table_args__ = (UniqueConstraint('owner', 'idempotency_key'),)
    id: Mapped[str] = mapped_column(String(256), primary_key=True, default=new_id)
    owner: Mapped[str] = mapped_column(String(256))
    run_id: Mapped[str] = mapped_column(ForeignKey('agent_runs.id'), index=True)
    rating: Mapped[str] = mapped_column(String(32))
    comment: Mapped[str] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(String(256))
    request_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Summary(Base):
    __tablename__ = 'agent_summaries'
    __table_args__ = (UniqueConstraint('session_id', 'version'),)
    id: Mapped[str] = mapped_column(String(256), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(ForeignKey('agent_sessions.id'), index=True)
    version: Mapped[int] = mapped_column(Integer)
    run_ids: Mapped[list] = mapped_column(JSONB)
    content: Mapped[list] = mapped_column(JSONB)
    dependencies: Mapped[list] = mapped_column(JSONB)
    source_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


async def initialize(engine):
    engine.sync_engine.hide_parameters = True
    async with engine.begin() as conn:
        await conn.execute(text("SELECT pg_advisory_xact_lock(hashtext('agent_schema_v1'))"))
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("""CREATE OR REPLACE FUNCTION agent_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'Agent immutable record'; END $$"""))
        for table in [Configuration.__tablename__, Audit.__tablename__, Event.__tablename__, Feedback.__tablename__, Summary.__tablename__]:
            await conn.execute(text(f'DROP TRIGGER IF EXISTS agent_immutable_guard ON {table}'))
            await conn.execute(text(f'CREATE TRIGGER agent_immutable_guard BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION agent_immutable()'))