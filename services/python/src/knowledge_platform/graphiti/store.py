"""Per-page serialization plus monotonic external generations across failed transactions."""

from __future__ import annotations

import hashlib
from contextlib import asynccontextmanager
from datetime import timedelta

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import array

from .models import Delivery, Page, Rebuild, Revision, now
from .schemas import GraphError, Projection, unavailable


def lock_id(page_id):
    return int.from_bytes(
        hashlib.sha256(("graphiti:" + page_id).encode()).digest()[:8], signed=True
    )


class Catalog:
    def __init__(self, database):
        self.database = database

    @asynccontextmanager
    async def rebuild_lock(self, key):
        async with self.database.engine.connect() as connection:
            number = lock_id("rebuild:" + key)
            acquired = await connection.scalar(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": number}
            )
            await connection.commit()
            if not acquired:
                raise GraphError(
                    409, "rebuild_running", "A rebuild for this index is already running"
                )
            try:
                yield
            finally:
                await connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": number})
                await connection.commit()

    async def rebuild_state(self, key):
        async with self.database.session() as session, session.begin():
            row = await session.get(Rebuild, key)
            if row is None:
                row = Rebuild(key=key, cursor=None, processed=0, complete=False)
                session.add(row)
            elif row.complete:
                row.cursor = None
                row.processed = 0
                row.complete = False
            return {"cursor": row.cursor, "processed": row.processed}

    async def rebuild_checkpoint(self, key, cursor, count, complete):
        async with self.database.session() as session, session.begin():
            row = await session.scalar(select(Rebuild).where(Rebuild.key == key).with_for_update())
            if row is None:
                raise unavailable("invalid_rebuild_state", "Rebuild checkpoint is missing")
            row.cursor = cursor
            row.processed += count
            row.complete = complete
            row.updated_at = now()

    async def scope(self, spaces, limit):
        async with self.database.session() as session:
            rows = (
                await session.execute(
                    select(
                        Revision.projection_id,
                        Revision.metadata_json,
                        Revision.is_current,
                        Revision.policies,
                        Revision.policy_fingerprint,
                    )
                    .where(Revision.space_id.in_(spaces))
                    .order_by(Revision.revision_id)
                    .limit(limit + 1)
                )
            ).all()
            if len(rows) > limit:
                raise unavailable(
                    "projection_scope_too_large",
                    "Requested scope exceeds the policy registry budget",
                )
            values = []
            for r in rows:
                graph = Projection.model_validate(r.metadata_json)
                graph.is_current = r.is_current
                values.append(
                    {
                        "projection_id": r.projection_id,
                        "graph": graph,
                        "policies": r.policies,
                        "policy_fingerprint": r.policy_fingerprint,
                        "hydrated": False,
                    }
                )
            return values

    async def graphs(self, projection_ids):
        async with self.database.session() as session:
            rows = (
                await session.execute(
                    select(Revision.projection_id, Revision.graph).where(
                        Revision.projection_id.in_(projection_ids)
                    )
                )
            ).all()
            return {r.projection_id: Projection.model_validate(r.graph) for r in rows}

    async def seed_projections(self, projection_ids, fragment_ids):
        async with self.database.session() as session:
            return set(
                (
                    await session.scalars(
                        select(Revision.projection_id).where(
                            Revision.projection_id.in_(projection_ids),
                            Revision.fragment_ids.has_any(array(fragment_ids)),
                        )
                    )
                ).all()
            )

    async def revisions(self, page_id):
        async with self.database.session() as session:
            rows = (
                await session.scalars(
                    select(Revision).where(Revision.page_id == page_id).order_by(Revision.version)
                )
            ).all()
            return [
                {
                    "page_id": r.page_id,
                    "revision_id": r.revision_id,
                    "version": r.version,
                    "is_current": r.is_current,
                    "generation": r.generation,
                }
                for r in rows
            ]

    async def due(self, cutoff, limit=20):
        async with self.database.session() as session:
            rows = (
                await session.execute(
                    select(Revision.page_id, Revision.revision_id)
                    .where(Revision.checked_at < cutoff)
                    .order_by(Revision.checked_at, Revision.revision_id)
                    .limit(limit)
                )
            ).all()
            return [{"page_id": r.page_id, "revision_id": r.revision_id} for r in rows]

    async def defer(self, revision_id, code):
        async with self.database.session() as session, session.begin():
            row = await session.scalar(
                select(Revision).where(Revision.revision_id == revision_id).with_for_update()
            )
            if row:
                row.failed_attempts += 1
                row.failure_code = code
                row.checked_at = now() + timedelta(
                    seconds=min(3600, 2 ** min(row.failed_attempts, 12))
                )

    async def project(self, graph, write, *, event_id=None, exists=None):
        async with self.database.session() as session, session.begin():
            await session.execute(
                text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_id(graph.page_id)}
            )
            if event_id and await session.get(Delivery, event_id):
                return "already_projected"
            page = await session.get(Page, graph.page_id)
            if page is None:
                page = Page(
                    page_id=graph.page_id,
                    current_revision=graph.current_revision,
                    current_version=graph.current_version,
                )
                session.add(page)
            elif graph.current_version > page.current_version:
                page.current_revision, page.current_version = (
                    graph.current_revision,
                    graph.current_version,
                )
            elif (
                graph.current_version == page.current_version
                and graph.current_revision != page.current_revision
            ):
                raise unavailable(
                    "canonical_version_conflict", "Canonical current version is inconsistent"
                )
            row = await session.get(Revision, graph.revision_id)
            if row and (
                row.page_id != graph.page_id
                or row.version != graph.version
                or row.graph["content_hash"] != graph.content_hash
            ):
                raise unavailable(
                    "canonical_version_conflict", "Immutable canonical revision changed"
                )
            if row and row.acl_epoch > graph.acl_epoch:
                if event_id:
                    session.add(
                        Delivery(
                            event_id=event_id,
                            page_id=row.page_id,
                            revision_id=row.revision_id,
                            generation=row.generation,
                        )
                    )
                return "older_policy_skipped"
            if (
                row
                and row.policy_fingerprint == graph.policy_fingerprint
                and exists
                and await exists(row.projection_id, graph)
            ):
                row.checked_at, row.failed_attempts, row.failure_code = now(), 0, None
                row.acl_epoch, row.policies = graph.acl_epoch, graph.policies
                row.is_current = graph.revision_id == page.current_revision
                previous = (
                    await session.scalars(
                        select(Revision).where(
                            Revision.page_id == page.page_id,
                            Revision.is_current.is_(True),
                            Revision.revision_id != page.current_revision,
                        )
                    )
                ).all()
                for old in previous:
                    old.is_current = False
                if event_id:
                    session.add(
                        Delivery(
                            event_id=event_id,
                            page_id=graph.page_id,
                            revision_id=graph.revision_id,
                            generation=row.generation,
                        )
                    )
                return "projection_unchanged"
            generation = await session.scalar(
                text("SELECT nextval('graphiti_projection_generation')")
            )
            graph = graph.model_copy(
                update={"is_current": graph.revision_id == page.current_revision}
            )
            # The Graph commit may succeed after our PG transaction rolls back. Its new physical
            # generation remains unreachable unless this exact catalog row commits afterwards.
            projection_id = await write(graph, generation)
            if not isinstance(projection_id, str) or not projection_id:
                raise unavailable(
                    "invalid_projection", "Graph writer did not confirm projection identity"
                )
            previous = (
                await session.scalars(
                    select(Revision).where(
                        Revision.page_id == page.page_id,
                        Revision.is_current.is_(True),
                        Revision.revision_id != page.current_revision,
                    )
                )
            ).all()
            for old in previous:
                old.is_current = False
            values = {
                "page_id": graph.page_id,
                "version": graph.version,
                "space_id": graph.space_id,
                "acl_domain": graph.acl_domain,
                "policy_fingerprint": graph.policy_fingerprint,
                "acl_epoch": graph.acl_epoch,
                "policies": graph.policies,
                "graph": graph.model_dump(mode="json"),
                "metadata_json": graph.model_dump(
                    mode="json", exclude={"nodes", "edges", "evidence"}
                ),
                "fragment_ids": sorted({f for n in graph.nodes for f in n.fragment_ids}),
                "is_current": graph.is_current,
                "generation": generation,
                "projection_id": projection_id,
                "checked_at": now(),
                "failure_code": None,
                "failed_attempts": 0,
            }
            if row is None:
                session.add(Revision(revision_id=graph.revision_id, **values))
            else:
                for key, value in values.items():
                    setattr(row, key, value)
            if event_id:
                session.add(
                    Delivery(
                        event_id=event_id,
                        page_id=graph.page_id,
                        revision_id=graph.revision_id,
                        generation=generation,
                    )
                )
            await session.flush()
            return "projected"
