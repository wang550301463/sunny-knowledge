"""One durable, fenced pipeline operation per activity. No user bearer in workflow state."""
from __future__ import annotations

import asyncio
import hashlib
from dataclasses import asdict
from urllib.parse import quote

from fastapi import HTTPException
from sqlalchemy import select, func

from .analyzers import SourceFile, SourceSnapshot
from .compiler import KnowledgeCompiler, StaticLanguageAnalyzer, evidence, COMPILER_VERSION, SCHEMA_VERSION
from .models import Source, Preview, Task, Step, Registration, SourcePage, now
from .schemas import IngestError
from .service import IngestService

MANIFEST_PATH = '.__knowledge__/manifest.json'
TERMINAL = {'succeeded', 'review_needed', 'superseded', 'failed'}


class Pipeline:
    def __init__(self, database, authorizer, machine, internal, secret_box, storage):
        self.db, self.auth, self.machine = database, authorizer, machine
        self.internal, self.box, self.storage = internal, secret_box, storage
        self.compiler, self.analyzer = KnowledgeCompiler(), StaticLanguageAnalyzer()

    async def advance(self, task_id: str) -> bool:
        try:
            async with self.db.session() as session, session.begin():
                # Consistent lock order: source then task. API version mutations lock source too.
                initial = await session.get(Task, task_id)
                if initial is None: return True
                service = IngestService(session, self.auth, self.machine, self.internal, self.box)
                source = await service.source(initial.source_id, lock=True)
                task = await session.scalar(select(Task).where(Task.id == task_id).with_for_update().execution_options(populate_existing=True))
                if task.status in TERMINAL: return True
                if source.version != task.source_version or (source.state == 'deleted' and task.operation != 'delete'):
                    task.status = task.stage = 'superseded'
                    task.updated_at = now()
                    return True
                token, actor = await service.worker(source.space_id, source.resource_id)
                task.status, task.updated_at = 'running', now()
                if task.stage == 'queued':
                    task.stage = 'plan' if task.operation == 'delete' else 'register'
                    task.checkpoint = {'registered': 0}
                    service.audit(source, actor, 'task.started', {'version': task.source_version}, task.id)
                    return False
                if task.stage == 'register':
                    await self.register_one(session, source, task, token)
                    return False
                if task.stage == 'plan':
                    await self.plan(session, source, task)
                    return False
                if task.stage == 'publish':
                    step = await session.scalar(select(Step).where(Step.task_id == task.id, Step.status == 'pending').order_by(Step.number).limit(1))
                    if step:
                        await self.publish_one(session, service, source, task, step, token, actor)
                        return False
                    steps = (await session.scalars(select(Step).where(Step.task_id == task.id).order_by(Step.number))).all()
                    review = any(s.status == 'review_needed' for s in steps)
                    task.status = 'review_needed' if review else 'succeeded'
                    task.stage = 'completed'
                    task.result = {**task.result, 'items': [{'page_id': s.page_id, 'path': s.path, 'status': s.status, **s.result} for s in steps]}
                    service.audit(source, actor, 'task.completed', {'status': task.status}, task.id)
                    return True
                raise IngestError(503, 'invalid_task_state', 'Task has an invalid durable stage')
        except (IngestError, HTTPException) as error:
            status = error.status if isinstance(error, IngestError) else error.status_code
            if status >= 500:
                # Dependency failure retries the same durable step; no payload enters Temporal.
                raise IngestError(503, 'dependency_unavailable', 'Pipeline dependency unavailable') from None
            code = error.code if isinstance(error, IngestError) else 'worker_authorization_required'
            async with self.db.session() as session, session.begin():
                task = await session.get(Task, task_id, with_for_update=True)
                if task and task.status not in TERMINAL:
                    task.status, task.stage, task.error_code, task.updated_at = 'failed', 'failed', code, now()
            return True

    async def register_one(self, session, source, task, token):
        preview = await session.get(Preview, (source.id, task.source_version))
        if preview is None:
            raise IngestError(409, 'preview_required', 'Immutable source preview missing')
        manifest = preview.manifest
        index = task.checkpoint['registered']
        files = manifest['files']
        if index > len(files):
            task.stage = 'plan'
            return
        if index == len(files):
            file = {'path': MANIFEST_PATH, 'kind': 'source_manifest', **manifest['canonical_manifest']}
        else:
            file = files[index]
        if file['kind'] != 'raw':
            data = await asyncio.to_thread(self.storage.get_bytes, file['object_key'], file['sha256'], file['size'])
            body = {'source_id': source.id, 'source_revision': manifest['source_revision'],
                    'resource_id': source.resource_id, 'space_id': source.space_id,
                    'path': file['path'], 'kind': file['kind'], 'text': data.decode('utf-8'),
                    'sha256': file['sha256'], 'object_key': file['object_key']}
            snapshot = await self.internal.request('POST', '/internal/v1/sources/snapshots', token, body)
            old = await session.get(Registration, (source.id, task.source_version, file['path']))
            if old is None:
                session.add(Registration(source_id=source.id, version=task.source_version, path=file['path'], snapshot=snapshot))
            elif old.snapshot != snapshot:
                raise IngestError(409, 'snapshot_conflict', 'Canonical immutable snapshot changed')
        task.checkpoint = {**task.checkpoint, 'registered': index + 1}

    async def plan(self, session, source, task):
        registrations = (await session.scalars(select(Registration).where(Registration.source_id == source.id, Registration.version == task.source_version))).all()
        snapshots = {r.path: r.snapshot for r in registrations if r.path != MANIFEST_PATH}
        plans, diagnostics = [], []
        if task.operation == 'sync':
            preview = await session.get(Preview, (source.id, task.source_version))
            # A source's own immutable registrations provide analysis bytes; no repository code executes.
            inputs = tuple(SourceFile(path, snap['text']) for path, snap in snapshots.items() if snap['kind'] == 'code')
            result = await asyncio.to_thread(self.analyzer.analyze, SourceSnapshot(source.id, preview.manifest['source_revision'], inputs))
            diagnostics = [asdict(d) for d in result.diagnostics]
            diagnostics += [{'path': f['path'], 'code': d} for f in preview.manifest['files'] for d in f['diagnostics']]
            for page in self.compiler.code_pages(result, snapshots):
                plans.append(('code', page, {}))
            for snapshot in snapshots.values():
                if snapshot['kind'] not in {'markdown', 'ticket'}: continue
                validity = self.compiler.validity_ticket(snapshot)
                if validity:
                    plans.append(('validity', {'page_id': validity['page_id'], 'path': snapshot['path']}, validity['body']))
                else:
                    plans.append(('proposal', self.compiler.narrative_page(source.id, snapshot), {}))
        prior_pages = (await session.scalars(select(SourcePage).where(SourcePage.source_id == source.id, SourcePage.state == 'active'))).all()
        existing_paths = set(snapshots)
        for old in prior_pages:
            if task.operation == 'delete' or old.path not in existing_paths:
                kind, body = 'retire', {}
                before = await session.get(Registration, (source.id, old.source_version, MANIFEST_PATH))
                after = await session.get(Registration, (source.id, task.source_version, MANIFEST_PATH))
                # Raw-only replacement remains present in a complete manifest and is not deletion.
                after_paths = []
                if task.operation == 'sync':
                    after_paths = [f['path'] for f in preview.manifest['files']]
                if task.operation == 'sync' and old.path in after_paths:
                    diagnostics.append({'code': 'source_no_longer_analyzable', 'path': old.path})
                    continue
                if task.operation == 'sync' and before and after:
                    kind = 'deletion'
                    body = {'before_manifest': evidence(before.snapshot), 'after_manifest': evidence(after.snapshot),
                            'deleted_paths': [old.path], 'reason': 'Path absent from complete immutable source snapshot'}
                plans.append((kind, {'page_id': old.page_id, 'path': old.path}, body))
        for index, (kind, page, body) in enumerate(plans):
            session.add(Step(task_id=task.id, number=index, kind=kind, page_id=page['page_id'], path=page['path'],
                             request={**body, **({'content': page['content']} if 'content' in page else {})}))
        task.result = {'diagnostics': diagnostics[:1000], 'diagnostics_truncated': len(diagnostics) > 1000}
        task.stage = 'publish'

    async def publish_one(self, session, service, source, task, step, token, actor):
        path = '/api/v1/pages/' + quote(step.page_id, safe='')
        key = hashlib.sha256(f'{task.identity}:{step.number}'.encode()).hexdigest()
        if not step.request.get('_base_captured'):
            current = await self.internal.request('GET', path, token, allow_missing=True)
            base = current['current_revision'] if current else None
            # Structured validity tickets already pin their own exact target base revision.
            request = {**step.request, '_base_captured': True, '_exists': current is not None,
                       'base_revision': step.request.get('base_revision', base)}
            if step.kind == 'retire':
                if not current or not current.get('revision'):
                    step.status, step.result = 'review_needed', {'error_code': 'unpublished_source_retirement'}
                    return
                content = {**current['revision']['content'], 'state': 'stale'}
                request['content'] = content
            step.request = request
            return
        body = {k: v for k, v in step.request.items() if not k.startswith('_')}
        try:
            if step.kind == 'code':
                refs = body['content']['evidence'] + [r for c in body['content']['claims'] for r in c['evidence']]
                request = {**body, 'page_id': step.page_id, 'space_id': source.space_id,
                    'proof': {'compiler_version': COMPILER_VERSION, 'schema_version': SCHEMA_VERSION,
                              'snapshot_ids': sorted({r['revision_id'] for r in refs}), 'idempotency_key': key}}
                result = await self.internal.request('POST', '/internal/v1/pages/publish', token, request)
                step.status, step.result = 'published', {'revision_id': result['id']}
            elif step.kind in {'validity', 'deletion'}:
                suffix = 'validity' if step.kind == 'validity' else 'source-deletion'
                result = await self.internal.request('POST', '/internal/v1/pages/' + quote(step.page_id, safe='') + '/' + suffix, token, body)
                step.status, step.result = 'published', {'revision_id': result['id']}
            else:
                reason = 'Source retirement requires review' if step.kind == 'retire' else 'Imported source requires review'
                if step.request['_exists']:
                    result = await self.internal.request('POST', path + '/proposals', token, {**body, 'reason': reason})
                    proposal_id = result['id']
                else:
                    result = await self.internal.request('POST', '/api/v1/pages', token,
                        {'id': step.page_id, 'space_id': source.space_id, 'content': body['content'], 'reason': reason})
                    proposal_id = result['proposal']['id']
                step.status, step.result = 'review_needed', {'proposal_id': proposal_id}
            mapped = await session.get(SourcePage, (source.id, step.page_id))
            if step.kind != 'validity':
                if mapped is None:
                    mapped = SourcePage(source_id=source.id, page_id=step.page_id, path=step.path, source_version=task.source_version)
                    session.add(mapped)
                mapped.source_version = task.source_version
                mapped.state = 'pending_invalidation' if step.kind == 'retire' else 'stale' if step.kind == 'deletion' else 'active'
            service.audit(source, actor, 'task.step_completed', {'step': step.number, 'kind': step.kind, 'status': step.status}, task.id)
        except IngestError as error:
            if error.status == 409:
                # Never refresh base_revision or force publication. Expose the durable frozen
                # proposal through the task for human resolution.
                step.status, step.result = 'review_needed', {'error_code': error.code}
                service.audit(source, actor, 'task.review_required', {'step': step.number, 'error_code': error.code}, task.id)
            else:
                raise