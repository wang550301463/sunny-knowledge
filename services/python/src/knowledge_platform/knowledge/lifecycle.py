"""Independent lifecycle dimensions. No value here is factual confidence."""

from datetime import UTC, datetime
from types import MappingProxyType

from pydantic import Field, field_validator

from .schemas import Key, KnowledgeError, PageContent, StrictModel

POLICY_VERSION = "freshness-v1"
HALF_LIFE_DAYS = MappingProxyType(
    {
        "Policy": 180, "Decision": 180, "Procedure": 180,
        "Service": 90, "Module": 90, "Person": 90, "Dependency": 90,
        "File": 21, "Incident": 21, "Change": 21,
    }
)
ORIGINAL_KINDS = frozenset({"code", "markdown", "ticket", "policy"})


def policy_payload():
    return {
        "version": POLICY_VERSION,
        "half_life_days": dict(HALF_LIFE_DAYS),
        "default_half_life_days": 90,
        "baseline": "earliest_explicit_original_snapshot_registration",
        "formula": "2**(-max(0,as_of-observed_at)/half_life)",
        "support": "distinct_registered_source_ids",
    }


def utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("Timezone-aware timestamp required")
    return value.astimezone(UTC)


def in_interval(record, instant):
    return (record.valid_from is None or record.valid_from <= instant) and (
        record.valid_until is None or instant < record.valid_until
    )


def calculate(content: PageContent, snapshots: dict, as_of: datetime, *, is_current: bool):
    instant = utc(as_of)
    refs = content.supports()
    original = [ref for ref in refs if ref.kind in ORIGINAL_KINDS]
    observed = {}
    for ref in original:
        try:
            observed[ref.revision_id] = utc(snapshots[ref.revision_id].created_at)
        except (KeyError, AttributeError, TypeError, ValueError):
            raise KnowledgeError(
                503, "lifecycle_source_unavailable", "Original observation time is unavailable"
            ) from None
    facts = [claim for claim in content.claims if claim.kind == "fact"]
    temporal = all(in_interval(row, instant) for row in [content, *content.claims, *refs])
    valid_claims = all(claim.state == "valid" for claim in content.claims)
    half_life = HALF_LIFE_DAYS.get(content.entity_type, 90)
    baseline = min(observed.values()) if observed else None
    known = bool(observed) and all(value <= instant for value in observed.values())
    age = (instant - baseline).total_seconds() if baseline is not None and known else None
    return {
        "support": {
            "registered_source_count": len({ref.source_id for ref in original}),
            "eligible_registered_source_count": len({
                ref.source_id for ref in original
                if in_interval(ref, instant) and observed[ref.revision_id] <= instant
            }),
            "fact_claim_count": len(facts),
            "fact_claims_with_original_evidence": sum(
                any(ref.kind in ORIGINAL_KINDS for ref in claim.evidence) for claim in facts
            ),
        },
        "validity": {
            "state": content.state,
            "is_current": is_current,
            "within_valid_time": temporal,
            "claims_valid": valid_claims,
            "eligible": is_current and content.state == "valid" and valid_claims and temporal,
        },
        "freshness": {
            "policy_version": POLICY_VERSION,
            "observed_at": baseline.isoformat() if baseline is not None else None,
            "half_life_days": half_life,
            "age_seconds": age,
            "score": 2 ** (-age / (half_life * 86400)) if age is not None else None,
        },
    }


class AccessEventCreate(StrictModel):
    revision_id: Key
    idempotency_key: Key


class LifecycleReconcile(StrictModel):
    space_id: Key
    scheduled_at: datetime
    cursor: Key | None = None
    limit: int = Field(default=25, ge=1, le=25, strict=True)
    idempotency_key: Key

    @field_validator("scheduled_at")
    @classmethod
    def day_boundary(cls, value):
        value = utc(value)
        if value != value.replace(hour=0, minute=0, second=0, microsecond=0):
            raise ValueError("Daily snapshots require UTC midnight")
        return value
