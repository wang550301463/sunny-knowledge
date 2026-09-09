"""Public response shapes; no hidden worker checkpoints, credentials, or reasoning."""

from copy import deepcopy
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, RootModel, create_model

from knowledge_platform.common.evidence import EvidenceRef

from .schemas import AgentConfig, Answer, Budget, Key, ToolName


class WireResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    __pydantic_extra__: dict[str, JsonValue]


# A read projection may have no currently visible spaces. It must not run the
# create-time validators that require nonempty scopes or fill a default tool list.
_fields = {name: (field.annotation, deepcopy(field)) for name, field in AgentConfig.model_fields.items()}
_fields["space_ids"] = (list[Key], Field(max_length=100))
VisibleAgentConfig = create_model("VisibleAgentConfig", __base__=WireResponse, __module__=__name__, **_fields)


class AgentDefinition(WireResponse):
    id: str
    configuration_id: str
    version: int
    name: str
    description: str
    owner_space_id: str
    created_by: str
    shared: bool
    published_configuration_id: str | None
    config: VisibleAgentConfig
    created_at: str


class AgentList(WireResponse):
    items: list[AgentDefinition]
    next_cursor: str | None = None


class Citation(WireResponse):
    id: str
    page_id: str
    revision_id: str = Field(description="Wiki revision ID, distinct from evidence.revision_id (source snapshot ID).")
    space_id: str
    evidence: EvidenceRef
    excerpt: str
    url: str


RunStatus = Literal["queued", "running", "completed", "partial", "failed", "cancelled"]


class RunMetadata(WireResponse):
    id: str
    session_id: str
    status: RunStatus
    event_seq: int
    created_at: str
    finished_at: str | None


class HiddenRun(RunMetadata):
    content_hidden: Literal[True]


class VisibleRun(RunMetadata):
    content_hidden: Literal[False]
    entrypoint: Literal["web", "channel"]
    agent_id: str
    configuration_id: str
    actual_scope: list[str]
    question: str
    answer: Answer | None
    answer_complete: bool
    citations: list[Citation]
    usage: dict[str, int | float]
    error_code: str | None
    budget: Budget
    rounds: int
    tool_calls: int


class AgentRunView(RootModel[Annotated[HiddenRun | VisibleRun, Field(discriminator="content_hidden")]]):
    pass


class RunList(WireResponse):
    items: list[AgentRunView]
    next_cursor: str | None = None


class RunState(WireResponse):
    id: str
    status: RunStatus


class FeedbackReceipt(WireResponse):
    id: str
    run_id: str


class EventBase(WireResponse):
    seq: int = Field(ge=1)


class EmptyData(WireResponse):
    pass


class QueuedEvent(EventBase):
    type: Literal["queued"]
    data: EmptyData


class StartedData(WireResponse):
    resumed: bool


class StartedEvent(EventBase):
    type: Literal["started"]
    data: StartedData


class ContextData(WireResponse):
    omitted_history: dict[str, int]


class ContextEvent(EventBase):
    type: Literal["context_ready"]
    data: ContextData


class GenerationData(WireResponse):
    status: Literal["started", "tools_requested", "decision_complete", "tool_budget_reached"]
    round: int | None = None
    stage: Literal["final", "decision"] | None = None


class GenerationEvent(EventBase):
    type: Literal["generation"]
    data: GenerationData


class ToolData(WireResponse):
    name: ToolName
    call_id: str
    status: Literal["started", "completed"]


class ToolEvent(EventBase):
    type: Literal["tool"]
    data: ToolData


class AnswerBlock(WireResponse):
    index: int = Field(ge=0)
    kind: Literal["fact", "inference", "gap"]
    text: str
    citation_ids: list[str]


class AnswerBlockEvent(EventBase):
    type: Literal["answer_block"]
    data: AnswerBlock


class CompletedData(WireResponse):
    status: Literal["completed", "partial", "failed", "cancelled"]
    error_code: str | None
    run: AgentRunView


class CompletedEvent(EventBase):
    type: Literal["completed"]
    data: CompletedData


class RunEvent(RootModel[Annotated[QueuedEvent | StartedEvent | ContextEvent | GenerationEvent | ToolEvent | AnswerBlockEvent | CompletedEvent, Field(discriminator="type")]]):
    pass
