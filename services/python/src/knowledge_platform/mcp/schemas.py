"""Bounded input contracts; no arbitrary tools, service URLs or supplied principals."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator

from knowledge_platform.common.evidence import EvidenceRef, Key


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def unique(value):
    if len(value) != len(set(value)):
        raise ValueError("Duplicate identifiers")
    return value


def aware(value):
    if value is not None and value.utcoffset() is None:
        raise ValueError("Timestamp requires UTC offset")
    return value


class Scope(Strict):
    space_ids: list[Key] = Field(min_length=1, max_length=100)
    as_of: datetime | None = None
    known_at: datetime | None = None
    include_historical: bool = False
    _unique = field_validator("space_ids")(unique)
    _aware = field_validator("as_of", "known_at")(aware)


class Relations(Strict):
    types: list[Literal["depends_on", "uses", "owns", "cites", "governed_by", "caused", "fixed", "supersedes", "contradicts"]] = Field(default_factory=lambda: ["depends_on", "uses"], min_length=1, max_length=9)
    direction: Literal["outgoing", "incoming", "both"] = "outgoing"
    hops: int = Field(default=1, ge=1, le=2, strict=True)
    _unique = field_validator("types")(unique)


class Search(Scope):
    query: str = Field(min_length=1, max_length=8192)
    relation: Relations | None = None
    limit: int = Field(default=12, ge=1, le=12, strict=True)

    @field_validator("query")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Nonblank question required")
        return value


class Traverse(Scope):
    seed_fragment_ids: list[Key] = Field(min_length=1, max_length=30)
    relation: Relations = Field(default_factory=Relations)
    _unique_seeds = field_validator("seed_fragment_ids")(unique)


class Timeline(Scope):
    include_historical: Literal[True] = True
    page_ids: list[Key] = Field(min_length=1, max_length=20)
    limit: int = Field(default=50, ge=1, le=100, strict=True)
    _unique_pages = field_validator("page_ids")(unique)


class GetPage(Strict):
    operation: Literal["page"] = "page"
    page_id: Key
    revision_id: Key | None = None


class GetEvidence(Strict):
    operation: Literal["evidence"]
    evidence: EvidenceRef


class CreateSession(Strict):
    operation: Literal["create_session"]
    title: str = Field(default="MCP conversation", min_length=1, max_length=160)


AgentKey = Annotated[str, Field(min_length=1, max_length=256, pattern=r"^[A-Za-z0-9_.:-]+$")]


class StartRun(Strict):
    operation: Literal["start"]
    agent_id: AgentKey
    session_id: AgentKey
    question: str = Field(min_length=1, max_length=8192)
    space_ids: list[AgentKey] = Field(min_length=1, max_length=100)
    idempotency_key: AgentKey
    configuration_id: AgentKey | None = None
    as_of: datetime | None = None
    known_at: datetime | None = None
    _unique = field_validator("space_ids")(unique)
    _aware = field_validator("as_of", "known_at")(aware)
    _nonblank = field_validator("question")(Search.nonblank)


class ReadRun(Strict):
    operation: Literal["get", "cancel"]
    run_id: AgentKey


class Feedback(Strict):
    run_id: AgentKey
    rating: Literal["helpful", "unhelpful", "incorrect"]
    comment: str = Field(default="", max_length=2000)
    idempotency_key: AgentKey


TOOL_INPUTS = {
    "search": TypeAdapter(Search),
    "get": TypeAdapter(GetPage | GetEvidence),
    "traverse": TypeAdapter(Traverse),
    "timeline": TypeAdapter(Timeline),
    "ask": TypeAdapter(CreateSession | StartRun | ReadRun),
    "feedback": TypeAdapter(Feedback),
}


def input_schema(name):
    schema = TOOL_INPUTS[name].json_schema()
    # MCP inputSchema is an object schema even when operations use an anyOf union.
    return {"type": "object", **schema}
