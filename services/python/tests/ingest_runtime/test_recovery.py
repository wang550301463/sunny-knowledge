"""Real ingest PostgreSQL; Knowledge protocol commits before injected transport failures."""
from copy import deepcopy
from urllib.parse import unquote

import pytest
from sqlalchemy import select

from knowledge_platform.ingest.compiler import page_id
from knowledge_platform.ingest.connectors import RawSnapshot
from knowledge_platform.ingest.models import SourcePage, Step, Task
from knowledge_platform.ingest.schemas import IngestError, SourceUpdate
from knowledge_platform.ingest.service import IngestService
from .test_pipeline import finish, setup_pipeline
from .test_postgres import Machine, svc


async def empty_second_version(store, source, pipeline):
    db, auth, box = store
    async with db.session() as session, session.begin():
        await svc(session, auth, box).update('alice', source['id'], SourceUpdate(base_version=1, name='new', config={'url':'https://host/repo','ref':'HEAD'}))
    key, manifest = pipeline.storage.put_snapshot(source['id'], 2, RawSnapshot('commit-2', ()))
    async with db.session() as session, session.begin():
        await svc(session, auth, box).save_preview('alice', source['id'], 2, key, manifest)
        return await svc(session, auth, box).sync('alice', source['id'], 2)


async def lose_publish_reply(pipeline, knowledge, task_id):
    original = knowledge.request
    async def commit_then_fail(method, path, token, body=None, **kwargs):
        result = await original(method, path, token, body, **kwargs)
        if path == '/internal/v1/pages/publish':
            page = knowledge.pages[body['page_id']]
            page['space_id'] = 'space'
            page['revision']['access_dependencies'] = body['proof']['snapshot_ids']
            raise IngestError(503, 'dependency_unavailable', 'Reply lost after canonical commit')
        if path.endswith('/source-deletion'):
            target = unquote(path.split('/pages/', 1)[1].removesuffix('/source-deletion'))
            knowledge.pages[target]['revision']['content']['state'] = 'stale'
        return result
    knowledge.request = commit_then_fail
    with pytest.raises(IngestError) as exc:
        await finish(pipeline, task_id)
    assert exc.value.status == 503


async def test_accepted_publish_lost_reply_is_reconciled_before_newer_empty_snapshot(store):
    source, first, pipeline, knowledge = await setup_pipeline(store)
    await lose_publish_reply(pipeline, knowledge, first['id'])
    db, _auth, _box = store
    async with db.session() as session:
        assert (await session.scalars(select(SourcePage))).all() == []
        intent = await session.scalar(select(Step).where(Step.task_id == first['id']))
        assert intent.request.get('_dispatch_intent') is True
    second = await empty_second_version(store, source, pipeline)
    await pipeline.advance(first['id'])
    await finish(pipeline, second['id'])
    assert sum(path == '/internal/v1/pages/publish' for _, path, _ in knowledge.calls) == 1
    assert any(path.endswith('/source-deletion') for _, path, _ in knowledge.calls)
    assert next(iter(knowledge.pages.values()))['revision']['content']['state'] == 'stale'


async def test_accepted_then_human_edited_lineage_generates_review_preserving_human_text(store):
    source, first, pipeline, knowledge = await setup_pipeline(store)
    await lose_publish_reply(pipeline, knowledge, first['id'])
    current = next(iter(knowledge.pages.values()))
    current['current_revision'] = 'human-revision'
    current['revision']['id'] = 'human-revision'
    current['revision']['content'] = {'title':'Human decision','markdown':'Reviewed human conclusion remains intact.','claims':[], 'evidence':[], 'state':'valid'}
    second = await empty_second_version(store, source, pipeline)
    await finish(pipeline, second['id'])
    proposals = [body for _, path, body in knowledge.calls if path.endswith('/proposals')]
    assert proposals and proposals[0]['content']['markdown'] == 'Reviewed human conclusion remains intact.'
    assert proposals[0]['content']['state'] == 'stale'
    assert not any(path.endswith('/source-deletion') for _, path, _ in knowledge.calls)
    db, _auth, _box = store
    async with db.session() as session: assert (await session.get(Task, second['id'])).status == 'review_needed'


async def conflicted_code(store, *, second_cas=False):
    source, task, pipeline, knowledge = await setup_pipeline(store)
    target = page_id(source['id'], 'main.go')
    human = {'title':'Human decision','markdown':'Human conclusion','claims':[], 'evidence':[], 'state':'valid'}
    knowledge.pages[target] = {'id':target,'space_id':'space','current_revision':'human-1','revision':{'id':'human-1','content':human, 'access_dependencies':[]}}
    original = knowledge.request
    calls = []
    denied_evidence = set()
    async def boundary(method, path, token, body=None, **kwargs):
        calls.append((method, path, deepcopy(body)))
        if path == '/internal/v1/pages/publish':
            knowledge.calls.append((method,path,body))
            knowledge.pages[target]['current_revision'] = 'human-2'
            knowledge.pages[target]['revision']['id'] = 'human-2'
            raise IngestError(409, 'review_required', 'Protected conclusion')
        if path.endswith('/proposals') and second_cas:
            knowledge.pages[target]['current_revision'] = 'human-3'
            knowledge.pages[target]['revision']['id'] = 'human-3'
            raise IngestError(409, 'publication_conflict', 'Review base changed')
        if '/revisions/' in path and method == 'GET':
            return {'id':path.rsplit('/',1)[1], 'content':human}
        if path == '/internal/v1/evidence/authorize':
            return {'decisions':[{'evidence':ref,'allowed':ref['revision_id'] not in denied_evidence} for ref in body['evidence']]}
        return await original(method,path,'worker',body,**kwargs)
    knowledge.request = boundary
    await finish(pipeline, task['id'])
    return source, task, pipeline, knowledge, calls, denied_evidence


async def test_publish_conflict_creates_real_proposal_with_frozen_compiler_content(store):
    _source, task, _pipeline, _knowledge, calls, _denied = await conflicted_code(store)
    proposals = [body for _, path, body in calls if path.endswith('/proposals')]
    assert len(proposals) == 1
    assert proposals[0]['base_revision'] == 'human-2'
    assert 'Pay' in proposals[0]['content']['markdown']
    assert sum(path == '/internal/v1/pages/publish' for _, path, _ in calls) == 1
    db, _auth, _box = store
    async with db.session() as session:
        row = await session.get(Task, task['id'])
        assert row.result['items'][0]['proposal_id']


async def test_second_proposal_cas_exposes_authorized_frozen_conflict_artifact(store):
    _source, task, pipeline, knowledge, calls, denied = await conflicted_code(store, second_cas=True)
    db, auth, box = store
    async with db.session() as session, session.begin():
        service = IngestService(session, auth, Machine(), knowledge, box)
        detail = await service.get_conflict('alice', task['id'], 0)
        assert detail['original_base_revision'] == 'human-1'
        assert detail['review_base_revision'] == 'human-2'
        assert 'Pay' in detail['request']['content']['markdown']
        assert detail['current_revision'] == 'human-3'
        denied.add(detail['request']['content']['evidence'][0]['revision_id'])
    async with db.session() as session, session.begin():
        with pytest.raises(IngestError) as exc:
            await IngestService(session, auth, Machine(), knowledge, box).get_conflict('alice',task['id'],0)
        assert exc.value.code == 'forbidden'
    assert sum(path == '/internal/v1/pages/publish' for _, path, _ in calls) == 1