from types import SimpleNamespace

from knowledge_platform.common.domain_metrics import DomainMetrics
from knowledge_platform.retrieval.config import RetrievalSettings
from knowledge_platform.retrieval.schemas import SearchRequest
from knowledge_platform.retrieval.worker import ProjectionWorker
from common.projection_metrics import exercise_delivery_metrics
from .test_postgres import catalog  # noqa: F401
from .test_service import setup,Clients
from .test_authorization import Auth
from .test_projection import projection
from .test_worker import Tokens


async def test_real_projection_rollback_and_ack_uncertainty_do_not_report_delivery_success(catalog):
    auth=Auth();clients=Clients(auth);index=SimpleNamespace(put=None)
    worker=ProjectionWorker(RetrievalSettings(retrieval_embedding_configuration_id='model-v1',retrieval_embedding_dimensions=3),auth,catalog,index,clients,Tokens())
    await exercise_delivery_metrics(worker,catalog,projection(),'retrieval',(index,'put'))


async def test_search_local_measurement_excludes_both_external_model_waits():
    service,_auth,clients,index=setup()
    ticks=[1.0];metrics=DomainMetrics('retrieval',clock=lambda:ticks[0]);service.metrics=metrics
    embedding,rerank,search=clients.embeddings,clients.rerank,index.search
    async def delayed_embedding(*args,**kwargs):
        ticks[0]+=100
        return await embedding(*args,**kwargs)
    async def delayed_rerank(*args,**kwargs):
        ticks[0]+=200
        return await rerank(*args,**kwargs)
    async def local_es(*args,**kwargs):
        ticks[0]+=2
        return await search(*args,**kwargs)
    clients.embeddings,clients.rerank,index.search=delayed_embedding,delayed_rerank,local_es
    value=await service.search('actor',SearchRequest(space_ids=['engineering'],query='private-query'))
    assert value['items']
    def duration(operation):
        return metrics.registry.get_sample_value('knowledge_domain_operation_duration_seconds_sum',{'service':'retrieval','component':'retrieval','operation':operation,'outcome':'success'})
    assert duration('search')==304
    assert duration('search_local')==4
    assert duration('embedding')==100 and duration('rerank')==200