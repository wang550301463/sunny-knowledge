"""Derived Wiki content keeps every consulted page's ACL in both projections."""

from copy import deepcopy
from importlib import import_module

import pytest

from .test_projection import projection


@pytest.fixture(params=["retrieval", "graphiti"])
def adapter(request):
    return import_module(f"knowledge_platform.{request.param}.projection")


def derived(adapter):
    value = projection()
    value["input_revisions"] = [
        {"page_id": "private:wiki", "revision_id": "wiki:1", "space_id": "private"},
        {"page_id": "private:wiki", "revision_id": "wiki:2", "space_id": "private"},
    ]
    value["policies"].append(
        dict(
            space_id="private",
            resource_id="private:wiki",
            space_read_subjects=["user:alice"],
            resource_read_subjects=None,
            acl_version=11,
            auth_epoch=9,
        )
    )
    value["read_clauses"] = adapter.clauses_for(
        [adapter.Policy.model_validate(p) for p in value["policies"]]
    )
    value["acl_domain"] = adapter.digest(
        {"space_id": value["space_id"], "read_clauses": value["read_clauses"]}
    )
    return value


def test_input_wiki_acl_is_required_without_becoming_original_evidence(adapter):
    value = derived(adapter)
    assert len(adapter.checked_policies(value)) == 3
    if hasattr(adapter, "compile_fragments"):
        fragments = adapter.compile_fragments(value, configuration_id="model-v1", dimensions=3)
        assert all(["user:alice"] in f.read_clauses for f in fragments)
        assert all(len(f.evidence) == 1 for f in fragments)
    else:
        graph = adapter.compile_graph(value)
        assert ["user:alice"] in graph.read_clauses
        assert graph.group_id != adapter.compile_graph(projection()).group_id
    missing = deepcopy(value)
    missing["policies"].pop()
    with pytest.raises(Exception, match="incomplete provenance or ACL"):
        adapter.checked_policies(missing)


@pytest.mark.parametrize(
    "inputs",
    [
        None,
        {},
        "private",
        [{}],
        [{"page_id": 7, "revision_id": "r", "space_id": "s"}],
        [{"page_id": "p", "revision_id": "", "space_id": "s"}],
        [{"page_id": "p", "revision_id": "r", "space_id": "s" * 513}],
        [{"page_id": "p", "revision_id": "r", "space_id": "s", "ignored": True}],
        [{"page_id": "p", "revision_id": "r", "space_id": "s"}] * 2,
        [
            {"page_id": "p", "revision_id": "r", "space_id": "s"},
            {"page_id": "p", "revision_id": "r2", "space_id": "other"},
        ],
        [
            {"page_id": "p", "revision_id": "r", "space_id": "s"},
            {"page_id": "other", "revision_id": "r", "space_id": "s"},
        ],
        [{"page_id": f"p:{i}", "revision_id": f"r:{i}", "space_id": "s"} for i in range(2001)],
    ],
)
def test_invalid_input_lineage_cannot_be_ignored(adapter, inputs):
    value = projection(input_revisions=inputs)
    with pytest.raises(Exception, match="incomplete provenance or ACL"):
        adapter.checked_policies(value)


def test_same_page_previous_revision_is_a_valid_input_constraint(adapter):
    value = projection()
    value["input_revisions"] = [
        {"page_id": value["page_id"], "revision_id": "previous", "space_id": value["space_id"]}
    ]
    assert len(adapter.checked_policies(value)) == 2
