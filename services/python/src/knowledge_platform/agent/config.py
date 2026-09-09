from pydantic import Field, model_validator

from knowledge_platform.common.config import Settings


class AgentSettings(Settings):
    service_name: str = 'agent'
    agent_encryption_key: str = Field(default='', repr=False)
    agent_poll_seconds: float = Field(default=1, ge=0.01, le=30)
    agent_event_poll_seconds: float = Field(default=0.5, ge=0.01, le=5)
    agent_lease_seconds: int = Field(default=15, ge=2, le=60)
    agent_heartbeat_seconds: float = Field(default=2, ge=0.1, le=10)
    agent_worker_concurrency: int = Field(default=10, ge=1, le=20)
    agent_max_response_bytes: int = Field(default=4_000_000, ge=1000, le=8_000_000)
    agent_max_context_chars: int = Field(default=100_000, ge=2000, le=500_000)
    agent_sse_send_timeout_seconds: float = Field(default=2, ge=0.1, le=10)
    agent_sse_lifetime_seconds: float = Field(default=190, ge=1, le=200)

    @model_validator(mode='after')
    def lease_order(self):
        if self.agent_heartbeat_seconds * 2 >= self.agent_lease_seconds:
            raise ValueError('Heartbeat interval must be below half the lease duration')
        return self