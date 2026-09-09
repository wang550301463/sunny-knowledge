from common.projection_metrics import exercise_delivery_metrics
from .test_worker import worker
from .test_projection import structured


async def test_real_graph_catalog_rollback_and_ack_uncertainty_never_count_delivery_success(catalog):
    current=worker()
    await exercise_delivery_metrics(current,catalog,structured(),'graphiti',(current.backend,'write'))