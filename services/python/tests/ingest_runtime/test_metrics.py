import math

from knowledge_platform.common.domain_metrics import DomainMetrics, SnapshotSampler
from knowledge_platform.ingest.models import Task
from knowledge_platform.ingest.metrics import TASK_QUEUES, task_snapshot
from .test_pipeline import setup_pipeline,finish


async def test_pipeline_metrics_follow_committed_stage_and_authoritative_queue_snapshot(store):
    source,task,pipeline,_knowledge=await setup_pipeline(store)
    metrics=DomainMetrics('ingest')
    pipeline.metrics=metrics
    sampler=SnapshotSampler(metrics,lambda:task_snapshot(pipeline.db),TASK_QUEUES)
    assert await sampler.sample()
    labels={'service':'ingest','queue':'tasks_queued'}
    assert metrics.registry.get_sample_value('knowledge_queue_size',labels)==1
    await finish(pipeline,task['id'])
    assert await sampler.sample()
    assert metrics.registry.get_sample_value('knowledge_queue_size',labels)==0
    published=metrics.registry.get_sample_value('knowledge_domain_operations_total',{'service':'ingest','component':'ingest','operation':'publish','outcome':'succeeded'})
    assert published==1
    from prometheus_client import generate_latest
    output=generate_latest(metrics.registry).decode()
    assert source['id'] not in output and task['id'] not in output


async def test_pipeline_dependency_failure_is_not_counted_as_completed_or_empty_backlog(store):
    _source,task,pipeline,knowledge=await setup_pipeline(store)
    metrics=DomainMetrics('ingest');pipeline.metrics=metrics
    db,auth,_box=store
    auth.disabled.add('worker')
    await finish(pipeline,task['id'])
    async with db.session() as session:
        assert (await session.get(Task,task['id'])).status=='failed'
    sampler=SnapshotSampler(metrics,lambda:task_snapshot(db),TASK_QUEUES)
    assert await sampler.sample()
    assert metrics.registry.get_sample_value('knowledge_queue_size',{'service':'ingest','queue':'tasks_failed'})==1
    assert not knowledge.calls