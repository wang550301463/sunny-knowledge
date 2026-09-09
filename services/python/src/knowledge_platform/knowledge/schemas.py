"""Strict HTTP contracts for immutable canonical knowledge and source evidence."""

from __future__ import annotations

from datetime import datetime
from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Key = Annotated[str, Field(min_length=1, max_length=512)]
EntityType = Literal[
    "Service",
    "Module",
    "File",
    "Person",
    "Dependency",
    "Decision",
    "Policy",
    "Incident",
    "Change",
    "Procedure",
]
RelationType = Literal[
    "depends_on",
    "uses",
    "owns",
    "cites",
    "governed_by",
    "caused",
    "fixed",
    "supersedes",
    "contradicts",
]
EvidenceKind = Literal["code", "markdown", "ticket", "policy", "source_manifest"]
MANIFEST_PATH = ".__knowledge__/manifest.json"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceRef(StrictModel):
    resource_id: Key
    revision_id: Key
    source_id: Key
    source_revision: Key
    path: Annotated[str, Field(min_length=1, max_length=2048)]
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    kind: EvidenceKind

    @field_validator("path")
    @classmethod
    def safe_relative_path(cls, value: str) -> str:
        if (
            "\\" in value
            or "\x00" in value
            or value.startswith("/")
            or ".." in PurePosixPath(value).parts
        ):
            raise ValueError("path must be a relative source path without traversal")
        return value

    @model_validator(mode="after")
    def fixed_range(self):
        if self.end_line < self.start_line:
            raise ValueError("end_line must be >= start_line")
        validate_interval(self.valid_from, self.valid_until)
        return self


def validate_interval(start: datetime | None, end: datetime | None) -> None:
    for value in (start, end):
        if value is not None and value.utcoffset() is None:
            raise ValueError("timestamps must include a timezone")
    if start is not None and end is not None and end <= start:
        raise ValueError("valid_until must follow valid_from")


class EntityIdentity(StrictModel):
    id: Key
    name: Annotated[str, Field(min_length=1, max_length=512)]
    type: EntityType


class RelationIdentity(StrictModel):
    source_id: Key
    target_id: Key
    type: RelationType


class Claim(StrictModel):
    id: Key
    text: Annotated[str, Field(min_length=1, max_length=100_000)]
    kind: Literal["fact", "inference", "gap"] = "fact"
    entity_type: EntityType | None = None
    entity: EntityIdentity | None = None
    relation: RelationIdentity | None = None
    evidence: list[EvidenceRef] = Field(default_factory=list, max_length=100)
    state: Literal["valid", "stale", "retracted"] = "valid"
    valid_from: datetime | None = None
    valid_until: datetime | None = None

    @model_validator(mode="after")
    def supported(self):
        if self.kind == "fact" and not self.evidence:
            raise ValueError("formal facts require original source evidence")
        if self.entity and (self.entity.id != self.id or self.entity.type != self.entity_type):
            raise ValueError("entity identity and type must match the canonical claim")
        if self.relation and (self.entity or self.kind == "gap" or not self.evidence):
            raise ValueError("relationships require distinct, evidenced fact or inference claims")
        validate_interval(self.valid_from, self.valid_until)
        return self


class PageContent(StrictModel):
    title: Annotated[str, Field(min_length=1, max_length=512)]
    markdown: Annotated[str, Field(max_length=4_000_000)]
    entity_type: EntityType | None = None
    claims: list[Claim] = Field(default_factory=list, max_length=1000)
    evidence: list[EvidenceRef] = Field(default_factory=list, max_length=1000)
    state: Literal["valid", "stale", "retracted"] = "valid"
    valid_from: datetime | None = None
    valid_until: datetime | None = None

    @model_validator(mode="after")
    def unique_claims(self):
        if len({claim.id for claim in self.claims}) != len(self.claims):
            raise ValueError("claim IDs must be unique")
        validate_interval(self.valid_from, self.valid_until)
        return self

    def supports(self) -> list[EvidenceRef]:
        refs = self.evidence + [ref for claim in self.claims for ref in claim.evidence]
        return list({ref.model_dump_json(): ref for ref in refs}.values())


