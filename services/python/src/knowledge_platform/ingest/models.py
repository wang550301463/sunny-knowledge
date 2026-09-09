"""Ingest-owned PostgreSQL metadata, immutable versions/previews, durable tasks and audit."""
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, text
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
        for table in ('ingest_source_versions', 'ingest_previews', 'ingest_audit'):
            await connection.execute(text(f'DROP TRIGGER IF EXISTS immutable_record ON {table}'))
            await connection.execute(text(f'''CREATE TRIGGER immutable_record BEFORE UPDATE OR DELETE
                ON {table} FOR EACH ROW EXECUTE FUNCTION ingest_reject_immutable_mutation()'''))

class Registration(Base):
    """Immutable (source_id, version, path) registration of a file snapshot.

    Reconstructed 2026-09-09 from pipeline.py usage: session.get(Registration,
    (source.id, task.source_version, file['path'])) and add(Registration(
    source_id=..., version=..., path=..., snapshot=...))."""
    __tablename__ = 'ingest_registrations'
    source_id: Mapped[str] = mapped_column(ForeignKey('ingest_sources.id'), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    path: Mapped[str] = mapped_column(String(2048), primary_key=True)
    snapshot: Mapped[str] = mapped_column(String(512))
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, server_default=text('now()'))
