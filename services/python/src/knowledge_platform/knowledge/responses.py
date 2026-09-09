"""Canonical output DTOs derived from the columns actually emitted by serialize().

These are response declarations, not publication validators. exclude_unset preserves
legacy missing defaults; extra=allow preserves extensions rather than filtering them.
"""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, JsonValue, PlainSerializer, create_model
from sqlalchemy import JSON

from . import models
from .schemas import PageContent, RevisionInput

Timestamp = Annotated[datetime, PlainSerializer(lambda value: value.isoformat(), return_type=str)]


class WireResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    __pydantic_extra__: dict[str, JsonValue]


def serialized_columns(name, row_type, overrides=None):
    """Unknown new JSON columns require an explicit source-owned content declaration."""
    overrides = overrides or {}
    fields = {}
    for column in row_type.__table__.columns:
        if column.name in overrides:
            annotation = overrides[column.name]
        elif isinstance(column.type, JSON):
            raise TypeError(f"Declare the wire type of {row_type.__name__}.{column.name}")
        else:
            annotation = column.type.python_type
            if annotation is datetime:
                annotation = Timestamp
        if column.nullable:
            annotation = annotation | None
        fields[column.name] = (annotation, ...)
    return create_model(name, __base__=WireResponse, __module__=__name__, **fields)


PageRecord = serialized_columns("PageRecord", models.Page)
KnowledgeRevision = serialized_columns(
    "KnowledgeRevision", models.Revision,
    {"content": PageContent, "access_dependencies": list[str], "input_revisions": list[RevisionInput],
     "proof": dict[str, JsonValue]},
)
RevisionProposal = serialized_columns(
    "RevisionProposal", models.Proposal,
    {"content": PageContent, "access_dependencies": list[str], "input_revisions": list[RevisionInput]},
)
SourceSnapshotView = serialized_columns("SourceSnapshotView", models.SourceSnapshot)


class WikiPageView(PageRecord):
    revision: KnowledgeRevision | None


class PageCreation(WireResponse):
    page: PageRecord
    proposal: RevisionProposal


class PageList(WireResponse):
    items: list[WikiPageView]
    next_cursor: str | None = None


class RevisionList(WireResponse):
    items: list[KnowledgeRevision]
    next_cursor: str | None = None


class ProposalList(WireResponse):
    items: list[RevisionProposal]
    next_cursor: str | None = None
