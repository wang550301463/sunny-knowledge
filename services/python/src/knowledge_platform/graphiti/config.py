from pydantic import Field

from knowledge_platform.common.config import Settings


class GraphitiSettings(Settings):
    service_name: str = 'graphiti'
    graphiti_neo4j_uri: str = 'bolt://neo4j:7687'
    graphiti_neo4j_user: str = 'neo4j'
    graphiti_neo4j_password: str = Field(default='', repr=False)
    graphiti_neo4j_database: str = 'neo4j'
    graphiti_namespace: str = Field(default='knowledge-v2', min_length=1, max_length=128)
    graphiti_oidc_token_url: str = ''
    graphiti_oidc_client_id: str = ''
    graphiti_oidc_client_secret: str = Field(default='', repr=False)
    graphiti_poll_seconds: float = Field(default=2, ge=0.1, le=60)
    graphiti_reconcile_seconds: float = Field(default=60, ge=1, le=3600)
    graphiti_worker_timeout_seconds: float = Field(default=240, ge=10, le=270)
    graphiti_query_timeout_seconds: float = Field(default=5, ge=0.1, le=60)
    graphiti_max_policy_records: int = Field(default=10000, ge=1, le=50000)