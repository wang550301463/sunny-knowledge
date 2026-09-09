"""Authorized retrieval outputs; rankings are not confidence or additional evidence."""

from copy import deepcopy
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, create_model

from .schemas import EvidenceRef, Fragment, GraphResult

Timestamp = Annotated[str, Field(json_schema_extra={"format": "date-time"})]


class WireResponse(BaseModel):
    model_config = ConfigDict(extra="allow", hide_input_in_errors=True)
    __pydantic_extra__: dict[str, JsonValue]


# Exactly the private fields excluded by RetrievalService.assemble. Tests compare
# the resulting declaration to that real serializer so additions cannot silently drift.
_PRIVATE_FRAGMENT_FIELDS = {
    "embedding", "read_clauses", "policy_fingerprint", "acl_domain", "acl_epoch",
    "acl_version", "embedding_configuration_id", "embedding_dimensions",
}
FragmentView = create_model(
    "FragmentView", __base__=WireResponse, __module__=__name__,
    **{name: (field.annotation, deepcopy(field)) for name, field in Fragment.model_fields.items()
       if name not in _PRIVATE_FRAGMENT_FIELDS},
)


class RankedFragment(FragmentView):
    primary: bool
    rrf_score: float | None
    rerank_score: float | None
    citation_ids: list[str]
    url: str


class OriginalEvidence(WireResponse):
    id: str
    evidence: EvidenceRef
    space_id: str
    excerpt: str
    sha256: str


class SearchResult(WireResponse):
    items: list[RankedFragment]
    evidence: list[OriginalEvidence]
    graph: GraphResult
    degraded: list[str]
    gaps: list[str]
    auth_epoch: int
    as_of: Timestamp | None
    known_at: Timestamp | None
    time_basis: Literal["source_revision_and_knowledge_time"]
    deployment_state: Literal["unknown_without_deployment_evidence"]


class SourceRevision(WireResponse):
    source_id: str
    source_revision: str


class TimelineEvent(WireResponse):
    page_id: str
    revision_id: str
    version: int
    known_at: Timestamp
    state: Literal["valid", "stale", "retracted"]
    publication_kind: str
    source_revisions: list[SourceRevision]


class TimelineResult(WireResponse):
    items: list[TimelineEvent]
    truncated: bool
    auth_epoch: int
    time_basis: Literal["source_revision_and_knowledge_time"]
    deployment_state: Literal["unknown_without_deployment_evidence"]