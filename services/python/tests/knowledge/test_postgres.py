"""Real PostgreSQL tests. Set TEST_DATABASE_URL; no SQLite substitute is used."""
import hashlib
import os
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.asyncio


class Authorizer:
    def __init__(self):
        self.denied = set()
        self.policies = {}

    async def resolve(self, token):
        return SimpleNamespace(id=token, subjects=['user:' + token], permissions=[], auth_epoch=1)

    async def require(self, token, action, space_id, resource_id=None):
        from knowledge_platform.knowledge.schemas import KnowledgeError
        if (token, action, resource_id) in self.denied:
            raise KnowledgeError(403, 'forbidden', 'Access denied')
        return SimpleNamespace(allowed=True, auth_epoch=1, acl_domain='test', acl_version=1)

    async def policy(self, space_id, resource_id):
        return self.policies.get(resource_id, dict(space_id=space_id, resource_id=resource_id, space_read_subjects=['user:alice', 'user:bob'], resource_read_subjects=None, acl_version=1, auth_epoch=1))

    async def request(self, method, path, target, token=None, json=None):
        return SimpleNamespace(status_code=201, raise_for_status=lambda: None)


@pytest_asyncio.fixture
async def store():
    url = os.environ.get('TEST_DATABASE_URL')
    if not url:
        pytest.skip('TEST_DATABASE_URL is required for real PostgreSQL integration')
    from knowledge_platform.knowledge.models import initialize
    schema = 'test_knowledge_' + uuid4().hex
    admin = create_async_engine(url)
    async with admin.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA {schema}'))
    engine = create_async_engine(url, connect_args={'options': f'-csearch_path={schema}'})
    await initialize(engine)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    authorizer = Authorizer()

    @asynccontextmanager
    async def service():
        from knowledge_platform.knowledge.service import KnowledgeService
        async with sessions.begin() as session:
            yield KnowledgeService(session, authorizer)

    yield service, authorizer, sessions
    await engine.dispose()
    async with admin.begin() as connection:
        await connection.execute(text(f'DROP SCHEMA {schema} CASCADE'))
    await admin.dispose()


async def source(store, *, kind='code', content='package payments\nfunc Pay() {}\n', revision='a' * 40, resource='source:payments'):
    from knowledge_platform.knowledge.schemas import SourceSnapshotCreate
    service, _, _ = store
    request = SourceSnapshotCreate(source_id='git:payments', source_revision=revision, resource_id=resource, space_id='finance', path='main.go', kind=kind, text=content, sha256=hashlib.sha256(content.encode()).hexdigest(), object_key=f'sources/payments/{revision}/main.go')
    async with service() as knowledge:
        snapshot = await knowledge.register_snapshot('alice', 'ingest', request)
    return request, snapshot


def content(snapshot, title='Payments'):
    from knowledge_platform.knowledge.schemas import PageContent
    ref = dict(resource_id=snapshot['resource_id'], revision_id=snapshot['id'], source_id=snapshot['source_id'], source_revision=snapshot['source_revision'], path=snapshot['path'], start_line=1, end_line=2, kind=snapshot['kind'])
    return PageContent(title=title, markdown='# Payments\nPay is defined in main.go.', entity_type='File', claims=[dict(id='claim:pay', text='Pay is defined in main.go.', kind='fact', entity_type='File', evidence=[ref])])


async def draft(store):
    from knowledge_platform.knowledge.schemas import PageCreate
    _, snapshot = await source(store)
    service, _, _ = store
    async with service() as knowledge:
        created = await knowledge.create_page('alice', PageCreate(id='page:payments', space_id='finance', content=content(snapshot)))
    return created, snapshot


async def publish(store):
    created, snapshot = await draft(store)
    service, _, _ = store
    async with service() as knowledge:
        revision = await knowledge.approve('reviewer', created['proposal']['id'], 'Reviewed source')
    return revision, snapshot