class PageCreate(StrictModel):
    id: Key | None = None
    space_id: Key
    content: PageContent
    reason: Annotated[str, Field(min_length=1, max_length=4096)] = "Initial page proposal"
    idempotency_key: Key | None = None


class ProposalCreate(StrictModel):
    base_revision: Key | None
    content: PageContent
    kind: Literal["formal_change", "entity_merge", "schema_change", "digest"] = "formal_change"
    reason: Annotated[str, Field(min_length=1, max_length=4096)]
    idempotency_key: Key | None = None


class ReviewAction(StrictModel):
    reason: Annotated[str, Field(min_length=1, max_length=4096)]


class RollbackRequest(StrictModel):
    base_revision: Key
    revision_id: Key
    reason: Annotated[str, Field(min_length=1, max_length=4096)]


class SourceManifest(StrictModel):
    type: Literal["source_manifest"]
    source_id: Key
    source_revision: Key
    paths: list[Annotated[str, Field(min_length=1, max_length=2048)]] = Field(max_length=50_000)

    @field_validator("paths")
    @classmethod
    def canonical_paths(cls, paths):
        if paths != sorted(set(paths)):
            raise ValueError("manifest paths must be sorted and unique")
        for path in paths:
            EvidenceRef.safe_relative_path(path)
            if (
                path == "."
                or PurePosixPath(path).as_posix() != path
                or PurePosixPath(path).parts[0] == ".__knowledge__"
            ):
                raise ValueError("manifest must contain canonical original source paths")
        return paths


class SourceSnapshotCreate(StrictModel):
    source_id: Key
    source_revision: Key
    resource_id: Key
    space_id: Key
    path: str = Field(min_length=1, max_length=2048)
    kind: EvidenceKind
    text: str = Field(max_length=4_000_000)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    object_key: str = Field(min_length=1, max_length=2048)

    _relative_path = field_validator("path")(EvidenceRef.safe_relative_path.__func__)

    @model_validator(mode="after")
    def manifest_namespace(self):
        if self.kind == "source_manifest":
            manifest = SourceManifest.model_validate_json(self.text)
            if (
                self.path != MANIFEST_PATH
                or manifest.source_id != self.source_id
                or manifest.source_revision != self.source_revision
            ):
                raise ValueError("manifest must match its source identity and reserved path")
        elif (
            PurePosixPath(self.path).parts and PurePosixPath(self.path).parts[0] == ".__knowledge__"
        ):
            raise ValueError("original sources cannot use the manifest namespace")
        return self


class SourceGeneration(StrictModel):
    source_id: Key
    generation: int = Field(strict=True, ge=1, le=9_223_372_036_854_775_807)

    @field_validator("source_id")
    @classmethod
    def source_identifier(cls, value):
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("source identifier cannot contain control characters")
        return value


class SourceFenceAdvance(SourceGeneration):
    space_id: Key
    resource_id: Key


class CompilerProof(StrictModel):
    compiler_version: Key
    schema_version: Key
    snapshot_ids: list[Key] = Field(min_length=1, max_length=1000)
    idempotency_key: Key


class DeterministicPublish(StrictModel):
    page_id: Key
    space_id: Key
    base_revision: Key | None
    content: PageContent
    proof: CompilerProof


class ValidityUpdate(StrictModel):
    base_revision: Key
    proof: EvidenceRef
    claim_ids: list[Key] = Field(min_length=1, max_length=1000)
    state: Literal["stale", "retracted"]
    reason: Annotated[str, Field(min_length=1, max_length=4096)]
    idempotency_key: Key | None = None


class SourceDeletion(StrictModel):
    base_revision: Key
    before_manifest: EvidenceRef
    after_manifest: EvidenceRef
    deleted_paths: list[Annotated[str, Field(min_length=1, max_length=2048)]] = Field(
        min_length=1, max_length=1000
    )
    reason: Annotated[str, Field(min_length=1, max_length=4096)]
    idempotency_key: Key | None = None

    _paths = field_validator("deleted_paths")(SourceManifest.canonical_paths.__func__)


