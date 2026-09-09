from pydantic import Field
from knowledge_platform.common.config import Settings


class IngestSettings(Settings):
    service_name: str = 'ingest'
    ingest_encryption_key: str = Field(default='', repr=False)
    ingest_oidc_token_url: str = ''
    ingest_oidc_client_id: str = ''
    ingest_oidc_client_secret: str = Field(default='', repr=False)
    ingest_s3_endpoint_url: str = 'http://seaweed-s3:8333'
    ingest_s3_bucket: str = 'knowledge-raw'
    ingest_s3_access_key_id: str = Field(default='', repr=False)
    ingest_s3_secret_access_key: str = Field(default='', repr=False)
    ingest_s3_region: str = 'us-east-1'
    ingest_temporal_address: str = 'temporal:7233'
    ingest_temporal_namespace: str = 'default'
    ingest_temporal_task_queue: str = 'knowledge-ingest'
    ingest_max_snapshot_bytes: int = Field(default=64_000_000, ge=1, le=1_000_000_000)
    ingest_max_snapshot_files: int = Field(default=10_000, ge=1, le=100_000)
    ingest_max_git_disk_bytes: int = Field(default=256_000_000, ge=1)
    ingest_git_timeout_seconds: int = Field(default=120, ge=1, le=900)
    ingest_preview_concurrency: int = Field(default=2, ge=1, le=8)
    ingest_auto_sync_enabled: bool = True
    ingest_auto_sync_tick_seconds: float = Field(default=60, ge=5, le=3600)