async def test_public_create_proposes_then_review_atomically_publishes(store):
    created, _ = await draft(store)
    assert created['page']['current_revision'] is None
    service, _, sessions = store
    async with sessions() as session:
        assert (await session.execute(text('SELECT count(*) FROM knowledge_revisions'))).scalar_one() == 0
        assert (await session.execute(text('SELECT count(*) FROM knowledge_outbox'))).scalar_one() == 0
    async with service() as knowledge:
        revision = await knowledge.approve('reviewer', created['proposal']['id'], 'Reviewed source')
    assert revision['number'] == 1
    async with sessions() as session:
        assert (await session.execute(text('SELECT current_revision FROM knowledge_pages'))).scalar_one() == revision['id']
        assert (await session.execute(text('SELECT count(*) FROM knowledge_outbox'))).scalar_one() == 1
        assert (await session.execute(text("SELECT count(*) FROM knowledge_audit WHERE action='revision.published'"))).scalar_one() == 1


async def test_competing_proposals_use_cas_and_failed_approval_stays_pending(store):
    from knowledge_platform.knowledge.schemas import KnowledgeError, ProposalCreate
    created, snapshot = await draft(store)
    service, _, _ = store
    async with service() as knowledge:
        other = await knowledge.propose('alice', 'page:payments', ProposalCreate(base_revision=None, content=content(snapshot, 'Second'), reason='Competing'))
        await knowledge.approve('reviewer', created['proposal']['id'], 'First accepted')
    with pytest.raises(KnowledgeError, match='base_revision') as caught:
        async with service() as knowledge:
            await knowledge.approve('reviewer', other['id'], 'Too late')
    assert caught.value.status == 409
    async with service() as knowledge:
        reviews = await knowledge.list_reviews('reviewer')
        assert [item['id'] for item in reviews['items']] == [other['id']]


async def test_source_revocation_hides_page_history_and_review(store):
    from knowledge_platform.knowledge.schemas import KnowledgeError
    created, snapshot = await draft(store)
    service, auth, _ = store
    auth.denied.add(('reviewer', 'read', snapshot['resource_id']))
    with pytest.raises(KnowledgeError) as caught:
        async with service() as knowledge:
            await knowledge.approve('reviewer', created['proposal']['id'], 'Revoked')
    assert caught.value.status == 403
    auth.denied.clear()
    async with service() as knowledge:
        revision = await knowledge.approve('reviewer', created['proposal']['id'], 'Accepted')
    auth.denied.add(('alice', 'read', snapshot['resource_id']))
    async with service() as knowledge:
        assert (await knowledge.list_pages('alice'))['items'] == []
        assert (await knowledge.list_revisions('alice', 'page:payments'))['items'] == []
    with pytest.raises(KnowledgeError) as caught:
        async with service() as knowledge:
            await knowledge.get_revision('alice', 'page:payments', revision['id'])
    assert caught.value.status == 403


async def test_snapshot_is_content_address_verified_and_immutable(store):
    from knowledge_platform.knowledge.schemas import KnowledgeError
    request, snapshot = await source(store)
    service, _, sessions = store
    async with service() as knowledge:
        same = await knowledge.register_snapshot('alice', 'ingest', request)
        assert same['id'] == snapshot['id']
    changed = request.model_copy(update={'text': 'modified', 'sha256': hashlib.sha256(b'modified').hexdigest()})
    with pytest.raises(KnowledgeError) as caught:
        async with service() as knowledge:
            await knowledge.register_snapshot('alice', 'ingest', changed)
    assert caught.value.status == 409
    with pytest.raises(Exception, match='immutable'):
        async with sessions.begin() as session:
            await session.execute(text('UPDATE knowledge_source_snapshots SET text=\'tampered\''))


async def test_rollback_creates_new_revision_and_cannot_mutate_history(store):
    from knowledge_platform.knowledge.schemas import ProposalCreate, RollbackRequest
    first, snapshot = await publish(store)
    service, _, sessions = store
    async with service() as knowledge:
        proposal = await knowledge.propose('alice', 'page:payments', ProposalCreate(base_revision=first['id'], content=content(snapshot, 'Second'), reason='Update'))
        second = await knowledge.approve('reviewer', proposal['id'], 'Reviewed')
        third = await knowledge.rollback('reviewer', 'page:payments', RollbackRequest(base_revision=second['id'], revision_id=first['id'], reason='Restore'))
    assert third['id'] != first['id'] and third['number'] == 3
    assert third['content'] == first['content']
    with pytest.raises(Exception, match='immutable'):
        async with sessions.begin() as session:
            await session.execute(text("UPDATE knowledge_revisions SET publication_kind='tampered'"))


