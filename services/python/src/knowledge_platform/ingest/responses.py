"""Public ingest wire views; source bytes, credentials and private checkpoints stay omitted.

Optional fields describe genuinely partial durable task states, not made-up defaults.
Routes use exclude_unset so old snapshots and extension fields round-trip unchanged.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from knowledge_platform.common.contracts import EvidenceRef
from knowledge_platform.knowledge.responses import RevisionProposal
from knowledge_platform.knowledge.schemas import PageContent

from .analyzers.models import Location

Timestamp = Annotated[str, Field(json_schema_extra={"format": "date-time"})]


class WireResponse(BaseModel):
    model_config = ConfigDict(extra="allow", hide_input_in_errors=True)
    __pydantic_extra__: dict[str, JsonValue]


class GitConfigView(WireResponse):
    url: str
    ref: str
    include_paths: list[str] = Field(default_factory=list)
    exclude_paths: list[str] = Field(default_factory=list)


class UploadConfigView(WireResponse):
    path: str


class PreviewSummary(WireResponse):
    source_revision: str
    file_count: int
    total_bytes: int


class SourceView(WireResponse):
    id: str
    name: str
    space_id: str
    resource_id: str
    kind: Literal["git", "markdown", "ticket"]
    version: int
    generation: int
    config: GitConfigView | UploadConfigView
    has_credential: bool
    state: str
    created_by: str
    created_at: Timestamp
    updated_at: Timestamp
    latest_task_id: str | None
    preview: PreviewSummary | None


class SourceList(WireResponse):
    items: list[SourceView]
    next_cursor: str | None


class PreviewFile(WireResponse):
    path: str
    size: int
    sha256: str
    kind: str
    diagnostics: list[str]


class PreviewView(PreviewSummary):
    source_id: str
    version: int
    diagnostics: list[str]
    files: list[PreviewFile]


class DiagnosticView(WireResponse):
    code: str
    message: str | None = None
    location: Location | None = None
    severity: Literal["info", "warning", "error"] | None = None
    path: str | None = None


class TaskResultItem(WireResponse):
    page_id: str
    path: str
    step_number: int
    status: str
    revision_id: str | None = None
    proposal_id: str | None = None
    error_code: str | None = None
    conflict_available: bool | None = None
    conflict_url: str | None = None
    resolution: str | None = None


class TaskResult(WireResponse):
    diagnostics: list[DiagnosticView] = Field(default_factory=list)
    diagnostics_truncated: bool = False
    items: list[TaskResultItem] = Field(default_factory=list)


class TaskView(WireResponse):
    id: str
    source_id: str
    source_version: int
    space_id: str
    operation: Literal["sync", "delete"]
    status: str
    stage: str
    error_code: str | None
    result: TaskResult
    created_at: Timestamp
    updated_at: Timestamp
    compiler_version: str | None
    schema_version: str | None


class TaskList(WireResponse):
    items: list[TaskView]
    next_cursor: str | None


class FrozenRequest(WireResponse):
    """Public portion of a frozen step; compiler/proposal/validity/deletion variants."""

    base_revision: str | None = None
    content: PageContent | None = None
    proof: EvidenceRef | None = None
    claim_ids: list[str] = Field(default_factory=list)
    state: Literal["stale", "retracted"] | None = None
    reason: str | None = None
    idempotency_key: str | None = None
    before_manifest: EvidenceRef | None = None
    after_manifest: EvidenceRef | None = None
    deleted_paths: list[str] = Field(default_factory=list)


class ConflictComparison(WireResponse):
    original_base_revision: str | None
    review_base_revision: str | None
    proposed_content_sha256: str
    current_content_sha256: str | None


class ConflictView(WireResponse):
    task_id: str
    step_number: int
    page_id: str
    source_version: int
    operation: str
    original_base_revision: str | None
    review_base_revision: str | None
    current_revision: str | None
    request: FrozenRequest
    comparison: ConflictComparison | None
    proposal_id: str | None
    can_propose: bool
    blocked_reason: str | None


# The proposal is returned unchanged by the canonical knowledge service.
ConflictProposalView = RevisionProposal