"""Automatic source synchronization engine.

For each active source with auto_sync enabled, periodically capture the
latest remote revision (Git commit / OSS listing digest). When the revision
differs from the last seen one, the engine advances the source to a new
version, pins the captured snapshot as that version's immutable preview, and
queues the same durable 'sync' task the manual flow uses — so every automatic
change follows the identical review/publish/traceability path: Task +
SourceVersion + Preview(manifest) + Registration rows, evidence pointing at
the exact upstream revision.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import timedelta

from sqlalchemy import select

from .connectors import GitConnector, OSSConnector, SnapshotLimits
from .models import Audit, Preview, Source, SourceVersion, Task, now
from .schemas import IngestError

logger = logging.getLogger(__name__)

MAX_SOURCES_PER_TICK = 5


def _connector_for(kind: str, limits: SnapshotLimits):
    if kind == 'git':
        return GitConnector(limits)
    if kind == 'oss':
        return OSSConnector(limits)
    raise IngestError(422, 'invalid_source', 'Automatic synchronization supports Git and OSS sources only')


async def auto_sync_source(state, source_id: str) -> dict:
    """Capture + (if changed) advance one auto-sync source. Returns a summary."""
    database, box, storage, limits = state.database, state.secret_box, state.storage, state.limits
    token = await state.machine.token()

    # Phase 1 — lockless network capture (same pattern as the manual preview).
    async with database.session() as session:
        source = await session.get(Source, source_id)
        if source is None or source.state != 'active' or not source.auto_sync:
            return {'source_id': source_id, 'changed': False, 'reason': 'inactive'}
        kind, version = source.kind, source.version
        row = await session.get(SourceVersion, (source_id, version))
        credential = json.loads(box.decrypt(row.credential_ciphertext, f'{source_id}:{version}')) \
            if row.credential_ciphertext else None
        config = row.config
        space_id, resource_id = source.space_id, source.resource_id

    snapshot = await asyncio.to_thread(_connector_for(kind, limits).capture, config, credential, version)

    # Phase 2 — compare-and-advance under the source row lock.
    async with database.session() as session, session.begin():
        source = await session.get(Source, source_id, with_for_update=True)
        if source is None or source.state != 'active' or not source.auto_sync \
                or source.version != version or source.kind != kind:
            return {'source_id': source_id, 'changed': False, 'reason': 'source_changed'}
        source.last_auto_sync_at = now()
        if source.last_seen_revision == snapshot.revision:
            await session.flush()
            return {'source_id': source_id, 'changed': False, 'reason': 'unchanged',
                    'revision': snapshot.revision}
        key, manifest = await asyncio.to_thread(storage.put_snapshot, source_id, version + 1, snapshot)

        # Service-account authorization for the durable task (mirrors the
        # manual flow's worker() checks: read/write on space and resource).
        service = state.auto_sync_service(session)
        principal = await service.actor(token)
        for action in ('read', 'write'):
            if not await service.allowed(token, action, space_id, resource_id):
                raise IngestError(403, 'worker_authorization_required',
                                  'Grant the ingest service account read/write access to this source')

        new_version = version + 1
        source.version, source.updated_at = new_version, now()
        source.last_seen_revision = snapshot.revision
        session.add(SourceVersion(source_id=source_id, version=new_version, config=config,
                                  credential_ciphertext=None if credential is None else
                                  box.encrypt(json.dumps(credential), f'{source_id}:{new_version}'),
                                  created_by=principal.id))
        # Pin the snapshot as the new version's immutable preview.
        session.add(Preview(source_id=source_id, version=new_version,
                            manifest_key=key, manifest=manifest))
        # Fence any queued/running tasks of superseded versions.
        from sqlalchemy import update
        await session.execute(update(Task).where(Task.source_id == source_id,
            Task.source_version < new_version, Task.status.in_(['queued', 'running'])).values(
                status='superseded', stage='superseded', updated_at=now()))
        session.add(Audit(source_id=source_id, actor_id=principal.id, action='source.auto_synced',
                          detail={'version': new_version, 'source_revision': snapshot.revision}))
        task = await service.new_task(source, principal, 'sync')
        await session.flush()
        return {'source_id': source_id, 'changed': True, 'version': new_version,
                'revision': snapshot.revision, 'task': task}


async def auto_sync_tick(state) -> dict:
    """One scheduler pass over due auto-sync sources."""
    database = state.database
    results = []
    async with database.session() as session:
        moment = now()
        rows = (await session.scalars(
            select(Source).where(Source.auto_sync.is_(True), Source.state == 'active',
                                 Source.kind.in_(['git', 'oss']))
            .order_by(Source.last_auto_sync_at.asc().nulls_first(), Source.id)
            .limit(MAX_SOURCES_PER_TICK))).all()
        due = [s for s in rows if s.last_auto_sync_at is None or
               s.last_auto_sync_at + timedelta(seconds=s.auto_sync_interval_seconds) <= moment]
        due_ids = [s.id for s in due]
    for source_id in due_ids:
        try:
            outcome = await auto_sync_source(state, source_id)
            results.append(outcome)
            if outcome.get('changed'):
                logger.info('auto-sync advanced source %s to version %s (revision %s)',
                            source_id, outcome.get('version'), outcome.get('revision'))
        except Exception as error:  # noqa: BLE001 — a failing source must not stop the tick
            # Surface via audit-free warning; the next tick retries after the interval.
            logger.warning('auto-sync failed for source %s: %s', source_id, error)
            results.append({'source_id': source_id, 'changed': False, 'reason': 'error',
                            'error': str(error) if not isinstance(error, IngestError) else error.message})
            # Advance last_auto_sync_at so a permanently broken remote cannot spin.
            async with database.session() as session, session.begin():
                source = await session.get(Source, source_id)
                if source is not None:
                    source.last_auto_sync_at = now()
    return {'checked': len(results), 'results': results}


async def auto_sync_loop(state, interval_seconds: float):
    """Background scheduler; one tick per interval, resilient to tick errors."""
    while True:
        try:
            await auto_sync_tick(state)
        except Exception:  # noqa: BLE001
            logger.exception('auto-sync tick crashed; retrying next interval')
        await asyncio.sleep(interval_seconds)
