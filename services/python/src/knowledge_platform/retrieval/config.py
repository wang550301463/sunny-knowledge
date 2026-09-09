from pydantic import Field, field_validator

from knowledge_platform.common.config import Settings


class RetrievalSettings(Settings):
    service_name: str = "retrieval"
    retrieval_es_url: str = "http://elasticsearch:9200"
    retrieval_es_index: str = "knowledge-fragments-v2"
    retrieval_es_api_key: str = Field(default="", repr=False)
    retrieval_embedding_configuration_id: str = ""
    retrieval_embedding_dimensions: int = Field(default=0, ge=0, le=4096)
    retrieval_rerank_configuration_id: str = ""
    retrieval_oidc_token_url: str = ""
    retrieval_oidc_client_id: str = ""
    retrieval_oidc_client_secret: str = Field(default="", repr=False)
    retrieval_poll_seconds: float = Field(default=2.0, ge=0.1, le=60)
    retrieval_reconcile_seconds: float = Field(default=60.0, ge=1, le=3600)
    retrieval_worker_timeout_seconds: float = Field(default=240.0, ge=10, le=270)
    retrieval_max_policy_domains: int = Field(default=10000, ge=1, le=50000)
    retrieval_policy_cache_seconds: float = Field(default=5, ge=0, le=30)
    retrieval_graph_timeout_seconds: float = Field(default=5, ge=0.1, le=30)

    @field_validator("retrieval_es_index")
    @classmethod
    def safe_index(cls, value):
        if not value or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for c in value) or value[0] in "-_":
            raise ValueError("Invalid Elasticsearch index name")
        return value