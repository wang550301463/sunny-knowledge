"""Strict HTTP contracts for immutable canonical knowledge and source evidence."""
from __future__ import annotations

from datetime import datetime
from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Key = Annotated[str, Field(min_length=1, max_length=512)]
EntityType = Literal['Service', 'Module', 'File', 'Person', 'Dependency', 'Decision', 'Policy', 'Incident', 'Change', 'Procedure']
EvidenceKind = Literal['code', 'markdown', 'ticket', 'policy']


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


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

    @field_validator('path')
    @classmethod
    def safe_relative_path(cls, value: str) -> str:
        if '\\' in value or '\x00' in value or value.startswith('/') or '..' in PurePosixPath(value).parts:
            raise ValueError('path must be a relative source path without traversal')
        return value

    @model_validator(mode='after')
    def fixed_range(self):
        if self.end_line < self.start_line:
            raise ValueError('end_line must be >= start_line')
        validate_interval(self.valid_from, self.valid_until)
        return self


def validate_interval(start: datetime | None, end: datetime | None) -> None:
    for value in (start, end):
        if value is not None and value.utcoffset() is None:
            raise ValueError('timestamps must include a timezone')
    if start is not None and end is not None and end <= start:
        raise ValueError('valid_until must follow valid_from')


class Claim(StrictModel):
    id: Key
    text: Annotated[str, Field(min_length=1, max_length=100_000)]
    kind: Literal['fact', 'inference', 'gap'] = 'fact'
    entity_type: EntityType | None = None
    evidence: list[EvidenceRef] = Field(default_factory=list, max_length=100)
    state: Literal['valid', 'stale', 'retracted'] = 'valid'
    valid_from: datetime | None = None
    valid_until: datetime | None = None

    @model_validator(mode='after')
    def supported(self):
        if self.kind == 'fact' and not self.evidence:
            raise ValueError('formal facts require original source evidence')
        validate_interval(self.valid_from, self.valid_until)
        return self


class PageContent(StrictModel):
    title: Annotated[str, Field(min_length=1, max_length=512)]
    markdown: Annotated[str, Field(max_length=4_000_000)]
    entity_type: EntityType | None = None
    claims: list[Claim] = Field(default_factory=list, max_length=1000)
    evidence: list[EvidenceRef] = Field(default_factory=list, max_length=1000)
    state: Literal['valid', 'stale', 'retracted'] = 'valid'
    valid_from: datetime | None = None
    valid_until: datetime | None = None

    @model_validator(mode='after')
    def unique_claims(self):
        if len({claim.id for claim in self.claims}) != len(self.claims):
            raise ValueError('claim IDs must be unique')
        validate_interval(self.valid_from, self.valid_until)
        return self

    def supports(self) -> list[EvidenceRef]:
        refs = self.evidence + [ref for claim in self.claims for ref in claim.evidence]
        return list({ref.model_dump_json(): ref for ref in refs}.values())


class PageCreate(StrictModel):
    id: Key | None = None
    space_id: Key
    content: PageContent
    reason: Annotated[str, Field(min_length=1, max_length=4096)] = 'Initial page proposal'


class ProposalCreate(StrictModel):
    base_revision: Key | None
    content: PageContent
    kind: Literal['formal_change', 'entity_merge', 'schema_change', 'digest'] = 'formal_change'
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
    sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    object_key: str = Field(min_length=1, max_length=2048)

    _relative_path = field_validator('path')(EvidenceRef.safe_relative_path.__func__)


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
    state: Literal['stale', 'retracted']
    reason: Annotated[str, Field(min_length=1, max_length=4096)]


class LeaseRequest(StrictModel):
    consumer: Literal['retrieval', 'graphiti']
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
