"""Lifecycle dimensions are deterministic metadata, never factual confidence."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from knowledge_platform.knowledge.schemas import PageContent

BASE = datetime(2020, 1, 1, tzinfo=UTC)


def reference(identity="s1", source="repo", **overrides):
    return {
        "revision_id": identity,
        "resource_id": "resource",
        "source_id": source,
        "source_revision": "commit-1",
        "path": "main.go",
        "kind": "code",
        "start_line": 1,
        "end_line": 1,
        **overrides,
    }


def metrics(content, snapshots=None, instant=None, current=True):
    from knowledge_platform.knowledge.lifecycle import calculate

    return calculate(
        PageContent.model_validate({"title": "Page", "markdown": "Source-backed", **content}),
        snapshots or {"s1": SimpleNamespace(created_at=BASE)},
        instant or BASE + timedelta(days=90),
        is_current=current,
    )


def test_same_source_across_versions_spaces_fragments_counts_once():
    refs = [reference(), reference("s2", source_revision="commit-2"), reference("s3", "other")]
    rows = {
        "s1": SimpleNamespace(created_at=BASE, space_id="a"),
        "s2": SimpleNamespace(created_at=BASE + timedelta(days=30), space_id="b"),
        "s3": SimpleNamespace(created_at=BASE + timedelta(days=60), space_id="b"),
    }
    result = metrics({"evidence": refs + [refs[0]]}, rows)
    assert result["support"]["registered_source_count"] == 2
    assert result["freshness"]["observed_at"] == BASE.isoformat()
    assert result["freshness"]["score"] == pytest.approx(0.5)
    assert "confidence" not in str(result)


@pytest.mark.parametrize(
    ("entity_type", "days"),
    [("Policy", 180), ("Decision", 180), ("Procedure", 180), ("Service", 90),
     ("Module", 90), ("Person", 90), ("Dependency", 90), ("File", 21),
     ("Incident", 21), ("Change", 21), (None, 90)],
)
def test_class_half_lives_apply_only_to_freshness(entity_type, days):
    result = metrics({"entity_type": entity_type, "evidence": [reference()]},
                     instant=BASE + timedelta(days=days))
    assert result["freshness"]["half_life_days"] == days
    assert result["freshness"]["score"] == pytest.approx(0.5)
    assert result["validity"]["eligible"] is True


def test_manifests_and_unreferenced_lineage_are_not_independent_support():
    manifest = reference(kind="source_manifest", path=".__knowledge__/manifest.json")
    result = metrics({"evidence": [manifest]})
    assert result["support"]["registered_source_count"] == 0
    assert result["freshness"]["observed_at"] is None
    assert result["freshness"]["score"] is None


def test_absolute_time_does_not_multiply_previous_decay_or_refresh_on_edit():
    page = {"evidence": [reference()]}
    first = metrics(page)
    same = metrics({**page, "markdown": "A new summary of the same evidence"})
    later = metrics(page, instant=BASE + timedelta(days=180))
    assert first == same
    assert first["freshness"]["score"] == pytest.approx(0.5)
    assert later["freshness"]["score"] == pytest.approx(0.25)


def test_validity_and_eligible_support_are_hard_constraints_separate_from_age():
    ref = reference(valid_until=(BASE + timedelta(days=1)).isoformat())
    result = metrics({"claims": [{"id": "c", "text": "fact", "evidence": [ref]}]})
    assert result["support"]["registered_source_count"] == 1
    assert result["support"]["eligible_registered_source_count"] == 0
    assert result["support"]["fact_claims_with_original_evidence"] == 1
    assert result["validity"]["eligible"] is False
    assert result["freshness"]["score"] == pytest.approx(0.5)
    assert metrics({"evidence": [reference()]}, current=False)["validity"]["eligible"] is False


def test_missing_or_future_original_observation_cannot_be_fabricated_as_fresh():
    with pytest.raises(Exception) as error:
        metrics({"evidence": [reference("missing")]})
    assert getattr(error.value, "code", None) == "lifecycle_source_unavailable"
    result = metrics({"evidence": [reference()]}, instant=BASE - timedelta(seconds=1))
    assert result["freshness"]["score"] is None

