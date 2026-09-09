EOF
cat > services/python/tests/ingest_runtime/test_pipeline.py <<'EOF'
import json
import pytest
from sqlalchemy import select
from knowledge_platform.ingest.pipeline import Pipeline
from knowledge_platform.ingest.models import Source, SourceVersion, Step, Task, Registration
from knowledge_platform.ingest.service import IngestService
from knowledge_platform.ingest.schemas import SourceCreate, SourceUpdate, IngestError
from knowledge_platform.ingest.connectors import RawSnapshot, RawFile
from knowledge_platform.ingest.storage import S3Store
from .test_storage import ObjectServer
from .test_postgres import store, svc, create, Machine, Internal

class KnowledgeProtocol(Internal):
    def __init__(self): self.snapshots={}; self.pages={}; self.calls=[]; self.conflict=False
    async def request(self,method,path,token,body=None,**kwargs):
        assert token=='worker'
        self.calls.append((method,path,body))
        if path=='/internal/v1/sources/snapshots':
            key=(body['source_id'],body['source_revision'],body['path'])
            if key not in self.snapshots: self.snapshots[key]=body|{'id':'snapshot-'+str(len(self.snapshots)+1)}
            return self.snapshots[key]
        if method=='GET' and path.startswith('/api/v1/pages/'):
            return self.pages.get(path.removeprefix('/api/v1/pages/'))
        if path=='/internal/v1/pages/publish':
            if self.conflict: raise IngestError(409,'publication_conflict','CAS conflict')
            page={'id':body['page_id'],'current_revision':'revision-1','revision':{'id':'revision-1','content':body['content']}}
            self.pages[body['page_id']]=page
            return {'id':'revision-1','page_id':body['page_id']}
        if path=='/api/v1/pages':
            self.pages[body['id']]={'id':body['id'],'current_revision':None,'revision':None}
            return {'page':self.pages[body['id']],'proposal':{'id':'proposal-1'}}
        if path.endswith('/proposals'): return {'id':'proposal-2'}
        if path.endswith('/validity'): return {'id':'validity-revision'}
        if path.endswith('/source-deletion'): return {'id':'deletion-revision'}
        raise AssertionError((method,path))

async def setup_pipeline(store,kind='git',files=None):
    db,auth,box=store
    body=SourceCreate(name='repo',space_id='space',kind=kind,config={'url':'https://host/repo','ref':'HEAD'} if kind=='git' else {'path':'a.md','content':'x'})
    source=await create(store,body)
    objects=S3Store(ObjectServer(),'raw')
    raw=RawSnapshot('commit-1',tuple(files or [RawFile('main.go',b'package main\nfunc Pay() {}\n','code')]))
    key,manifest=objects.put_snapshot(source['id'],1,raw)
    async with db.session() as s,s.begin():
        service=svc(s,auth,box)
        await service.save_preview('alice',source['id'],1,key,manifest)
        task=await service.sync('alice',source['id'],1)
    knowledge=KnowledgeProtocol()
    return source,task,Pipeline(db,auth,Machine(),knowledge,box,objects),knowledge

async def finish(pipeline,task_id):
    for _ in range(100):
        if await pipeline.advance(task_id): return
    pytest.fail('pipeline did not finish')

async def test_real_parser_pipeline_registers_exact_source_before_publishing(store):
    source,task,pipeline,knowledge=await setup_pipeline(store)
    await finish(pipeline,task['id'])
    assert len(knowledge.snapshots)==2 # original file and complete source manifest
    snap=next(s for s in knowledge.snapshots.values() if s['path']=='main.go')
    assert snap['text']=='package main\nfunc Pay() {}\n'
    publication=next(b for _,p,b in knowledge.calls if p=='/internal/v1/pages/publish')
    assert publication['content']['evidence'][0]['revision_id']==snap['id']
    db,auth,box=store
    async with db.session() as s:
        assert (await s.get(Task,task['id'])).status=='succeeded'
    assert all('alice' not in str(b) for _,_,b in knowledge.calls)

async def test_prepared_old_task_is_fenced_after_source_update(store):
    source,task,pipeline,knowledge=await setup_pipeline(store)
    for _ in range(4): await pipeline.advance(task['id'])
    db,auth,box=store
    async with db.session() as s,s.begin():
        await svc(s,auth,box).update('alice',source['id'],SourceUpdate(base_version=1,name='new',config={'url':'https://host/repo','ref':'HEAD'}))
    await finish(pipeline,task['id'])
    assert not any(path=='/internal/v1/pages/publish' for _,path,_ in knowledge.calls)

async def test_cas_conflict_becomes_review_needed_without_force_retry(store):
    source,task,pipeline,knowledge=await setup_pipeline(store)
    knowledge.conflict=True
    await finish(pipeline,task['id'])
    db,auth,box=store
    async with db.session() as s:
        result=await s.get(Task,task['id'])
        assert result.status=='review_needed'
    assert sum(path=='/internal/v1/pages/publish' for _,path,_ in knowledge.calls)==1

async def test_markdown_creates_proposal_only_and_binary_stays_raw(store):
    source,task,pipeline,knowledge=await setup_pipeline(store,files=[RawFile('readme.md',b'# User supplied conclusion\n','markdown'),RawFile('bad.go',b'\xff','raw',('invalid_utf8',))])
    await finish(pipeline,task['id'])
    assert any(path=='/api/v1/pages' for _,path,_ in knowledge.calls)
    assert not any(path=='/internal/v1/pages/publish' for _,path,_ in knowledge.calls)
    assert not any(s['path']=='bad.go' for s in knowledge.snapshots.values())

async def test_revoked_worker_stops_before_canonical_write(store):
    source,task,pipeline,knowledge=await setup_pipeline(store)
    db,auth,box=store; auth.disabled.add('worker')
    await pipeline.advance(task['id'])
    assert knowledge.calls==[]
    async with db.session() as s:
        assert (await s.get(Task,task['id'])).status=='failed'