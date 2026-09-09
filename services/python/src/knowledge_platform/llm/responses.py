"""Safe model metadata and normalized inference wire contracts; no provider secrets.

ModelConfig fields are reused without its input-normalization validators: immutable
older configurations and already-emitted strings must not be rewritten on reads.
"""

from copy import deepcopy
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, create_model

from .schemas import Capability, ModelConfig, ToolCall

Timestamp = Annotated[str, Field(json_schema_extra={"format": "date-time"})]


class WireResponse(BaseModel):
    model_config = ConfigDict(extra="allow", hide_input_in_errors=True)
    __pydantic_extra__: dict[str, JsonValue]


VisibleConfig = create_model(
    "VisibleConfig", __base__=WireResponse, __module__=__name__,
    **{name: (field.annotation, deepcopy(field)) for name, field in ModelConfig.model_fields.items()},
)


class ModelView(VisibleConfig):
    id: str
    configuration_id: str
    version: int
    state: str
    has_credential: bool
    created_at: Timestamp
    created_by: str
    test_state: str
    capabilities: dict[str, bool]
    tested_at: Timestamp | None
    test_error_code: str | None


class ModelList(WireResponse):
    items: list[ModelView]
    next_cursor: str | None


class ModelVersions(WireResponse):
    items: list[ModelView]
    next_cursor: int | None


class AuditEvent(WireResponse):
    id: str
    configuration_id: str | None
    action: str
    actor_id: str
    # Native event-specific audit metadata, not the request/provider payload.
    details: dict[str, JsonValue]
    created_at: Timestamp


class AuditList(WireResponse):
    items: list[AuditEvent]


class Usage(WireResponse):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    search_units: int = 0


class UsageEvent(WireResponse):
    invocation_id: str
    configuration_id: str
    caller: str
    capability: Capability
    started_at: Timestamp
    outcome: str
    duration_ms: int | None
    request_id: str | None
    usage: Usage


class UsageList(WireResponse):
    items: list[UsageEvent]


class CapabilityTestResult(WireResponse):
    id: str
    configuration_id: str
    test_state: Literal["passed", "failed"]
    capabilities: dict[str, bool]
    dimensions: int | None
    error_code: str | None
    tested_at: Timestamp


class InferenceResult(WireResponse):
    configuration_id: str
    invocation_id: str
    usage: Usage
    request_id: str | None


class ChatResult(InferenceResult):
    content: str | None
    tool_calls: list[ToolCall]
    finish_reason: Literal["stop", "length", "tool_calls", "content_filter"]


class EmbeddingResult(InferenceResult):
    embeddings: list[list[int | float]]
    dimensions: int


class RankedDocument(WireResponse):
    index: int
    relevance_score: int | float


class RerankResult(InferenceResult):
    results: list[RankedDocument]


class ContentDelta(WireResponse):
    type: Literal["content_delta"]
    configuration_id: str
    invocation_id: str
    delta: str


class ToolCallDelta(WireResponse):
    type: Literal["tool_call_delta"]
    configuration_id: str
    invocation_id: str
    index: int
    tool_call: ToolCall


class Completed(ChatResult):
    type: Literal["completed"]


class StreamErrorDetail(WireResponse):
    code: str
    message: str


class StreamError(WireResponse):
    type: Literal["error"]
    error: StreamErrorDetail


StreamEvent = Annotated[ContentDelta | ToolCallDelta | Completed | StreamError, Field(discriminator="type")]