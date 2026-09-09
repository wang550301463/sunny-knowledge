"""Authorized Graphiti projection consumer.

Recovered 2026-09-09: the body below is the genuine worker recovered from the
Codex session stdout (the assembler had interleaved it with retrieval/worker
content — this file now takes only the graphiti-owned fragment, verified
against the Catalog/Neo4jGraph/Clients method contracts).
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import UTC, datetime, timedelta
from urllib.parse import quote, urlencode

from fastapi import HTTPException
from sqlalchemy.exc import SQLAlchemyError

from knowledge_platform.common.auth import HTTPAuthorizer
from knowledge_platform.common.db import create_database

from .authorization import AuthorizationGuard
from .backend import Neo4jGraph
from .clients import Clients, MachineTokens
from .config import GraphitiSettings
from .models import initialize
from .projection import compile_graph, digest
from .schemas import GraphError, TraverseRequest, unavailable
from .store import Catalog

logger = logging.getLogger(__name__)


class ProjectionWorker:
    def __init__(self, settings, authorizer, catalog, backend, clients, tokens):
        self.settings, self.auth, self.catalog, self.backend, self.clients, self.tokens = (
            settings,
            authorizer,
            catalog,
            backend,
            clients,
            tokens,
        )

    async def project(self, page_id, revision_id, event_id=None, *, force=False):
        async with asyncio.timeout(self.settings.graphiti_worker_timeout_seconds):
            value = await self.clients.projection(page_id, revision_id)
            if value.get("page_id") != page_id or value.get("revision_id") != revision_id:
                raise unavailable(
                    "invalid_projection", "Canonical projection identity is inconsistent"
                )
            graph = compile_graph(value)
            token = await self.tokens.token()
            guard = await AuthorizationGuard.begin(self.auth, token, [graph.space_id])
            guard.epoch(graph.acl_epoch)
            instant = datetime.now(UTC)
            if graph.valid_from is not None and instant < graph.valid_from:
                instant = graph.valid_from
            if graph.valid_until is not None and instant >= graph.valid_until:
                instant = graph.valid_until - timedelta(microseconds=1)
            request = TraverseRequest(
                space_ids=[graph.space_id],
                seed_fragment_ids=["projection"],
                include_historical=True,
                as_of=instant,
            )

            if await self.clients.pages(
                token,
                [{"projection_id": "candidate", "graph": graph}],
                request,
                guard,
                inspection=True,
            ) != {"candidate"}:
                raise unavailable(
                    "worker_authorization_required",
                    "Graph service account cannot read this revision",
                )
            await guard.finish()

            async def authorized_write(compiled, generation):
                records = [{"projection_id": "candidate", "graph": compiled}]
                if await self.clients.pages(token, records, request, guard, inspection=True) != {
                    "candidate"
                }:
                    raise unavailable(
                        "worker_authorization_required",
                        "Graph service account cannot read this revision",
                    )
                await guard.finish()
                projection_id = await self.backend.write(compiled, generation)
                if await self.clients.pages(token, records, request, guard, inspection=True) != {
                    "candidate"
                }:
                    raise unavailable(
                        "worker_authorization_required",
                        "Graph service account lost revision access",
                    )
                await guard.finish()
                return projection_id

            return await self.catalog.project(
                graph,
                authorized_write,
                event_id=event_id,
                exists=None if force else self.backend.exists,
            )

    async def once(self):
        leased = await self.clients.call(
            "POST",
            "/internal/v1/outbox/lease",
            "knowledge",
            body={"consumer": "graphiti", "limit": 1, "lease_seconds": 300},
        )
        try:
            items = leased["items"]
            if not isinstance(items, list) or len(items) > 1:
                raise ValueError
            for event in items:
                await self.project(event["page_id"], event["revision_id"], event["id"])
                await self.clients.call(
                    "POST",
                    f"/internal/v1/outbox/{quote(event['id'], safe='')}/ack",
                    "knowledge",
                    body={"lease_token": event["lease_token"]},
                )
        except (KeyError, TypeError, ValueError):
            raise unavailable(
                "invalid_outbox_response", "Knowledge outbox response is invalid"
            ) from None
        return bool(items)

    async def rebuild(self):
        key = digest([self.settings.graphiti_namespace, self.settings.graphiti_neo4j_database])
        async with self.catalog.rebuild_lock(key):
            state = await self.catalog.rebuild_state(key)
            cursor = state["cursor"]
            seen = set()
            while True:
                parameters = {"limit": 100}
                if cursor:
                    parameters["cursor"] = cursor
                page = await self.clients.call(
                    "GET",
                    "/internal/v1/projections/revisions?" + urlencode(parameters),
                    "knowledge",
                )
                try:
                    items = page["items"]
                    next_cursor = page.get("next_cursor")
                    if (
                        not isinstance(items, list)
                        or len(items) > 100
                        or (
                            next_cursor is not None
                            and (
                                not isinstance(next_cursor, str)
                                or not next_cursor
                                or len(next_cursor) > 4096
                            )
                        )
                    ):
                        raise ValueError
                    if len({(r["page_id"], r["revision_id"]) for r in items}) != len(items):
                        raise ValueError
                    for row in items:
                        if (
                            type(row["version"]) is not int
                            or row["version"] < 1
                            or any(
                                not isinstance(row[k], str) or not row[k]
                                for k in ("page_id", "revision_id")
                            )
                        ):
                            raise ValueError
                    if next_cursor and (not items or next_cursor == cursor or next_cursor in seen):
                        raise ValueError
                except (KeyError, TypeError, ValueError):
                    raise unavailable(
                        "invalid_rebuild_response", "Canonical revision enumeration is invalid"
                    ) from None
                for row in items:
                    await self.project(row["page_id"], row["revision_id"], force=True)
                await self.catalog.rebuild_checkpoint(
                    key, next_cursor, len(items), next_cursor is None
                )
                if next_cursor is None:
                    return
                seen.add(next_cursor)
                cursor = next_cursor

    async def reconcile(self):
        cutoff = datetime.now(UTC) - timedelta(seconds=self.settings.graphiti_reconcile_seconds)
        for row in await self.catalog.due(cutoff, limit=20):
            try:
                await self.project(row["page_id"], row["revision_id"])
            except (GraphError, HTTPException, SQLAlchemyError, TimeoutError) as error:
                code = error.code if isinstance(error, GraphError) else "dependency_unavailable"
                await self.catalog.defer(row["revision_id"], code)


async def run(*, rebuild=False):
    settings = GraphitiSettings()
    database = create_database(settings.database_url)
    auth = HTTPAuthorizer(settings)
    backend = Neo4jGraph(settings)
    tokens = MachineTokens(settings)
    await initialize(database.engine)
    worker = ProjectionWorker(
        settings, auth, Catalog(database), backend, Clients(settings, auth), tokens
    )
    try:
        if rebuild:
            await backend.initialize()
            await worker.rebuild()
            return
        initialized = False
        while True:
            try:
                if not initialized:
                    await backend.initialize()
                    initialized = True
                busy = await worker.once()
                await worker.reconcile()
                if not busy:
                    await asyncio.sleep(settings.graphiti_poll_seconds)
            except (GraphError, HTTPException, SQLAlchemyError, TimeoutError):
                logger.warning("Graph projection attempt failed; delivery remains retryable")
                await asyncio.sleep(settings.graphiti_poll_seconds)
    finally:
        await tokens.close()
        await backend.close()
        await auth.close()
        await database.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Authorized Graphiti projection consumer")
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Resume or start canonical revision enumeration, then exit",
    )
    arguments = parser.parse_args()
    try:
        asyncio.run(run(rebuild=arguments.rebuild))
    except (GraphError, HTTPException, SQLAlchemyError, TimeoutError):
        logger.error("Graph worker failed; consult service readiness and private projection status")
        raise SystemExit(1) from None