async def test_outbox_leases_are_per_consumer_exclusive_and_ack_fenced(store):
    from knowledge_platform.knowledge.schemas import KnowledgeError, LeaseRequest
    revision, _ = await publish(store)
    service, _, _ = store
    async with service() as knowledge:
        first = await knowledge.lease_outbox('retrieval', LeaseRequest(consumer='retrieval'))
    assert len(first['items']) == 1
    event = first['items'][0]
    assert event['revision_id'] == revision['id']
    assert 'token' not in str(event['payload']).lower()
    async with service() as knowledge:
        assert (await knowledge.lease_outbox('retrieval', LeaseRequest(consumer='retrieval')))['items'] == []
        graph = await knowledge.lease_outbox('graphiti', LeaseRequest(consumer='graphiti'))
        assert len(graph['items']) == 1
    with pytest.raises(KnowledgeError) as caught:
        async with service() as knowledge:
            await knowledge.ack_outbox('retrieval', event['id'], 'wrong-token')
    assert caught.value.status == 409
    async with service() as knowledge:
        await knowledge.ack_outbox('retrieval', event['id'], event['lease_token'])
        await knowledge.ack_outbox('retrieval', event['id'], event['lease_token'])
        assert (await knowledge.lease_outbox('retrieval', LeaseRequest(consumer='retrieval')))['items'] == []


async def test_projection_domain_conjoins_all_support_policies(store):
    revision, snapshot = await publish(store)
    service, auth, _ = store
    auth.policies[snapshot['resource_id']] = dict(space_id='finance', resource_id=snapshot['resource_id'], space_read_subjects=['user:alice', 'user:bob'], resource_read_subjects=['user:alice'], acl_version=2, auth_epoch=2)
    async with service() as knowledge:
        projection = await knowledge.projection('retrieval', 'page:payments', revision['id'])
    assert projection['read_clauses'] == [['user:alice'], ['user:alice', 'user:bob']]
    first_domain = projection['acl_domain']
    auth.policies[snapshot['resource_id']]['resource_read_subjects'] = []
    async with service() as knowledge:
        blocked = await knowledge.projection('retrieval', 'page:payments', revision['id'])
    assert [] in blocked['read_clauses']
    assert blocked['acl_domain'] != first_domain


async def test_auto_publish_requires_ingest_identity_and_exact_source_proof(store):
    from knowledge_platform.knowledge.schemas import CompilerProof, DeterministicPublish, KnowledgeError
    _, snapshot = await source(store)
    service, _, sessions = store
    request = DeterministicPublish(page_id='page:compiled', space_id='finance', base_revision=None, content=content(snapshot), proof=CompilerProof(compiler_version='tree-sitter-v1', schema_version='v2', snapshot_ids=[snapshot['id']], idempotency_key='compile:1'))
    with pytest.raises(KnowledgeError) as caught:
        async with service() as knowledge:
            await knowledge.deterministic_publish('alice', 'agent', request)
    assert caught.value.status == 403
    async with service() as knowledge:
        revision = await knowledge.deterministic_publish('alice', 'ingest', request)
    async with service() as knowledge:
        repeated = await knowledge.deterministic_publish('alice', 'ingest', request)
    assert repeated['id'] == revision['id']
    async with sessions() as session:
        assert (await session.execute(text('SELECT count(*) FROM knowledge_revisions'))).scalar_one() == 1


async def test_source_line_bounds_are_checked_before_proposal(store):
    from knowledge_platform.knowledge.schemas import KnowledgeError, PageCreate
    _, snapshot = await source(store)
    service, _, _ = store
    body = content(snapshot)
    body.claims[0].evidence[0].end_line = 500
    with pytest.raises(KnowledgeError) as caught:
        async with service() as knowledge:
            await knowledge.create_page('alice', PageCreate(id='page:invalid', space_id='finance', content=body))
    assert caught.value.status == 422