class LeaseRequest(StrictModel):
    consumer: Literal["retrieval", "graphiti"]
    limit: int = Field(default=20, ge=1, le=100)
    lease_seconds: int = Field(default=30, ge=5, le=300)


class AckRequest(StrictModel):
    lease_token: Key


class EvidenceAuthorizeRequest(StrictModel):
    evidence: list[EvidenceRef] = Field(max_length=100)


class KnowledgeError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


class PageAuthorization(StrictModel):
    page_id: Key
    revision_id: Key


class PageAuthorizeRequest(StrictModel):
    pages: list[PageAuthorization] = Field(max_length=100)
    include_historical: bool = False
    as_of: datetime | None = None

    @field_validator("as_of")
    @classmethod
    def aware_as_of(cls, value):
        validate_interval(value, None)
        return value

    @field_validator("path")
    @classmethod
    def safe_relative_path(cls, value: str) -> str:
        if (
            "\\" in value
            or "\x00" in value
            or value.startswith("/")
            or ".." in PurePosixPath(value).parts
        ):
            raise ValueError("path must be a relative source path without traversal")
        return value

    @model_validator(mode="after")
    def fixed_range(self):
        if self.end_line < self.start_line:
            raise ValueError("end_line must be >= start_line")
        validate_interval(self.valid_from, self.valid_until)
        return self


def validate_interval(start: datetime | None, end: datetime | None) -> None:
    for value in (start, end):
        if value is not None and value.utcoffset() is None:
            raise ValueError("timestamps must include a timezone")
    if start is not None and end is not None and end <= start:
        raise ValueError("valid_until must follow valid_from")


class Claim(StrictModel):
    id: Key
    text: Annotated[str, Field(min_length=1, max_length=100_000)]
    kind: Literal["fact", "inference", "gap"] = "fact"
    entity_type: EntityType | None = None
    evidence: list[EvidenceRef] = Field(default_factory=list, max_length=100)
    state: Literal["valid", "stale", "retracted"] = "valid"
    valid_from: datetime | None = None
    valid_until: datetime | None = None

    @model_validator(mode="after")
    def supported(self):
        if self.kind == "fact" and not self.evidence:
            raise ValueError("formal facts require original source evidence")
        validate_interval(self.valid_from, self.valid_until)
        return self


class PageContent(StrictModel):
    title: Annotated[str, Field(min_length=1, max_length=512)]
    markdown: Annotated[str, Field(max_length=4_000_000)]
    entity_type: EntityType | None = None
    claims: list[Claim] = Field(default_factory=list, max_length=1000)
    evidence: list[EvidenceRef] = Field(default_factory=list, max_length=1000)
    state: Literal["valid", "stale", "retracted"] = "valid"
    valid_from: datetime | None = None
    valid_until: datetime | None = None

    @model_validator(mode="after")
    def unique_claims(self):
        if len({claim.id for claim in self.claims}) != len(self.claims):
            raise ValueError("claim IDs must be unique")
        validate_interval(self.valid_from, self.valid_until)
        return self

    def supports(self) -> list[EvidenceRef]:
        refs = self.evidence + [ref for claim in self.claims for ref in claim.evidence]
        return list({ref.model_dump_json(): ref for ref in refs}.values())


class PageCreate(StrictModel):
    id: Key | None = None
    space_id: Key
    content: PageContent
    reason: Annotated[str, Field(min_length=1, max_length=4096)] = "Initial page proposal"


class ProposalCreate(StrictModel):
    base_revision: Key | None
    content: PageContent
    kind: Literal["formal_change", "entity_merge", "schema_change", "digest"] = "formal_change"
    reason: Annotated[str, Field(min_length=1, max_length=4096)]


class ReviewAction(StrictModel):
    reason: Annotated[str, Field(min_length=1, max_length=4096)]


class RollbackRequest(StrictModel):
    base_revision: Key
    revision_id: Key
    reason: Annotated[str, Field(min_length=1, max_length=4096)]


