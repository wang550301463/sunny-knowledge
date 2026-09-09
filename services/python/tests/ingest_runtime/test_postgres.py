import base64
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine

from knowledge_platform.common.db import Database
from knowledge_platform.common.auth import Principal, Decision
from knowledge_platform.common.secrets import SecretBox
from knowledge_platform.ingest.models import initialize, SourceVersion, Audit, Task
from knowledge_platform.ingest.service import IngestService
from knowledge_platform.ingest.schemas import SourceCreate, SourceUpdate, IngestError

class Authorizer:
    def __init__(self): self.epoch=1; self.denied=set(); self.disabled=set(); self.calls=[]
    async def resolve(self, token):
        if token in self.disabled: raise HTTPException(403,'disabled')
        return Principal(id=token,subjects=['service:'+token if token=='worker' else 'user:'+token],auth_epoch=self.epoch)
    async def authorize(self, token, action, space_id, resource_id=None):
        await self.resolve(token)
        self.calls.append((token,action,space_id,resource_id))
        return Decision(allowed=(token,action,resource_id) not in self.denied,auth_epoch=self.epoch)

class Machine:
    async def token(self): return 'worker'

class Internal:
    async def register_resource(self,token,space_id,resource_id): return {}

@pytest_asyncio.fixture
async def store():
    dsn=os.environ.get('TEST_INGEST_DATABASE_URL')
    if not dsn: pytest.skip('Set TEST_INGEST_DATABASE_URL to an isolated PostgreSQL database')
    schema='ingest_test_'+uuid4().hex
    dsn=dsn.replace('postgresql://','postgresql+psycopg://',1)
    admin=create_async_engine(dsn)
    async with admin.begin() as c: await c.execute(text(f'CREATE SCHEMA {schema}'))
    engine=create_async_engine(dsn,connect_args={'options':f'-csearch_path={schema}'})
    db=Database(engine)
    await initialize(engine)
    auth=Authorizer(); box=SecretBox(base64.b64encode(b'x'*32).decode())
    try: yield db,auth,box
    finally:
        await db.close()
        async with admin.begin() as c: await c.execute(text(f'DROP SCHEMA {schema} CASCADE'))
        await admin.dispose()

def svc(session,auth,box): return IngestService(session,auth,Machine(),Internal(),box)

def upload(): return SourceCreate(name='Guide',space_id='space',kind='markdown',config={'path':'a.md','content':'# original\r\n'})

async def create(store, body=None):
    db,auth,box=store
    async with db.session() as session,session.begin(): return await svc(session,auth,box).create('alice',body or upload())

async def test_source_version_cas_and_live_resource_denial(store):
    item=await create(store); db,auth,box=store
    async with db.session() as s,s.begin():
        updated=await svc(s,auth,box).update('alice',item['id'],SourceUpdate(base_version=1,name='Guide',config={'path':'a.md','content':'new'}))
        assert updated['version']==2
    async with db.session() as s,s.begin():
        with pytest.raises(IngestError) as exc:
            await svc(s,auth,box).update('alice',item['id'],SourceUpdate(base_version=1,name='Guide',config={'path':'a.md','content':'stale'}))
        assert exc.value.code=='version_conflict'
    auth.denied.add(('alice','read',item['resource_id']))
    async with db.session() as s,s.begin():
        assert (await svc(s,auth,box).list_sources('alice',None,None,50))['items']==[]
        with pytest.raises(IngestError): await svc(s,auth,box).get('alice',item['id'])

async def test_creation_requires_explicit_worker_grant(store):
    db,auth,box=store; auth.denied.add(('worker','write',None))
    with pytest.raises(IngestError) as exc: await create(store)
    assert exc.value.code=='worker_authorization_required'

async def test_encrypted_credentials_and_original_actor_audit(store):
    item=await create(store,SourceCreate(name='Repo',space_id='space',kind='git',config={'url':'https://host/repo','ref':'HEAD'},credential={'username':'bot','password':'secret-never-return'}))
    db,auth,box=store
    assert item['has_credential'] and 'secret-never-return' not in json.dumps(item)
    async with db.session() as s:
        v=await s.get(SourceVersion,(item['id'],1))
        assert 'secret-never-return' not in v.credential_ciphertext
        assert box.decrypt(v.credential_ciphertext,f'{item["id"]}:1').find('secret-never-return')>=0
        audit=(await s.scalars(select(Audit))).all()
        assert audit[0].actor_id=='alice'

async def test_sync_requires_preview_and_duplicate_task_survives_new_session(store):
    item=await create(store); db,auth,box=store
    async with db.session() as s,s.begin():
        with pytest.raises(IngestError) as exc: await svc(s,auth,box).sync('alice',item['id'],1)
        assert exc.value.code=='preview_required'
    manifest={'source_revision':'version:1','files':[],'file_count':0,'total_bytes':0,'diagnostics':[]}
    async with db.session() as s,s.begin(): await svc(s,auth,box).save_preview('alice',item['id'],1,'key',manifest)
    async with db.session() as s,s.begin(): first=await svc(s,auth,box).sync('alice',item['id'],1)
    async with db.session() as s,s.begin(): second=await svc(s,auth,box).sync('alice',item['id'],1)
    assert first['id']==second['id']
    assert first['status']=='queued'
    async with db.session() as s,s.begin():
        await svc(s,auth,box).update('alice',item['id'],SourceUpdate(base_version=1,name='new',config={'path':'a.md','content':'new'}))
    async with db.session() as s,s.begin():
        old=await svc(s,auth,box).get_task('alice',first['id'])
        assert old['status']=='superseded'

async def test_immutable_source_version_trigger_rejects_mutation(store):
    item=await create(store); db,auth,box=store
    from sqlalchemy.exc import DBAPIError
    with pytest.raises(DBAPIError):
        async with db.session() as s,s.begin():
            await s.execute(text('UPDATE ingest_source_versions SET config=\'{}\'::jsonb WHERE source_id=:id'),{'id':item['id']})

async def test_disabled_worker_cannot_sync_even_with_old_creator_authority(store):
    item=await create(store); db,auth,box=store; auth.disabled.add('worker')
    async with db.session() as s,s.begin():
        with pytest.raises(IngestError) as exc: await svc(s,auth,box).sync('alice',item['id'],1)
        assert exc.value.code=='worker_authorization_required'