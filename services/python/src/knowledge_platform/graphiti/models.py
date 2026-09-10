"""Graphiti-owned rebuildable projection catalog and durable delivery offsets.

Reconstructed 2026-09-09: the original module was lost with the disk wipe.
Mirrors retrieval/models.py (the parallel projection catalog) with the field
set consumed by graphiti/store.py: Page, Revision (graph + fragments + policy
fingerprint), Delivery outbox offsets and the Rebuild checkpoint."""
from datetime import UTC, datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now():
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Page(Base):
    __tablename__ = "graphiti_pages"
    page_id: Mapped[str] = mapped_column(String, primary_key=True)
    current_revision: Mapped[str] = mapped_column(String)
    current_version: Mapped[int] = mapped_column(BigInteger)


class Revision(Base):
    __tablename__ = "graphiti_revisions"
    __table_args__ = (
        UniqueConstraint("page_id", "version"),
        Index("graphiti_reconcile_order", "checked_at", "revision_id"),
        Index("graphiti_scope", "space_id"),
    )
    revision_id: Mapped[str] = mapped_column(String, primary_key=True)
    page_id: Mapped[str] = mapped_column(String, index=True)
    version: Mapped[int] = mapped_column(BigInteger)
    space_id: Mapped[str] = mapped_column(String)
    projection_id: Mapped[str] = mapped_column(String)
    metadata_json: Mapped[dict] = mapped_column(JSONB)
    graph: Mapped[dict] = mapped_column(JSONB)
    fragment_ids: Mapped[list] = mapped_column(JSONB)
    policy_fingerprint: Mapped[str] = mapped_column(String)
    acl_domain: Mapped[str] = mapped_column(String, default="")
    acl_epoch: Mapped[int] = mapped_column(BigInteger)
    policies: Mapped[list] = mapped_column(JSONB)
    is_current: Mapped[bool] = mapped_column(Boolean)
    generation: Mapped[int] = mapped_column(BigInteger)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    failure_code: Mapped[str | None] = mapped_column(String, nullable=True)
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)


class Delivery(Base):
    __tablename__ = "graphiti_deliveries"
    event_id: Mapped[str] = mapped_column(String, primary_key=True)
    page_id: Mapped[str] = mapped_column(String)
    revision_id: Mapped[str] = mapped_column(String)
    generation: Mapped[int] = mapped_column(BigInteger)
    projected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Rebuild(Base):
    __tablename__ = "graphiti_rebuilds"
    key: Mapped[str] = mapped_column(String, primary_key=True)
    cursor: Mapped[str] = mapped_column(String, nullable=True)
    processed: Mapped[int] = mapped_column(BigInteger, default=0)
    complete: Mapped[bool] = mapped_column(Boolean, default=False)


async def initialize(engine):
    async with engine.begin() as connection:
        await connection.execute(text("SELECT pg_advisory_xact_lock(728414)"))
        await connection.execute(text("CREATE SEQUENCE IF NOT EXISTS graphiti_projection_generation AS bigint START WITH 1"))
        await connection.run_sync(Base.metadata.create_all)
        # Additive columns for existing deployments (store.py writes acl_domain,
        # failure_code, failed_attempts).
        await connection.execute(text(
            "ALTER TABLE graphiti_revisions ADD COLUMN IF NOT EXISTS acl_domain varchar NOT NULL DEFAULT ''"))
        await connection.execute(text(
            "ALTER TABLE graphiti_revisions ADD COLUMN IF NOT EXISTS failure_code varchar"))
        await connection.execute(text(
            "ALTER TABLE graphiti_revisions ADD COLUMN IF NOT EXISTS failed_attempts integer NOT NULL DEFAULT 0"))