class SourceSnapshotCreate(StrictModel):
    source_id: Key
    source_revision: Key
    resource_id: Key
    space_id: Key
    path: str = Field(min_length=1, max_length=2048)
    kind: EvidenceKind
    text: str = Field(max_length=4_000_000)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    object_key: str = Field(min_length=1, max_length=2048)

    _relative_path = field_validator("path")(EvidenceRef.safe_relative_path.__func__)


class CompilerProof(StrictModel):
    compiler_version: Key
    schema_version: Key
    snapshot_ids: list[Key] = Field(min_length=1, max_length=1000)
    idempotency_key: Key


class DeterministicPublish(StrictModel):
    page_id: Key
    space_id: Key
    base_revision: Key | None
    content: PageContent
    proof: CompilerProof


class ValidityUpdate(StrictModel):
    base_revision: Key
    proof: EvidenceRef
    claim_ids: list[Key] = Field(min_length=1, max_length=1000)
    state: Literal["stale", "retracted"]
    reason: Annotated[str, Field(min_length=1, max_length=4096)]


class LeaseRequest(StrictModel):
    consumer: Literal["retrieval", "graphiti"]
    limit: int = Field(default=20, ge=1, le=100)
    lease_seconds: int = Field(default=30, ge=5, le=300)


class AckRequest(StrictModel):
    lease_token: Key


class EvidenceAuthorizeRequest(StrictModel):
    evidence: list[EvidenceRef] = Field(max_length=100)


class KnowledgeError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


class PageAuthorization(StrictModel):
    page_id: Key
    revision_id: Key


class PageAuthorizeRequest(StrictModel):
    pages: list[PageAuthorization] = Field(max_length=100)
    include_historical: bool = False
    as_of: datetime | None = None

    @field_validator("as_of")
    @classmethod
    def aware_as_of(cls, value):
        validate_interval(value, None)
        return value

class ProposalCreate(StrictModel):
    base_revision: Key | None
    content: PageContent
    kind: Literal["formal_change", "entity_merge", "schema_change", "digest"] = "formal_change"
    reason: Annotated[str, Field(min_length=1, max_length=4096)]


class ReviewAction(StrictModel):
    reason: Annotated[str, Field(min_length=1, max_length=4096)]


class RollbackRequest(StrictModel):
    base_revision: Key
    revision_id: Key
    reason: Annotated[str, Field(min_length=1, max_length=4096)]


class SourceSnapshotCreate(StrictModel):
    source_id: Key
    source_revision: Key
    resource_id: Key
    space_id: Key
    path: str = Field(min_length=1, max_length=2048)
    kind: EvidenceKind
    text: str = Field(max_length=4_000_000)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    object_key: str = Field(min_length=1, max_length=2048)

    _relative_path = field_validator("path")(EvidenceRef.safe_relative_path.__func__)


class CompilerProof(StrictModel):
    compiler_version: Key
    schema_version: Key
    snapshot_ids: list[Key] = Field(min_length=1, max_length=1000)
    idempotency_key: Key


class DeterministicPublish(StrictModel):
    page_id: Key
    space_id: Key
    base_revision: Key | None
    content: PageContent
    proof: CompilerProof


class ValidityUpdate(StrictModel):
    base_revision: Key
    proof: EvidenceRef
    claim_ids: list[Key] = Field(min_length=1, max_length=1000)
    state: Literal["stale", "retracted"]
    reason: Annotated[str, Field(min_length=1, max_length=4096)]


class LeaseRequest(StrictModel):
    consumer: Literal["retrieval", "graphiti"]
    limit: int = Field(default=20, ge=1, le=100)
    lease_seconds: int = Field(default=30, ge=5, le=300)


class AckRequest(StrictModel):
    lease_token: Key


class EvidenceAuthorizeRequest(StrictModel):
    evidence: list[EvidenceRef] = Field(max_length=100)


class KnowledgeError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


class PageAuthorization(StrictModel):
    page_id: Key
    revision_id: Key


class PageAuthorizeRequest(StrictModel):
    pages: list[PageAuthorization] = Field(max_length=100)
    include_historical: bool = False
    as_of: datetime | None = None

    @field_validator("as_of")
    @classmethod
    def aware_as_of(cls, value):
        validate_interval(value, None)
        return value
