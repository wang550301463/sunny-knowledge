from datetime import UTC,datetime,timedelta

import pytest
from prometheus_client import generate_latest

from knowledge_platform.common.domain_metrics import DomainMetrics


async def exercise_delivery_metrics(worker,catalog,projection,service,write_attribute):
    metrics=DomainMetrics(service)
    worker.metrics=metrics
    worker.catalog=catalog
    calls=[]
    phase={'value':'write_failure'}
    async def remote_write(*args,**kwargs):
        if phase['value']=='write_failure':
            raise RuntimeError('private-graph-or-index-url and private-source')
        return 'accepted-projection'
    target,attribute=write_attribute
    setattr(target,attribute,remote_write)
    async def get_projection(page,revision):
        return projection
    worker.clients.projection=get_projection
    async def call(method,path,target,token=None,body=None):
        calls.append(path)
        if path.endswith('/lease'):
            return {'items':[{'id':'private-event','page_id':projection['page_id'],'revision_id':projection['revision_id'],'lease_token':'private-lease','created_at':(datetime.now(UTC)-timedelta(seconds=100)).isoformat()}]}
        if phase['value']=='ack_unknown':
            raise TimeoutError('private-ack-failure')
        return {'acked':True}
    worker.clients.call=call
    def total(operation,outcome):
        return metrics.registry.get_sample_value('knowledge_domain_operations_total',{'service':service,'component':'projection','operation':operation,'outcome':outcome}) or 0
    with pytest.raises(RuntimeError):
        await worker.once()
    assert total('project','error')==1
    assert total('project','projected')==0
    assert not any(path.endswith('/ack') for path in calls)
    assert await catalog.revisions(projection['page_id'])==[]
    phase['value']='ack_unknown'
    with pytest.raises(TimeoutError):
        await worker.once()
    assert total('project','projected')==1
    assert total('ack','timeout')==1
    assert total('delivery','success')==0
    assert len(await catalog.revisions(projection['page_id']))==1
    phase['value']='success'
    assert await worker.once()
    assert total('project','already_projected')==1
    assert total('delivery','success')==1
    assert metrics.registry.get_sample_value('knowledge_projection_delivery_age_seconds_count',{'service':service})==1
    assert metrics.registry.get_sample_value('knowledge_projection_delivery_age_seconds_sum',{'service':service})>=100
    output=generate_latest(metrics.registry).decode()
    for secret in ('private-','page:pay','revision-a'):
        assert secret not in output