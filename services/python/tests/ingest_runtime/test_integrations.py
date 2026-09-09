"""Explicit real middleware tests. Protocol fixtures do not count as live full-stack acceptance."""
import asyncio
import os
from uuid import uuid4

import pytest
from temporalio.client import Client
from temporalio.worker import Worker

from knowledge_platform.ingest.config import IngestSettings
from knowledge_platform.ingest.connectors import RawFile, RawSnapshot
from knowledge_platform.ingest.storage import S3Store
from knowledge_platform.ingest.models import Task
from knowledge_platform.ingest.worker import Activities
from knowledge_platform.ingest.workflow import IngestWorkflow, TemporalDispatcher
from .test_postgres import store
from .test_pipeline import setup_pipeline

@pytest.mark.integration
@pytest.mark.skipif(os.environ.get('INGEST_RUN_S3_TESTS')!='1',reason='Set INGEST_RUN_S3_TESTS=1 and INGEST_S3_* for real S3 integration')
def test_real_s3_immutable_raw_manifest_and_conditional_retry():
    config=IngestSettings()
    store=S3Store.from_settings(config)
    bucket='ingest-test-'+uuid4().hex
    store.client.create_bucket(Bucket=bucket)
    store.bucket=bucket
    try:
        data=b'exact integration bytes\r\n'+uuid4().hex.encode()
        raw=RawSnapshot('immutable-commit',(RawFile('test.md',data,'markdown'),))
        first_key,first=store.put_snapshot('test-source',1,raw)
        second_key,second=store.put_snapshot('test-source',1,raw)
        assert (first_key,first)==(second_key,second)
        file=first['files'][0]
        assert store.get_bytes(file['object_key'],file['sha256'])==data
    finally:
        # This uniquely named bucket is owned only by this test.
        for item in store.client.list_objects_v2(Bucket=bucket).get('Contents',[]):
            store.client.delete_object(Bucket=bucket,Key=item['Key'])
        store.client.delete_bucket(Bucket=bucket)

@pytest.mark.integration
@pytest.mark.skipif(os.environ.get('INGEST_RUN_TEMPORAL_TESTS')!='1',reason='Set INGEST_RUN_TEMPORAL_TESTS=1, INGEST_TEMPORAL_ADDRESS and TEST_INGEST_DATABASE_URL')
async def test_real_temporal_runs_durable_pg_task_and_history_contains_no_raw_body(store):
    source,task,pipeline,knowledge=await setup_pipeline(store)
    config=IngestSettings()
    client=await Client.connect(config.ingest_temporal_address,namespace=config.ingest_temporal_namespace)
    queue='ingest-test-'+uuid4().hex
    activities=Activities(pipeline)
    async with Worker(client,task_queue=queue,workflows=[IngestWorkflow],activities=[activities.advance,activities.fail]):
        dispatcher=TemporalDispatcher(pipeline.db,client,queue)
        assert await dispatcher.dispatch()==1
        handle=client.get_workflow_handle('ingest:'+task['id'])
        await asyncio.wait_for(handle.result(),timeout=60)
        history=await handle.fetch_history()
        wire=str(history)
        assert 'func Pay()' not in wire and 'Bearer' not in wire and 'machine-token' not in wire
    db,auth,box=store
    async with db.session() as session: assert (await session.get(Task,task['id'])).status=='succeeded'