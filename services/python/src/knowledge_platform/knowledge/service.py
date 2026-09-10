"""Canonical publication boundary. Caller supplies one transaction per operation."""
from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from typing import Protocol
from urllib.parse import quote

from sqlalchemy import and_, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Audit, Delivery, Outbox, Page, Proposal, Revision, SourceSnapshot, new_id, now
from .schemas import (
    DeterministicPublish, EvidenceRef, KnowledgeError, LeaseRequest, PageContent,
    PageCreate, ProposalCreate, RollbackRequest, SourceSnapshotCreate, ValidityUpdate,
)


class Authorizer(Protocol):
    async def resolve(self, token: str): ...
    async def require(self, token: str, action: str, space_id: str, resource_id: str | None = None): ...
    async def policy(self, space_id: str, resource_id: str) -> dict: ...
    async def request(self, method: str, path: str, target: str, token: str | None = None, json: dict | None = None): ...


def digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def denied(error: Exception) -> bool:
    return getattr(error, 'status', getattr(error, 'status_code', None)) == 403


def serialize(row) -> dict:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


class KnowledgeService:
    def __init__(self, session: AsyncSession, authorizer: Authorizer):
        self.session, self.auth = session, authorizer

    @staticmethod
    def workload(caller: str, allowed: tuple[str, ...]) -> None:
        if caller not in allowed:
            raise KnowledgeError(403, 'forbidden_service', 'Service identity is not allowed for this operation')

    async def _lock(self, key: str) -> None:
        await self.session.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))'), {'key': key})

    async def _actor(self, token: str) -> str:
        return (await self.auth.resolve(token)).id

    async def _register_resource(self, token: str, space_id: str, resource_id: str) -> None:
        await self.auth.require(token, 'read', space_id)
        await self.auth.require(token, 'write', space_id)
        response = await self.auth.request('POST', '/internal/v1/resources', 'iam', json={'space_id': space_id, 'resource_id': resource_id})
        response.raise_for_status()
        await self.auth.require(token, 'read', space_id, resource_id)
        await self.auth.require(token, 'write', space_id, resource_id)

    async def _page(self, page_id: str, lock: bool = False) -> Page:
        if lock:
            await self._lock('page:' + page_id)
        query = select(Page).where(Page.id == page_id).execution_options(populate_existing=True)
        if lock:
            query = query.with_for_update()
        page = await self.session.scalar(query)
        if page is None:
            raise KnowledgeError(404, 'page_not_found', 'Page not found')
        return page

    async def _revision(self, page_id: str, revision_id: str) -> Revision:
        revision = await self.session.scalar(select(Revision).where(Revision.id == revision_id, Revision.page_id == page_id))
        if revision is None:
            raise KnowledgeError(404, 'revision_not_found', 'Revision not found')
        return revision

    async def _page_permission(self, token: str, page: Page, action: str = 'read') -> None:
        await self.auth.require(token, 'read', page.space_id, page.id)
        if action != 'read':
            await self.auth.require(token, action, page.space_id, page.id)

    async def _evidence(self, ref: EvidenceRef, token: str | None = None) -> SourceSnapshot:
        snapshot = await self.session.get(SourceSnapshot, ref.revision_id)
        if snapshot is None:
            raise KnowledgeError(422, 'invalid_evidence', 'Evidence revision is not registered')
        if token is not None:
            await self.auth.require(token, 'read', snapshot.space_id, snapshot.resource_id)
        fields = ('resource_id', 'source_id', 'source_revision', 'path', 'kind')
        if any(getattr(snapshot, key) != getattr(ref, key) for key in fields):
            raise KnowledgeError(422, 'invalid_evidence', 'Evidence does not match its immutable source revision')
        if ref.end_line > len(snapshot.text.splitlines()):
            raise KnowledgeError(422, 'invalid_evidence', 'Evidence line range exceeds source revision')
        return snapshot

    async def _supports(self, token: str | None, content: PageContent) -> list[SourceSnapshot]:
        snapshots = [await self._evidence(ref, token) for ref in content.supports()]
        return list({snapshot.id: snapshot for snapshot in snapshots}.values())

    @staticmethod
    def _cas(page: Page, base_revision: str | None) -> None:
        if page.current_revision != base_revision:
            raise KnowledgeError(409, 'revision_conflict', 'base_revision no longer matches the current revision')

    async def _audit(self, action: str, actor: str, resource_id: str, details: dict) -> None:
        self.session.add(Audit(action=action, actor_id=actor, resource_id=resource_id, details=details))

    async def register_snapshot(self, token: str, caller: str, request: SourceSnapshotCreate) -> dict:
        self.workload(caller, ('ingest',))
        await self._register_resource(token, request.space_id, request.resource_id)
        if hashlib.sha256(request.text.encode()).hexdigest() != request.sha256:
            raise KnowledgeError(422, 'checksum_mismatch', 'Source snapshot checksum does not match exact UTF-8 text')
        await self._lock('source:' + digest({'source_id': request.source_id, 'source_revision': request.source_revision, 'path': request.path}))
        snapshot = await self.session.scalar(select(SourceSnapshot).where(SourceSnapshot.source_id == request.source_id, SourceSnapshot.source_revision == request.source_revision, SourceSnapshot.path == request.path))
        if snapshot is not None:
            if any(getattr(snapshot, key) != value for key, value in request.model_dump().items()):
                raise KnowledgeError(409, 'immutable_source_conflict', 'Source revision already exists with different content or metadata')
            return serialize(snapshot)
        actor = await self._actor(token)
        snapshot = SourceSnapshot(**request.model_dump(), registered_by=actor)
        self.session.add(snapshot)
        await self.session.flush()
        await self._audit('source.registered', actor, snapshot.resource_id, {'snapshot_id': snapshot.id, 'sha256': snapshot.sha256, 'source_revision': snapshot.source_revision})
        return serialize(snapshot)

    async def get_snapshot(self, token: str, snapshot_id: str) -> dict:
        snapshot = await self.session.get(SourceSnapshot, snapshot_id)
        if snapshot is None:
            raise KnowledgeError(404, 'snapshot_not_found', 'Source snapshot not found')
        await self.auth.require(token, 'read', snapshot.space_id, snapshot.resource_id)
        result = serialize(snapshot)
        result.pop('object_key')
        return result

    async def authorize_evidence(self, token: str, evidence) -> dict:
        """Batch evidence authorization for retrieval workers.

        Returns {'decisions': [{evidence: <original ref>, allowed: bool,
        excerpt: str, sha256: str}]} — allowed is true only when the
        snapshot exists and the caller has read access to its space.
        When allowed, excerpt and sha256 come from the source snapshot.
        """
        decisions = []
        for ref in evidence:
            snapshot = await self.session.get(SourceSnapshot, ref.revision_id)
            if snapshot is None:
                decisions.append({'evidence': ref.model_dump(mode='json'), 'allowed': False, 'excerpt': '', 'sha256': ''})
                continue
            try:
                await self.auth.require(token, 'read', snapshot.space_id, snapshot.resource_id)
                allowed = True
            except HTTPException:
                allowed = False
            excerpt = snapshot.text if allowed else ''
            decisions.append({
                'evidence': ref.model_dump(mode='json'), 'allowed': allowed,
                'excerpt': excerpt, 'sha256': snapshot.sha256 if allowed else ''})
        return {'decisions': decisions}

    async def authorize_pages(self, token: str, pages, include_historical: bool, as_of) -> dict:
        """Batch page authorization for graphiti projection workers.

        Returns {'decisions': [{page_id, revision_id, allowed, space_id}]}
        for each requested page — allowed is true only when the caller has
        read access to the page's space and the revision exists.
        """
        decisions = []
        for page_auth in pages:
            page = await self.session.get(Page, page_auth.page_id)
            if page is None:
                decisions.append({
                    'page_id': page_auth.page_id,
                    'revision_id': page_auth.revision_id,
                    'allowed': False,
                    'authorized': False,
                    'space_id': '',
                })
                continue
            try:
                await self.auth.require(token, 'read', page.space_id)
                allowed = True
            except HTTPException:
                allowed = False
            decisions.append({
                'page_id': page_auth.page_id,
                'revision_id': page_auth.revision_id,
                'allowed': allowed,
                'authorized': allowed,
                'space_id': page.space_id,
            })
        return {'decisions': decisions}

    async def source_revision(self, token: str, source_id: str, source_revision: str, path: str | None = None) -> dict:
        query = select(SourceSnapshot).where(SourceSnapshot.source_id == source_id, SourceSnapshot.source_revision == source_revision).order_by(SourceSnapshot.path)
        if path is not None:
            query = query.where(SourceSnapshot.path == path)
        snapshots = (await self.session.scalars(query)).all()
        items = []
        for snapshot in snapshots:
            try:
                items.append(await self.get_snapshot(token, snapshot.id))
            except Exception as error:
                if not denied(error):
                    raise
        return {'items': items}

    async def create_page(self, token: str, request: PageCreate) -> dict:
        page_id = request.id or new_id()
        await self._register_resource(token, request.space_id, page_id)
        await self._supports(token, request.content)
        await self._lock('page:' + page_id)
        if await self.session.get(Page, page_id) is not None:
            raise KnowledgeError(409, 'page_exists', 'Page already exists')
        actor = await self._actor(token)
        page = Page(id=page_id, space_id=request.space_id, created_by=actor)
        self.session.add(page)
        await self.session.flush()
        proposal = await self._proposal(page, actor, ProposalCreate(base_revision=None, content=request.content, reason=request.reason))
        return {'page': serialize(page), 'proposal': serialize(proposal)}

    async def _proposal(self, page: Page, actor: str, request: ProposalCreate) -> Proposal:
        self._cas(page, request.base_revision)
        proposal = Proposal(page_id=page.id, base_revision=request.base_revision, content=request.content.model_dump(mode='json'), kind=request.kind, reason=request.reason, proposed_by=actor)
        self.session.add(proposal)
        await self.session.flush()
        await self._audit('proposal.created', actor, page.id, {'proposal_id': proposal.id, 'kind': request.kind, 'base_revision': request.base_revision})
        return proposal

    async def propose(self, token: str, page_id: str, request: ProposalCreate) -> dict:
        page = await self._page(page_id, lock=True)
        await self._page_permission(token, page, 'write')
        if page.current_revision:
            current = await self._revision(page.id, page.current_revision)
            await self._supports(token, PageContent.model_validate(current.content))
        await self._supports(token, request.content)
        if request.kind == 'digest' and not request.content.supports():
            raise KnowledgeError(422, 'digest_missing_support', 'A digest must retain original source evidence')
        proposal = await self._proposal(page, await self._actor(token), request)
        return serialize(proposal)

    async def get_page(self, token: str, page_id: str) -> dict:
        page = await self._page(page_id)
        await self._page_permission(token, page)
        result = serialize(page)
        result['revision'] = await self.get_revision(token, page.id, page.current_revision) if page.current_revision else None
        return result

    async def get_revision(self, token: str, page_id: str, revision_id: str) -> dict:
        page = await self._page(page_id)
        await self._page_permission(token, page)
        revision = await self._revision(page_id, revision_id)
        await self._supports(token, PageContent.model_validate(revision.content))
        return serialize(revision)

    async def list_pages(self, token: str, space_id: str | None = None, cursor: str | None = None, limit: int = 50) -> dict:
        await self._actor(token)
        query = select(Page).order_by(Page.id)
        if space_id is not None:
            query = query.where(Page.space_id == space_id)
        if cursor:
            query = query.where(Page.id > cursor)
        # Iterate in keyset batches; denied objects never contribute counts or cursors.
        items = []
        last_id = cursor
        while len(items) <= limit:
            batch = (await self.session.scalars(query.limit(100))).all()
            if not batch:
                break
            for page in batch:
                last_id = page.id
                try:
                    result = await self.get_page(token, page.id)
                    if result['revision']:
                        items.append(result)
                except Exception as error:
                    if not denied(error):
                        raise
                if len(items) > limit:
                    break
            query = query.where(Page.id > last_id)
        result = {'items': items[:limit]}
        if len(items) > limit:
            result['next_cursor'] = items[limit - 1]['id']
        return result

    async def list_revisions(self, token: str, page_id: str, cursor: int | None = None, limit: int = 50) -> dict:
        page = await self._page(page_id)
        await self._page_permission(token, page)
        query = select(Revision).where(Revision.page_id == page_id).order_by(Revision.number.desc())
        if cursor is not None:
            query = query.where(Revision.number < cursor)
        items = []
        for revision in (await self.session.scalars(query)).all():
            try:
                await self._supports(token, PageContent.model_validate(revision.content))
                items.append(serialize(revision))
            except Exception as error:
                if not denied(error):
                    raise
            if len(items) > limit:
                break
        result = {'items': items[:limit]}
        if len(items) > limit:
            result['next_cursor'] = str(items[limit - 1]['number'])
        return result

    async def _review(self, token: str, proposal_id: str) -> tuple[Page, Proposal]:
        proposal = await self.session.get(Proposal, proposal_id)
        if proposal is None:
            raise KnowledgeError(404, 'proposal_not_found', 'Proposal not found')
        page = await self._page(proposal.page_id, lock=True)
        proposal = await self.session.scalar(select(Proposal).where(Proposal.id == proposal_id).with_for_update().execution_options(populate_existing=True))
        await self._page_permission(token, page, 'review')
        await self._supports(token, PageContent.model_validate(proposal.content))
        if page.current_revision:
            current = await self._revision(page.id, page.current_revision)
            await self._supports(token, PageContent.model_validate(current.content))
        if proposal.status != 'pending':
            raise KnowledgeError(409, 'review_resolved', 'Proposal has already been reviewed')
        return page, proposal

    async def approve(self, token: str, proposal_id: str, reason: str) -> dict:
        page, proposal = await self._review(token, proposal_id)
        actor = await self._actor(token)
        revision = await self._publish(page, actor, proposal.base_revision, PageContent.model_validate(proposal.content), 'review', {'proposal_id': proposal.id, 'proposal_kind': proposal.kind, 'reason': reason})
        proposal.status, proposal.reviewed_by = 'approved', actor
        proposal.review_reason, proposal.reviewed_at = reason, now()
        proposal.published_revision = revision.id
        await self._audit('proposal.approved', actor, page.id, {'proposal_id': proposal.id, 'revision_id': revision.id, 'reason': reason})
        return serialize(revision)

    async def reject(self, token: str, proposal_id: str, reason: str) -> dict:
        page, proposal = await self._review(token, proposal_id)
        actor = await self._actor(token)
        proposal.status, proposal.reviewed_by = 'rejected', actor
        proposal.review_reason, proposal.reviewed_at = reason, now()
        await self._audit('proposal.rejected', actor, page.id, {'proposal_id': proposal.id, 'reason': reason})
        await self.session.flush()
        return serialize(proposal)

    async def list_reviews(self, token: str, status: str = 'pending', space_id: str | None = None, cursor: str | None = None, limit: int = 50) -> dict:
        await self._actor(token)
        query = select(Proposal, Page).join(Page).where(Proposal.status == status).order_by(Proposal.id)
        if space_id:
            query = query.where(Page.space_id == space_id)
        if cursor:
            query = query.where(Proposal.id > cursor)
        items = []
        for proposal, page in (await self.session.execute(query)).all():
            try:
                await self._page_permission(token, page, 'review')
                await self._supports(token, PageContent.model_validate(proposal.content))
                if page.current_revision:
                    revision = await self._revision(page.id, page.current_revision)
                    await self._supports(token, PageContent.model_validate(revision.content))
                items.append(serialize(proposal) | {'space_id': page.space_id})
            except Exception as error:
                if not denied(error):
                    raise
            if len(items) > limit:
                break
        result = {'items': items[:limit]}
        if len(items) > limit:
            result['next_cursor'] = items[limit - 1]['id']
        return result

    async def _publish(self, page: Page, actor: str, base_revision: str | None, content: PageContent, kind: str, proof: dict | None = None, idempotency_key: str | None = None, request_hash: str | None = None) -> Revision:
        self._cas(page, base_revision)
        revision = Revision(page_id=page.id, number=page.revision_number + 1, base_revision=base_revision, content=content.model_dump(mode='json'), created_by=actor, publication_kind=kind, proof=proof, idempotency_key=idempotency_key, request_hash=request_hash)
        self.session.add(revision)
        await self.session.flush()
        page.current_revision, page.revision_number = revision.id, revision.number
        await self._audit('revision.published', actor, page.id, {'revision_id': revision.id, 'base_revision': base_revision, 'number': revision.number, 'publication_kind': kind})
        outbox = Outbox(page_id=page.id, revision_id=revision.id, version=revision.number, payload={'space_id': page.space_id, 'page_id': page.id, 'revision_id': revision.id, 'version': revision.number})
        self.session.add(outbox)
        await self.session.flush()
        self.session.add_all([Delivery(event_id=outbox.id, consumer=consumer) for consumer in ('retrieval', 'graphiti')])
        await self.session.flush()
        return revision

    async def rollback(self, token: str, page_id: str, request: RollbackRequest) -> dict:
        page = await self._page(page_id, lock=True)
        await self._page_permission(token, page, 'review')
        historical = await self._revision(page_id, request.revision_id)
        content = PageContent.model_validate(historical.content)
        await self._supports(token, content)
        if page.current_revision:
            current = await self._revision(page.id, page.current_revision)
            await self._supports(token, PageContent.model_validate(current.content))
        revision = await self._publish(page, await self._actor(token), request.base_revision, content, 'rollback', {'restored_revision': historical.id, 'reason': request.reason})
        return serialize(revision)

    async def deterministic_publish(self, token: str, caller: str, request: DeterministicPublish) -> dict:
        self.workload(caller, ('ingest',))
        await self._register_resource(token, request.space_id, request.page_id)
        snapshots = await self._supports(token, request.content)
        if not snapshots or set(request.proof.snapshot_ids) != {snapshot.id for snapshot in snapshots}:
            raise KnowledgeError(422, 'invalid_compiler_proof', 'Compiler proof must exactly enumerate the immutable source support')
        if any(snapshot.kind != 'code' for snapshot in snapshots):
            raise KnowledgeError(422, 'review_required', 'Only deterministic code facts may be automatically published')
        code_types = ('Service', 'Module', 'File', 'Dependency', None)
        if request.content.entity_type not in code_types or any(claim.kind != 'fact' or claim.entity_type not in code_types for claim in request.content.claims):
            raise KnowledgeError(422, 'review_required', 'Inferences and formal business conclusions require human review')
        await self._lock('compiler:' + request.proof.idempotency_key)
        request_hash = digest(request.model_dump(mode='json'))
        existing = await self.session.scalar(select(Revision).where(Revision.idempotency_key == request.proof.idempotency_key))
        if existing is not None:
            if existing.request_hash != request_hash:
                raise KnowledgeError(409, 'idempotency_conflict', 'Idempotency key was used for a different compilation')
            return await self.get_revision(token, existing.page_id, existing.id)
        await self._lock('page:' + request.page_id)
        page = await self.session.get(Page, request.page_id, populate_existing=True)
        actor = await self._actor(token)
        if page is None:
            if request.base_revision is not None:
                raise KnowledgeError(409, 'revision_conflict', 'New page must have null base_revision')
            page = Page(id=request.page_id, space_id=request.space_id, created_by=actor)
            self.session.add(page)
            await self.session.flush()
        if page.space_id != request.space_id:
            raise KnowledgeError(409, 'space_conflict', 'Page space cannot be changed')
        await self._page_permission(token, page, 'write')
        if page.current_revision:
            current = await self._revision(page.id, page.current_revision)
            await self._supports(token, PageContent.model_validate(current.content))
            if current.publication_kind not in ('deterministic_code', 'structured_invalidation'):
                raise KnowledgeError(409, 'review_required', 'An automatically compiled change cannot overwrite a reviewed conclusion')
        revision = await self._publish(page, actor, request.base_revision, request.content, 'deterministic_code', request.proof.model_dump(mode='json'), request.proof.idempotency_key, request_hash)
        return serialize(revision)

    async def lease_outbox(self, caller: str, request: LeaseRequest) -> dict:
        self.workload(caller, ('retrieval', 'graphiti'))
        if request.consumer != caller:
            raise KnowledgeError(403, 'wrong_consumer', 'Consumers may only lease their own deliveries')
        timestamp = now()
        query = select(Delivery, Outbox).join(Outbox, Delivery.event_id == Outbox.id).where(Delivery.consumer == caller, Delivery.acked_at.is_(None), or_(Delivery.lease_until.is_(None), Delivery.lease_until <= timestamp)).order_by(Outbox.created_at, Outbox.id).limit(request.limit).with_for_update(skip_locked=True, of=Delivery)
        items = []
        for delivery, event in (await self.session.execute(query)).all():
            delivery.lease_token, delivery.lease_until = new_id(), timestamp + timedelta(seconds=request.lease_seconds)
            delivery.attempts += 1
            items.append(serialize(event) | {'lease_token': delivery.lease_token, 'lease_until': delivery.lease_until, 'attempts': delivery.attempts})
        await self.session.flush()
        return {'items': items}

    async def ack_outbox(self, caller: str, event_id: str, lease_token: str) -> dict:
        self.workload(caller, ('retrieval', 'graphiti'))
        delivery = await self.session.scalar(select(Delivery).where(Delivery.event_id == event_id, Delivery.consumer == caller).with_for_update())
        if delivery is None or delivery.lease_token != lease_token:
            raise KnowledgeError(409, 'lease_conflict', 'Delivery lease is not owned by this worker')
        if delivery.acked_at is not None:
            return {'acked': True}
        if delivery.lease_until is None or delivery.lease_until <= now():
            raise KnowledgeError(409, 'lease_expired', 'Delivery lease has expired')
        delivery.acked_at = now()
        return {'acked': True}

    async def projection(self, caller: str, page_id: str, revision_id: str) -> dict:
        self.workload(caller, ('retrieval', 'graphiti'))
        page = await self._page(page_id)
        revision = await self._revision(page_id, revision_id)
        content = PageContent.model_validate(revision.content)
        snapshots = await self._supports(None, content)
        resources = {(page.space_id, page.id)} | {(snapshot.space_id, snapshot.resource_id) for snapshot in snapshots}
        policies = [await self.auth.policy(space_id, resource_id) for space_id, resource_id in sorted(resources)]
        clauses = []
        for policy in policies:
            if not isinstance(policy.get('space_read_subjects'), list) or 'resource_read_subjects' not in policy:
                raise KnowledgeError(503, 'policy_unavailable', 'Incomplete policy; projection refused')
            clauses.append(sorted(set(policy['space_read_subjects'])))
            if policy['resource_read_subjects'] is not None:
                clauses.append(sorted(set(policy['resource_read_subjects'])))
        normalized = sorted({tuple(clause) for clause in clauses})
        read_clauses = [list(clause) for clause in normalized]
        return {
            'page_id': page.id, 'space_id': page.space_id, 'revision_id': revision.id,
            'version': revision.number, 'current_revision': page.current_revision,
            'current_version': page.revision_number, 'is_current': page.current_revision == revision.id,
            'content': revision.content, 'created_at': revision.created_at,
            'read_clauses': read_clauses,
            'acl_domain': digest({'space_id': page.space_id, 'read_clauses': read_clauses}),
            'policies': policies,
            'source_snapshots': [{key: getattr(snapshot, key) for key in ('id', 'space_id', 'resource_id', 'source_id', 'source_revision', 'path', 'kind', 'sha256')} for snapshot in snapshots],
        }