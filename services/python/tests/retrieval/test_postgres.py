"""Real PostgreSQL projection fencing; set RETRIEVAL_TEST_DATABASE_URL."""
import asyncio
import os
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from test_projection import projection


@pytest_asyncio.fixture
async def catalog():
    url=os.environ.get("RETRIEVAL_TEST_DATABASE_URL")
    if not url:
        pytest.skip("RETRIEVAL_TEST_DATABASE_URL required for real PostgreSQL")
    from knowledge_platform.common.db import Database
    from knowledge_platform.retrieval.models import initialize
    from knowledge_platform.retrieval.store import Catalog
    schema="test_retrieval_"+uuid4().hex
    admin=create_async_engine(url)
    async with admin.begin() as connection:
        await connection.execute(text(f"CREATE SCHEMA {schema}"))
    engine=create_async_engine(url,connect_args={"options":f"-csearch_path={schema}"})
    await initialize(engine)
    yield Catalog(Database(engine))
    await engine.dispose()
    async with admin.begin() as connection:
        await connection.execute(text(f"DROP SCHEMA {schema} CASCADE"))
    await admin.dispose()


@pytest.mark.asyncio
async def test_older_event_preserves_new_current_and_records_both_immutable_revisions(catalog):
    from knowledge_platform.retrieval.projection import compile_fragments
    writes=[]
    async def build(value,existing):
        fs=compile_fragments(value,configuration_id="model-v1",dimensions=3)
        for f in fs:f.embedding=[1,0,0]
        return fs
    async def write(fs,generation):
        writes.append((fs,generation))
    second=projection(revision_id="revision-b",version=2,current_revision="revision-b",current_version=2)
    await catalog.project(second,build,write,event_id="event-b")
    first=projection(current_revision="revision-b",current_version=2,is_current=False)
    await catalog.project(first,build,write,event_id="event-a")
    rows=await catalog.revisions("page:pay")
    assert {r["revision_id"]:r["is_current"] for r in rows} == {"revision-a":False,"revision-b":True}
    assert [gen for _,gen in writes] == sorted({gen for _,gen in writes})
    count=len(writes)
    await catalog.project(first,build,write,event_id="event-a")
    assert len(writes)==count


@pytest.mark.asyncio
async def test_failed_bulk_does_not_commit_offset_and_retry_generation_increases(catalog):
    from knowledge_platform.retrieval.projection import compile_fragments
    seen=[]
    async def build(value,existing):
        return compile_fragments(value,configuration_id="model-v1",dimensions=3)
    async def fail(fs,generation):
        seen.append(generation)
        raise RuntimeError("network ended after Elasticsearch may have accepted")
    with pytest.raises(RuntimeError):
        await catalog.project(projection(),build,fail,event_id="event-a")
    assert await catalog.revisions("page:pay")==[]
    async def success(fs,generation):seen.append(generation)
    await catalog.project(projection(),build,success,event_id="event-a")
    assert len(await catalog.revisions("page:pay"))==1
    assert seen[1]>seen[0]


@pytest.mark.asyncio
async def test_old_acl_snapshot_cannot_overwrite_new_policy_projection(catalog):
    from knowledge_platform.retrieval.projection import compile_fragments,clauses_for,digest
    from knowledge_platform.retrieval.schemas import Policy
    async def build(value,existing):return compile_fragments(value,configuration_id="model-v1",dimensions=3)
    writes=[]
    async def write(fs,generation):writes.extend(fs)
    newer=projection()
    for p in newer["policies"]:p.update(auth_epoch=10)
    newer["policies"][0].update(resource_read_subjects=["user:bob"],acl_version=10)
    newer["read_clauses"]=clauses_for([Policy.model_validate(p) for p in newer["policies"]])
    newer["acl_domain"]=digest({"space_id":newer["space_id"],"read_clauses":newer["read_clauses"]})
    await catalog.project(newer,build,write)
    await catalog.project(projection(),build,write)
    assert len(writes)==2
    assert (await catalog.policies(["engineering"],100))[0]["acl_domain"]==newer["acl_domain"]


@pytest.mark.asyncio
async def test_concurrent_page_publications_serialize_external_writes(catalog):
    from knowledge_platform.retrieval.projection import compile_fragments
    active=0
    peak=0
    async def build(value,existing):return compile_fragments(value,configuration_id="model-v1",dimensions=3)
    async def write(fs,generation):
        nonlocal active,peak
        active+=1
        peak=max(peak,active)
        await asyncio.sleep(.03)
        active-=1
    first=projection()
    second=projection(revision_id="revision-b",version=2,current_revision="revision-b",current_version=2)
    await asyncio.gather(catalog.project(first,build,write),catalog.project(second,build,write))
    assert peak==1
    rows=await catalog.revisions("page:pay")
    assert next(r for r in rows if r["revision_id"]=="revision-b")["is_current"]
    assert not next(r for r in rows if r["revision_id"]=="revision-a")["is_current"]