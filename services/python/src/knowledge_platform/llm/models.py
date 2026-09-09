"""LLM-owned PostgreSQL records; version, audit, tests, invocation and outcomes append-only."""
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now():
    return datetime.now(UTC)


def new_id():
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class ModelRecord(Base):
    __tablename__ = 'llm_models'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    configuration_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    state: Mapped[str] = mapped_column(String(20), default='active')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Configuration(Base):
    __tablename__ = 'llm_configurations'
    __table_args__ = (UniqueConstraint('model_id', 'version'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    model_id: Mapped[str] = mapped_column(ForeignKey('llm_models.id'), index=True)
    version: Mapped[int] = mapped_column(Integer)
    config: Mapped[dict] = mapped_column(JSONB)
    credential_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Audit(Base):
    __tablename__ = 'llm_audit'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    model_id: Mapped[str] = mapped_column(ForeignKey('llm_models.id'), index=True)
    configuration_id: Mapped[str] = mapped_column(ForeignKey('llm_configurations.id'))
    action: Mapped[str] = mapped_column(String(32))
    actor_id: Mapped[str] = mapped_column(String(512))
    details: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class CapabilityTest(Base):
    __tablename__ = 'llm_capability_tests'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    configuration_id: Mapped[str] = mapped_column(ForeignKey('llm_configurations.id'), index=True)
    actor_id: Mapped[str] = mapped_column(String(512))
    test_state: Mapped[str] = mapped_column(String(20))
    capabilities: Mapped[dict] = mapped_column(JSONB)
    dimensions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Invocation(Base):
    __tablename__ = 'llm_invocations'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    configuration_id: Mapped[str] = mapped_column(ForeignKey('llm_configurations.id'), index=True)
    caller: Mapped[str] = mapped_column(String(64))
    capability: Mapped[str] = mapped_column(String(20))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class UsageOutcome(Base):
    __tablename__ = 'llm_usage_outcomes'
    invocation_id: Mapped[str] = mapped_column(ForeignKey('llm_invocations.id'), primary_key=True)
    outcome: Mapped[str] = mapped_column(String(64))
    provider_request_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    usage: Mapped[dict] = mapped_column(JSONB)
    duration_ms: Mapped[int] = mapped_column(Integer)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


async def initialize(engine):
    async with engine.begin() as connection:
        await connection.execute(text("SELECT pg_advisory_xact_lock(hashtext('llm-schema-v1'))"))
        await connection.run_sync(Base.metadata.create_all)
        await connection.execute(text('''CREATE OR REPLACE FUNCTION llm_reject_mutation()
            RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
            RAISE EXCEPTION 'LLM append-only records cannot be changed'; END $$'''))
        for table in ('llm_configurations', 'llm_audit', 'llm_capability_tests', 'llm_invocations', 'llm_usage_outcomes'):
            await connection.execute(text(f'DROP TRIGGER IF EXISTS immutable_record ON {table}'))
            await connection.execute(text(f'CREATE TRIGGER immutable_record BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION llm_reject_mutation()'))