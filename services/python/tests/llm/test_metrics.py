import pytest
from sqlalchemy import select, text

from knowledge_platform.llm.models import UsageOutcome
from .test_http import create

pytestmark=pytest.mark.asyncio


def count(api,operation,outcome):
    return api.app.state.telemetry.registry.get_sample_value('knowledge_domain_operations_total',{'service':'llm','component':'model','operation':operation,'outcome':outcome}) or 0


@pytest.mark.parametrize('capability,body',[
    ('chat',{'messages':[{'role':'user','content':'secret-query-and-code'}]}),
    ('embedding',{'input':['secret-query-and-code']}),
    ('rerank',{'query':'secret-query-and-code','documents':['private-source'], 'top_n':1}),
])
async def test_actual_inference_and_usage_commit_export_no_private_model_or_input_labels(api,capability,body):
    model=await create(api,capability)
    route={'chat':'chat','embedding':'embeddings','rerank':'rerank'}[capability]
    result=await api.client.post('/internal/v1/'+route,json={'configuration_id':model['configuration_id'],**body},headers=api.headers('agent',False))
    assert result.status_code==200,result.text
    assert count(api,capability,'success')==1
    assert count(api,'usage_commit','success')==1
    async with api.store.session() as session:
        assert len((await session.scalars(select(UsageOutcome))).all())==1
    output=api.app.state.telemetry.metrics().decode()
    for secret in ('secret-query','private-source','SECRET-provider-key','provider-version-one',model['id'],model['configuration_id'],'provider.test','opaque-user-token'):
        assert secret not in output


async def test_failed_usage_transaction_never_counts_successful_inference_completion(api):
    model=await create(api,'embedding')
    async with api.store.engine.begin() as connection:
        await connection.execute(text('''CREATE FUNCTION reject_metric_usage() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'private-db-error'; END $$'''))
        await connection.execute(text('CREATE TRIGGER fail_usage BEFORE INSERT ON llm_usage_outcomes FOR EACH ROW EXECUTE FUNCTION reject_metric_usage()'))
    result=await api.client.post('/internal/v1/embeddings',json={'configuration_id':model['configuration_id'],'input':['private']},headers=api.headers('agent',False))
    assert result.status_code==503
    assert count(api,'embedding','success')==0
    assert count(api,'usage_commit','success')==0
    assert count(api,'usage_commit','error')==1
    async with api.store.session() as session:
        assert len((await session.scalars(select(UsageOutcome))).all())==0