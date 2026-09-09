from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra='ignore')
    service_name: str = 'knowledge'
    database_url: str = ''
    service_private_key_file: str = '/run/knowledge/private.pem'
    service_public_keys_file: str = '/run/knowledge/public-keys.json'
    auth_url: str = 'http://auth:8080'
    iam_url: str = 'http://iam:8080'
    knowledge_url: str = 'http://knowledge:8080'
    ingest_url: str = 'http://ingest:8080'
    retrieval_url: str = 'http://retrieval:8080'
    llm_url: str = 'http://llm:8080'
    graphiti_url: str = 'http://graphiti:8080'
    agent_url: str = 'http://agent:8080'
    mcp_url: str = 'http://mcp:8080'
    channel_url: str = 'http://channel:8080'
    gateway_url: str = 'http://gateway:8080'
    public_web_url: str = 'http://localhost:18180'
    request_timeout: float = 20.0

    @classmethod
    def from_env(cls, service_name: str | None = None):
        return cls(**({'service_name': service_name} if service_name else {}))

    def url_for(self, target: str) -> str:
        if target not in {'gateway','iam','auth','knowledge','ingest','retrieval','llm','graphiti','agent','mcp','channel'}:
            raise ValueError('Unknown service target')
        return getattr(self, f'{target}_url').rstrip('/')