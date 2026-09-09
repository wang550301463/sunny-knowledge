"""Shared evidence reference contract (reconstructed 2026-09-09).

The original module was lost with the disk wipe; this reconstruction mirrors
knowledge_platform.knowledge.schemas.EvidenceRef — the wire contract used by
MCP tool results and citation payloads. Field semantics and validators are
kept identical so model_validate round-trips between services.
"""
from datetime import datetime
from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Key = Annotated[str, Field(min_length=1, max_length=512)]
EvidenceKind = Literal["code", "markdown", "ticket", "policy", "source_manifest"]


class EvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

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
        if "\\" in value or "\x00" in value or value.startswith("/") or ".." in PurePosixPath(value).parts:
            raise ValueError("path must be a relative source path without traversal")
        return value

    @model_validator(mode="after")
    def fixed_range(self):
        if self.end_line < self.start_line:
            raise ValueError("end_line must not precede start_line")
        if self.valid_from is not None and self.valid_until is not None and self.valid_until < self.valid_from:
            raise ValueError("valid_until must not precede valid_from")
        return self
