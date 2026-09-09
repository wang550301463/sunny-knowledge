"""Strict retrieval boundary DTOs. Scores are rankings, never confidence."""
from __future__ import annotations

from datetime import datetime
from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Key = Annotated[str, Field(min_length=1, max_length=512)]
RelationType = Literal["depends_on", "uses", "owns", "cites", "governed_by", "caused", "fixed", "supersedes", "contradicts"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RetrievalError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def unavailable(code="dependency_unavailable", message="Required retrieval dependency unavailable"):
    return RetrievalError(503, code, message)


def aware(value):
    if value is not None and value.utcoffset() is None:
        raise ValueError("Timestamp requires UTC offset")
    return value


class EvidenceRef(StrictModel):
    resource_id: Key
    revision_id: Key
    source_id: Key
    source_revision: Key
    path: str = Field(min_length=1, max_length=2048)
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    kind: Literal["code", "markdown", "ticket", "policy", "source_manifest"]

    _aware = field_validator("valid_from", "valid_until")(aware)

    @model_validator(mode="after")
    def valid_reference(self):
        if self.end_line < self.start_line or "\\" in self.path or "\x00" in self.path or self.path.startswith("/") or ".." in PurePosixPath(self.path).parts:
            raise ValueError("Invalid source location")
        if self.valid_from and self.valid_until and self.valid_from >= self.valid_until:
            raise ValueError("Invalid validity interval")
        return self


class Policy(StrictModel):
    space_id: Key
    resource_id: Key
    space_read_subjects: list[str]
    resource_read_subjects: list[str] | None
    acl_version: int = Field(ge=0)
    auth_epoch: int = Field(ge=0)
    acl_domain: str = ""


class Fragment(StrictModel):
    id: Key
    page_id: Key
    revision_id: Key
    version: int = Field(ge=1)
    space_id: Key
    title: str
    text: str = Field(max_length=65536)
    kind: Literal["fact", "inference", "gap", "narrative"]
    entity_ids: list[str]
    evidence: list[EvidenceRef]
    read_clauses: list[list[str]]
    acl_domain: str
    acl_epoch: int = Field(ge=0)
    acl_version: int = Field(ge=0)
    state: Literal["valid", "stale", "retracted"]
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    known_at: datetime
    is_current: bool
    embedding_configuration_id: Key
    embedding_dimensions: int = Field(ge=1, le=4096)
    embedding: list[float] | None = None

    _aware = field_validator("valid_from", "valid_until", "known_at")(aware)


class Scope(StrictModel):
    space_ids: list[Key] = Field(min_length=1, max_length=100)
    as_of: datetime | None = None
    known_at: datetime | None = None
    include_historical: bool = False

    _aware = field_validator("as_of", "known_at")(aware)

    @field_validator("space_ids")
    @classmethod
    def unique_scope(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("Duplicate scope")
        return value


class Relations(StrictModel):
    types: list[RelationType] = Field(default_factory=lambda: ["depends_on", "uses"], min_length=1, max_length=9)
    direction: Literal["outgoing", "incoming", "both"] = "outgoing"
    hops: int = Field(default=1, ge=1, le=2)


class SearchRequest(Scope):
    query: str = Field(min_length=1, max_length=8192)
    embedding_configuration_id: Key | None = None
    rerank_configuration_id: Key | None = None
    relation: Relations | None = None
    limit: int = Field(default=12, ge=1, le=12)

    @field_validator("query")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Query must contain text")
        return value


class TraverseRequest(Scope):
    seed_fragment_ids: list[Key] = Field(min_length=1, max_length=30)
    relation: Relations = Field(default_factory=Relations)


class TimelineRequest(Scope):
    page_ids: list[Key] = Field(min_length=1, max_length=20)
    limit: int = Field(default=50, ge=1, le=100)


class GraphNode(StrictModel):
    id: Key
    fragment_ids: list[Key] = Field(min_length=1, max_length=100)


class GraphEdge(StrictModel):
    id: Key
    source: Key
    target: Key
    type: RelationType
    kind: Literal["fact", "inference"]
    fragment_ids: list[Key] = Field(min_length=1, max_length=100)


class GraphPath(StrictModel):
    node_ids: list[Key] = Field(min_length=1, max_length=3)
    edge_ids: list[Key] = Field(max_length=2)
    fragment_ids: list[Key] = Field(min_length=1, max_length=100)


class GraphResult(StrictModel):
    nodes: list[GraphNode] = Field(default_factory=list, max_length=100)
    edges: list[GraphEdge] = Field(default_factory=list, max_length=200)
    paths: list[GraphPath] = Field(default_factory=list, max_length=200)
    degraded: list[str] = Field(default_factory=list, max_length=10)