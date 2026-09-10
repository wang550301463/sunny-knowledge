"""Ingest-owned PostgreSQL metadata, immutable versions/previews, durable tasks and audit."""
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now(): return datetime.now(timezone.utc)
def uid(): return str(uuid4())


class Base(DeclarativeBase): pass


class Source(Base):
    __tablename__ = 'ingest_sources'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(256))
    space_id: Mapped[str] = mapped_column(String(512), index=True)
    resource_id: Mapped[str] = mapped_column(String(512), unique=True)
    kind: Mapped[str] = mapped_column(String(16))
    version: Mapped[int] = mapped_column(Integer, default=1)
    state: Mapped[str] = mapped_column(String(16), default='active')
    created_by: Mapped[str] = mapped_column(String(512))
    latest_task_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    auto_sync: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_sync_interval_seconds: Mapped[int] = mapped_column(Integer, default=3600)
    last_auto_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_revision: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class SourceVersion(Base):
    __tablename__ = 'ingest_source_versions'
    source_id: Mapped[str] = mapped_column(ForeignKey('ingest_sources.id'), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    config: Mapped[dict] = mapped_column(JSONB)
    credential_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Preview(Base):
    __tablename__ = 'ingest_previews'
    source_id: Mapped[str] = mapped_column(ForeignKey('ingest_sources.id'), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    manifest_key: Mapped[str] = mapped_column(String(512))
    manifest: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Task(Base):
    __tablename__ = 'ingest_tasks'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    source_id: Mapped[str] = mapped_column(ForeignKey('ingest_sources.id'), index=True)
    source_version: Mapped[int] = mapped_column(Integer)
    space_id: Mapped[str] = mapped_column(String(512), index=True)
    operation: Mapped[str] = mapped_column(String(16))
    identity: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(32), default='queued')
    stage: Mapped[str] = mapped_column(String(32), default='queued')
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_by: Mapped[str] = mapped_column(String(512))
    workflow_started: Mapped[bool] = mapped_column(Boolean, default=False)
    checkpoint: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Step(Base):
    __tablename__ = 'ingest_steps'
    task_id: Mapped[str] = mapped_column(ForeignKey('ingest_tasks.id'), primary_key=True)
    number: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    page_id: Mapped[str] = mapped_column(String(512))
    path: Mapped[str] = mapped_column(String(2048))
    request: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(32), default='pending')
    result: Mapped[dict] = mapped_column(JSONB, default=dict)


class SourcePage(Base):
    __tablename__ = 'ingest_source_pages'
    source_id: Mapped[str] = mapped_column(ForeignKey('ingest_sources.id'), primary_key=True)
    page_id: Mapped[str] = mapped_column(String(512), primary_key=True)
    path: Mapped[str] = mapped_column(String(2048))
    source_version: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(32), default='active')


class Audit(Base):
    __tablename__ = 'ingest_audit'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    source_id: Mapped[str] = mapped_column(String(36), index=True)
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    actor_id: Mapped[str] = mapped_column(String(512))
    action: Mapped[str] = mapped_column(String(64))
    detail: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


async def initialize(engine):
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await connection.execute(text('''CREATE OR REPLACE FUNCTION ingest_reject_immutable_mutation()
            RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
            RAISE EXCEPTION 'Immutable ingest record'; END $$'''))
        # Auto-sync columns: additive, idempotent (existing deployments predate them).
        for column, definition in (
            ('auto_sync', 'boolean NOT NULL DEFAULT false'),
            ('auto_sync_interval_seconds', 'integer NOT NULL DEFAULT 3600'),
            ('last_auto_sync_at', 'timestamptz'),
            ('last_seen_revision', 'varchar(256)'),
        ):
            await connection.execute(text(
                f'ALTER TABLE ingest_sources ADD COLUMN IF NOT EXISTS {column} {definition}'))
        # Task dispatch checkpoint column (Temporal dispatcher uses it to
        # avoid duplicate workflow starts after a restart).
        await connection.execute(text(
            'ALTER TABLE ingest_tasks ADD COLUMN IF NOT EXISTS workflow_started boolean NOT NULL DEFAULT false'))
        await connection.execute(text(
            'ALTER TABLE ingest_tasks ADD COLUMN IF NOT EXISTS checkpoint jsonb NOT NULL DEFAULT \'{}\''))
        # Registration.snapshot stores the full knowledge snapshot response (dict).
        # Only migrate if the column is still varchar (drop+add wipes data, so
        # guard with a type check to make it idempotent and non-destructive).
        await connection.execute(text("""
            DO $$ BEGIN
                IF EXISTS (SELECT 1 FROM information_schema.columns
                           WHERE table_name = 'ingest_registrations'
                             AND column_name = 'snapshot'
                             AND data_type = 'character varying') THEN
                    ALTER TABLE ingest_registrations DROP COLUMN snapshot;
                    ALTER TABLE ingest_registrations ADD COLUMN snapshot jsonb;
                END IF;
            END $$;
        """))
        for table in ('ingest_source_versions', 'ingest_previews', 'ingest_audit'):
            await connection.execute(text(f'DROP TRIGGER IF EXISTS immutable_record ON {table}'))
            await connection.execute(text(f'''CREATE TRIGGER immutable_record BEFORE UPDATE OR DELETE
                ON {table} FOR EACH ROW EXECUTE FUNCTION ingest_reject_immutable_mutation()'''))

class Registration(Base):
    """Immutable (source_id, version, path) registration of a file snapshot.

    Reconstructed 2026-09-09 from pipeline.py usage: session.get(Registration,
    (source.id, task.source_version, file['path'])) and add(Registration(
    source_id=..., version=..., path=..., snapshot=...)). The snapshot column
    stores the full knowledge-service snapshot response (dict with id, text,
    kind, sha256 etc.) for downstream plan/publish consumption."""
    __tablename__ = 'ingest_registrations'
    source_id: Mapped[str] = mapped_column(ForeignKey('ingest_sources.id'), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    path: Mapped[str] = mapped_column(String(2048), primary_key=True)
    snapshot: Mapped[dict] = mapped_column(JSONB)
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, server_default=text('now()'))
