from pydantic import Field

from knowledge_platform.common.config import Settings


class KnowledgeSettings(Settings):
    knowledge_lifecycle_principal_id: str = ""
    knowledge_lifecycle_oidc_token_url: str = ""
    knowledge_lifecycle_oidc_client_id: str = ""
    knowledge_lifecycle_oidc_client_secret: str = Field(default="", repr=False)
    knowledge_lifecycle_space_ids: list[str] = Field(default_factory=list, max_length=100)
    knowledge_lifecycle_temporal_address: str = "temporal:7233"
    knowledge_lifecycle_temporal_namespace: str = "default"
    knowledge_lifecycle_task_queue: str = "knowledge-lifecycle"
