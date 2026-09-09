"""Native protocol headers must remain usable without granting caller identity."""
import json
from pathlib import Path

import pytest

from knowledge_platform.generated_contracts.client import execute_json, prepare
from knowledge_platform.generated_contracts.operations import OPERATIONS

ROOT=Path(__file__).resolve().parents[3]


@pytest.mark.parametrize('path',[
    '/api/v1/pages','/api/v1/pages/{page_id}/proposals','/internal/v1/pages/publish',
    '/internal/v1/pages/{page_id}/validity','/internal/v1/pages/{page_id}/source-deletion',
])
def test_native_source_fence_headers_are_conditional_and_never_gateway_headers(path):
    doc=json.loads((ROOT/'contracts/generated/openapi/knowledge.json').read_text())
    operation=doc['paths'][path]['post']
    headers=[p for p in operation['parameters'] if p['in']=='header']
    assert {p['name'] for p in headers}=={'X-Ingest-Source-Id','X-Ingest-Generation'}
    assert all(p['x-required-for-caller']=='ingest' and p['required']==path.startswith('/internal/') for p in headers)
    op=OPERATIONS[operation['operationId']]
    values={name:'page:test' for name in op.path_parameters}
    args={'path':values,'body':{},'headers':{'X-Ingest-Source-Id':'git:test','X-Ingest-Generation':'1'}}
    assert dict(prepare(op,**args).headers)['X-Ingest-Generation']=='1'
    if path.startswith('/internal/'):
        with pytest.raises(ValueError):prepare(op,path=values,body={})
    else:
        gateway=json.loads((ROOT/'contracts/generated/openapi/gateway.json').read_text())
        public=OPERATIONS[gateway['paths'][path]['post']['operationId']]
        assert not public.header_parameters
        with pytest.raises(ValueError):prepare(public,**args)


@pytest.mark.parametrize('path',[
    '/api/v1/runs/{run_id}/events','/internal/v1/runs/{run_id}/events','/internal/v1/channel/runs/{run_id}/events',
])
def test_native_sse_cursor_header_preserves_resume_precedence(path):
    doc=json.loads((ROOT/'contracts/generated/openapi/agent.json').read_text())
    operation=doc['paths'][path]['get']
    cursor=next(p for p in operation['parameters'] if p['in']=='header')
    assert cursor['name'].lower()=='last-event-id'
    assert cursor['schema']['pattern']=='^[0-9]{1,18}$'
    op=OPERATIONS[operation['operationId']]
    result=prepare(op,path={'run_id':'run:test'},query={'cursor':1},headers={'Last-Event-ID':'4'})
    assert result.path.endswith('?cursor=1') and dict(result.headers)['Last-Event-ID']=='4'
    with pytest.raises(ValueError):
        prepare(op,path={'run_id':'run:test'},headers={'Last-Event-ID':'4','last-event-id':'5'})


@pytest.mark.asyncio
async def test_header_aware_operations_do_not_silently_drop_headers_in_json_transport():
    op=OPERATIONS['knowledge_post_internal_v1_pages_publish']
    result=prepare(op,body={},headers={'X-Ingest-Source-Id':'git:test','X-Ingest-Generation':'1'})
    class UnexpectedTransport:
        async def request(self,*_args,**_kwargs):
            raise AssertionError('A headerless transport must not be called')
    with pytest.raises(ValueError):await execute_json(UnexpectedTransport(),op,result,token='opaque')