"""Canonical Wiki-input ACL metadata shared by the rebuildable projections."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from .evidence import Key


class InputRevision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    page_id: Key
    revision_id: Key
    space_id: Key


_inputs = TypeAdapter(Annotated[list[InputRevision], Field(max_length=2000)])


def input_revision_resources(projection: dict) -> set[tuple[str, str]]:
    """Check the complete closure's identity shape, returning mandatory page policies.

    Canonical owns immutable revision existence, lineage traversal and live page
    ownership. Consumers additionally reject ambiguous identities or a truncated
    policy set. These constraints never count as new original-source evidence.
    """
    values = _inputs.validate_python(projection.get("input_revisions", []), strict=True)
    pairs, revisions = set(), {}
    pages = {projection["page_id"]: projection["space_id"]}
    for value in values:
        pair = (value.page_id, value.revision_id)
        identity = (value.space_id, value.page_id)
        if (
            pair in pairs
            or pages.get(value.page_id, value.space_id) != value.space_id
            or revisions.get(value.revision_id, identity) != identity
        ):
            raise ValueError("Ambiguous canonical Wiki lineage")
        pairs.add(pair)
        pages[value.page_id] = value.space_id
        revisions[value.revision_id] = identity
    return {(v.space_id, v.page_id) for v in values}
