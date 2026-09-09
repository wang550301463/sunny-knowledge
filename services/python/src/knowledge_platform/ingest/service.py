"""Request-scoped coherent authorization, source CAS and durable task scheduling."""
from __future__ import annotations

import hashlib
import json
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select, update

from .compiler import COMPILER_VERSION, SCHEMA_VERSION
from .models import Source, SourceVersion, Preview, Task, Audit, now
from .schemas import IngestError, validate_config


class IngestService:
    def __init__(self, session, authorizer, machine, internal, secret_box):
        self.session, self.auth, self.machine = session, authorizer, machine
        self.internal, self.box = internal, secret_box
        self.epoch = None

    def coherent(self, epoch):
        if self.epoch is not None and epoch != self.epoch:
            raise IngestError(503, 'authorization_changed', 'Authorization changed; retry the operation')
        self.epoch = epoch

    async def actor(self, token):
        principal = await self.auth.resolve(token)
        self.coherent(principal.auth_epoch)
        return principal

    async def allowed(self, token, action, space_id, resource_id=None):
        result = await self.auth.authorize(token, action, space_id, resource_id)
        self.coherent(result.auth_epoch)
        return result.allowed

    async def authorize(self, token, source, write=False):
        actor = await self.actor(token)
        for action in ('read', 'write') if write else ('read',):
            if not await self.allowed(token, action, source.space_id, source.resource_id):
                raise IngestError(403, 'forbidden', 'Source access denied')
        return actor

    async def worker(self, space_id, resource_id=None):
        token = await self.machine.token()
        try:
            actor = await self.actor(token)
            for action in ('read', 'write'):
                if not await self.allowed(token, action, space_id, resource_id):
                    raise IngestError(403, 'worker_authorization_required', 'Grant the ingest service account read/write access to this source')
        except HTTPException as error:
            if error.status_code in {401, 403}:
                raise IngestError(403, 'worker_authorization_required', 'Ingest service account is disabled or unauthorized') from None
            raise
        if not any(s.startswith('service:') for s in actor.subjects):
            raise IngestError(403, 'worker_authorization_required', 'Ingest credentials must resolve to a registered service account')
        return token, actor

    async def source(self, source_id, lock=False):
        stmt = select(Source).where(Source.id == source_id)
        if lock: stmt = stmt.with_for_update()
        source = await self.session.scalar(stmt)
        if source is None: raise IngestError(404, 'not_found', 'Source not found')
        return source

    def audit(self, source, actor, action, detail=None, task_id=None):
        self.session.add(Audit(source_id=source.id, task_id=task_id, actor_id=actor.id,
                               action=action, detail=detail or {}))

    async def source_wire(self, source):
        version = await self.session.get(SourceVersion, (source.id, source.version))
        preview = await self.session.get(Preview, (source.id, source.version))
        return {'id': source.id, 'name': source.name, 'space_id': source.space_id,
                'resource_id': source.resource_id, 'kind': source.kind, 'version': source.version,
                'config': {k: v for k, v in version.config.items() if k != 'content'},
                'has_credential': bool(version.credential_ciphertext), 'state': source.state,
                'created_by': source.created_by, 'created_at': source.created_at.isoformat(),
                'updated_at': source.updated_at.isoformat(), 'latest_task_id': source.latest_task_id,
                'auto_sync': source.auto_sync,
                'auto_sync_interval_seconds': source.auto_sync_interval_seconds,
                'last_auto_sync_at': source.last_auto_sync_at.isoformat() if source.last_auto_sync_at else None,
                'last_seen_revision': source.last_seen_revision,
                'preview': {k: preview.manifest[k] for k in ('source_revision', 'file_count', 'total_bytes')} if preview else None}

    async def create(self, token, body):
        actor = await self.actor(token)
        for action in ('read', 'write'):
            if not await self.allowed(token, action, body.space_id):
                raise IngestError(403, 'forbidden', 'Space access denied')
        await self.worker(body.space_id)
        source_id = str(uuid4())
        resource_id = 'source:' + source_id
        await self.internal.register_resource(token, body.space_id, resource_id)
        # Resource registration legitimately advances IAM epoch; discard all preflight decisions.
        self.epoch = None
        source = Source(id=source_id, name=body.name, space_id=body.space_id, resource_id=resource_id,
                        kind=body.kind, version=1, state='active', created_by=actor.id,
                        auto_sync=body.auto_sync, auto_sync_interval_seconds=body.auto_sync_interval_seconds)
        actor = await self.authorize(token, source, write=True)
        await self.worker(source.space_id, source.resource_id)
        credential = self.box.encrypt(json.dumps(body.credential.plain()), f'{source_id}:1') if body.credential else None
        self.session.add(source)
        await self.session.flush()
        self.session.add(SourceVersion(source_id=source_id, version=1, config=body.config,
                                       credential_ciphertext=credential, created_by=actor.id))
        self.audit(source, actor, 'source.created', {'version': 1})
        await self.session.flush()
        return await self.source_wire(source)

    async def get(self, token, source_id):
        source = await self.source(source_id)
        await self.authorize(token, source)
        return await self.source_wire(source)

    @staticmethod
    def version_matches(source, version, active=True):
        if source.version != version:
            raise IngestError(409, 'version_conflict', 'Source version changed; reload and retry')
        if active and source.state != 'active':
            raise IngestError(409, 'source_deleted', 'Source is deleted')

    async def update(self, token, source_id, body):
        source = await self.source(source_id, lock=True)
        actor = await self.authorize(token, source, write=True)
        await self.worker(source.space_id, source.resource_id)
        self.version_matches(source, body.base_version)
        try: validate_config(source.kind, body.config)
        except ValueError: raise IngestError(422, 'invalid_source', 'Invalid source configuration') from None
        old = await self.session.get(SourceVersion, (source.id, source.version))
        plaintext = self.box.decrypt(old.credential_ciphertext, f'{source.id}:{source.version}') if old.credential_ciphertext else None
        if 'credential' in body.model_fields_set:
            plaintext = json.dumps(body.credential.plain()) if body.credential else None
        if source.kind not in ('git', 'oss') and plaintext:
            raise IngestError(422, 'invalid_source', 'Only Git and OSS sources accept credentials')
        source.version += 1
        source.name, source.updated_at = body.name, now()
        source.auto_sync = body.auto_sync
        source.auto_sync_interval_seconds = body.auto_sync_interval_seconds
        self.session.add(SourceVersion(source_id=source.id, version=source.version, config=body.config,
            credential_ciphertext=self.box.encrypt(plaintext, f'{source.id}:{source.version}') if plaintext else None,
            created_by=actor.id))
        await self.fence(source)
        self.audit(source, actor, 'source.updated', {'version': source.version})
        await self.session.flush()
        return await self.source_wire(source)

    async def fence(self, source):
        await self.session.execute(update(Task).where(Task.source_id == source.id,
            Task.source_version < source.version, Task.status.in_(['queued', 'running'])).values(
                status='superseded', stage='superseded', updated_at=now()))

    async def preview_input(self, token, source_id, version):
        source = await self.source(source_id)
        await self.authorize(token, source, write=True)
        await self.worker(source.space_id, source.resource_id)
        self.version_matches(source, version)
        old = await self.session.get(Preview, (source_id, version))
        if old: return {'preview': self.preview_wire(old)}
        row = await self.session.get(SourceVersion, (source_id, version))
        credential = json.loads(self.box.decrypt(row.credential_ciphertext, f'{source.id}:{version}')) if row.credential_ciphertext else None
        return {'kind': source.kind, 'config': row.config, 'credential': credential}

    @staticmethod
    def preview_wire(preview):
        manifest = preview.manifest
        return {'source_id': preview.source_id, 'version': preview.version,
                **{k: manifest[k] for k in ('source_revision', 'diagnostics', 'file_count', 'total_bytes')},
                'files': [{k: v for k, v in f.items() if k != 'object_key'} for f in manifest['files']]}

    async def save_preview(self, token, source_id, version, manifest_key, manifest):
        source = await self.source(source_id, lock=True)
        actor = await self.authorize(token, source, write=True)
        await self.worker(source.space_id, source.resource_id)
        self.version_matches(source, version)
        old = await self.session.get(Preview, (source_id, version))
        if old:
            if old.manifest != manifest or old.manifest_key != manifest_key:
                raise IngestError(409, 'snapshot_conflict', 'Source version already pins a different immutable snapshot')
            return self.preview_wire(old)
        row = Preview(source_id=source_id, version=version, manifest_key=manifest_key, manifest=manifest)
        self.session.add(row)
        self.audit(source, actor, 'source.previewed', {'version': version, 'source_revision': manifest['source_revision']})
        await self.session.flush()
        return self.preview_wire(row)

    @staticmethod
    def task_wire(task):
        return {k: getattr(task, k) for k in ('id', 'source_id', 'source_version', 'space_id',
            'operation', 'status', 'stage', 'error_code', 'result')} | {
                'created_at': task.created_at.isoformat(), 'updated_at': task.updated_at.isoformat()}

    async def new_task(self, source, actor, operation):
        identity = hashlib.sha256(json.dumps([source.id, source.version, operation,
                        COMPILER_VERSION, SCHEMA_VERSION], separators=(',', ':')).encode()).hexdigest()
        old = await self.session.scalar(select(Task).where(Task.identity == identity))
        if old: return self.task_wire(old)
        task = Task(id=str(uuid4()), source_id=source.id, source_version=source.version,
                    space_id=source.space_id, operation=operation, identity=identity, created_by=actor.id)
        self.session.add(task)
        source.latest_task_id, source.updated_at = task.id, now()
        self.audit(source, actor, 'task.queued', {'version': source.version, 'operation': operation}, task.id)
        await self.session.flush()
        return self.task_wire(task)

    async def sync(self, token, source_id, version):
        source = await self.source(source_id, lock=True)
        actor = await self.authorize(token, source, write=True)
        await self.worker(source.space_id, source.resource_id)
        self.version_matches(source, version)
        if not await self.session.get(Preview, (source_id, version)):
            raise IngestError(409, 'preview_required', 'Preview this source version before syncing')
        return await self.new_task(source, actor, 'sync')

    async def delete(self, token, source_id, version):
        source = await self.source(source_id, lock=True)
        actor = await self.authorize(token, source, write=True)
        await self.worker(source.space_id, source.resource_id)
        if source.state == 'deleted' and source.version == version + 1:
            return await self.new_task(source, actor, 'delete')
        self.version_matches(source, version)
        prior = await self.session.get(SourceVersion, (source_id, version))
        source.version += 1
        source.state = 'deleted'
        self.session.add(SourceVersion(source_id=source_id, version=source.version, config=prior.config,
                                       credential_ciphertext=None, created_by=actor.id))
        await self.fence(source)
        self.audit(source, actor, 'source.deleted', {'version': source.version})
        return await self.new_task(source, actor, 'delete')

    async def list_sources(self, token, space_id, cursor, limit):
        await self.actor(token)
        stmt = select(Source).order_by(Source.id)
        if space_id: stmt = stmt.where(Source.space_id == space_id)
        if cursor: stmt = stmt.where(Source.id > cursor)
        items = []
        # Fetch in bounded pages; denied rows never contribute to a returned count/cursor.
        while True:
            rows = (await self.session.scalars(stmt.limit(100))).all()
            if not rows: break
            for source in rows:
                if await self.allowed(token, 'read', source.space_id, source.resource_id):
                    items.append(await self.source_wire(source))
                    if len(items) > limit:
                        return {'items': items[:limit], 'next_cursor': items[limit - 1]['id']}
            stmt = stmt.where(Source.id > rows[-1].id)
        return {'items': items, 'next_cursor': None}

    async def get_task(self, token, task_id):
        task = await self.session.get(Task, task_id)
        if not task: raise IngestError(404, 'not_found', 'Task not found')
        await self.authorize(token, await self.source(task.source_id))
        return self.task_wire(task)

    async def list_tasks(self, token, space_id, source_id, cursor, limit):
        await self.actor(token)
        stmt = select(Task, Source).join(Source, Source.id == Task.source_id).order_by(Task.id)
        if space_id: stmt = stmt.where(Task.space_id == space_id)
        if source_id: stmt = stmt.where(Task.source_id == source_id)
        if cursor: stmt = stmt.where(Task.id > cursor)
        items = []
        while True:
            rows = (await self.session.execute(stmt.limit(100))).all()
            if not rows: break
            for task, source in rows:
                if await self.allowed(token, 'read', source.space_id, source.resource_id):
                    items.append(self.task_wire(task))
                    if len(items) > limit:
                        return {'items': items[:limit], 'next_cursor': items[limit - 1]['id']}
            stmt = stmt.where(Task.id > rows[-1][0].id)
        return {'items': items, 'next_cursor': None}