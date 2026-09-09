import asyncio
import math
from datetime import UTC, datetime

import pytest
from prometheus_client import CollectorRegistry, generate_latest

from knowledge_platform.common.domain_metrics import DomainMetrics, QueueSnapshot, SnapshotSampler


def sample(registry, name, labels):
    return registry.get_sample_value(name, labels)


def test_operation_outcomes_are_bounded_and_never_export_payload_or_exception_text():
    registry = CollectorRegistry()
    metrics = DomainMetrics('llm', registry)
    with metrics.operation('model', 'embedding'):
        pass
    with pytest.raises(ValueError):
        with metrics.operation('model', 'chat'):
            raise ValueError('private-source secret-provider-token https://provider/private')
    with metrics.operation('private-component', 'private-model-id') as operation:
        operation.outcome = 'private-error-description'
    output = generate_latest(registry).decode()
    assert 'component="model",operation="embedding",outcome="success",service="llm"' in output
    assert 'component="model",operation="chat",outcome="error",service="llm"' in output
    for private in ('private-', 'secret-provider', 'https://provider'):
        assert private not in output


def test_queue_snapshot_expires_even_while_metrics_server_remains_alive():
    ticks = [100.0]
    registry = CollectorRegistry()
    metrics = DomainMetrics('ingest', registry, clock=lambda: ticks[0], wall=lambda: 1000.0)
    metrics.define_queue('tasks_queued')
    labels = {'service':'ingest','queue':'tasks_queued'}
    assert sample(registry,'knowledge_queue_known',labels) == 0
    assert math.isnan(sample(registry,'knowledge_queue_size',labels))
    metrics.queue_snapshot('tasks_queued',QueueSnapshot(2,datetime.fromtimestamp(990,UTC)))
    assert sample(registry,'knowledge_queue_known',labels) == 1
    assert sample(registry,'knowledge_queue_size',labels) == 2
    assert sample(registry,'knowledge_queue_oldest_age_seconds',labels) == 10
    ticks[0] += 46
    assert sample(registry,'knowledge_queue_known',labels) == 0
    assert math.isnan(sample(registry,'knowledge_queue_size',labels))
    assert sample(registry,'knowledge_queue_observed_age_seconds',labels) == 46
    metrics.queue_snapshot('tasks_queued',QueueSnapshot(0,None))
    assert sample(registry,'knowledge_queue_known',labels) == 1
    assert sample(registry,'knowledge_queue_oldest_age_seconds',labels) == 0


@pytest.mark.asyncio
async def test_snapshot_failure_and_timeout_clear_known_state_without_fake_zero():
    registry = CollectorRegistry()
    metrics = DomainMetrics('ingest',registry)
    state = {'failure':False}
    async def load():
        if state['failure']:
            raise RuntimeError('private-db-url and credentials')
        return {'tasks_queued':QueueSnapshot(3,datetime.now(UTC))}
    sampler = SnapshotSampler(metrics,load,['tasks_queued'],timeout=0.01)
    assert await sampler.sample() is True
    state['failure'] = True
    assert await sampler.sample() is False
    labels={'service':'ingest','queue':'tasks_queued'}
    assert sample(registry,'knowledge_queue_known',labels) == 0
    assert math.isnan(sample(registry,'knowledge_queue_size',labels))
    async def hang():
        await asyncio.sleep(60)
    sampler.load = hang
    assert await sampler.sample() is False
    assert 'private-db-url' not in generate_latest(registry).decode()


def test_known_count_with_unavailable_age_stays_explicitly_unknown_and_labels_are_fixed():
    registry=CollectorRegistry()
    metrics=DomainMetrics('retrieval',registry)
    metrics.define_queue('outbox_total')
    metrics.queue_snapshot('failed_revisions',QueueSnapshot(2,None))
    metrics.queue_snapshot('private-source-id',QueueSnapshot(1,None))
    labels={'service':'retrieval','queue':'failed_revisions'}
    assert sample(registry,'knowledge_queue_known',labels) == 1
    assert math.isnan(sample(registry,'knowledge_queue_oldest_age_seconds',labels))
    output=generate_latest(registry).decode()
    assert 'private-source-id' not in output
    assert sample(registry,'knowledge_queue_known',{'service':'retrieval','queue':'outbox_total'}) == 0