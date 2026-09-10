"""Deterministic Wiki fragments with complete provenance ACL and time intersection."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime

from pydantic import ValidationError

from .schemas import EvidenceRef, Fragment, Policy, unavailable


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def clauses_for(policies):
    clauses = []
    for policy in policies:
        clauses.append(tuple(sorted(set(policy.space_read_subjects))))
        if policy.resource_read_subjects is not None:
            clauses.append(tuple(sorted(set(policy.resource_read_subjects))))
    return [list(c) for c in sorted(set(clauses))]


def policy_fingerprint(policies):
    values = [
        Policy.model_validate(p).model_dump() if isinstance(p, dict) else p.model_dump()
        for p in policies
    ]
    return digest(
        sorted(
            [{k: v for k, v in p.items() if k not in {"auth_epoch", "acl_domain"}} for p in values],
            key=lambda p: (p["space_id"], p["resource_id"]),
        )
    )


def checked_policies(projection):
    try:
        policies = [Policy.model_validate(value) for value in projection["policies"]]
        expected = {(projection["space_id"], projection["page_id"])} | {
            (s["space_id"], s["resource_id"]) for s in projection["source_snapshots"]
        }
        if (
            len({p.auth_epoch for p in policies}) != 1
            or {(p.space_id, p.resource_id) for p in policies} != expected
            or len(policies) != len(expected)
        ):
            raise ValueError
        clauses = clauses_for(policies)
        if projection["read_clauses"] != clauses or projection["acl_domain"] != digest(
            {"space_id": projection["space_id"], "read_clauses": clauses}
        ):
            raise ValueError
        return policies
    except (KeyError, TypeError, ValueError, ValidationError):
        raise unavailable(
            "invalid_projection", "Canonical projection has incomplete provenance or ACL"
        ) from None


def _instant(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        result = value
    else:
        result = datetime.fromisoformat(value)
    if result.utcoffset() is None:
        raise ValueError("Naive validity time")
    return result


def compile_fragments(projection, *, configuration_id, dimensions, chunk_chars=2000):
    policies = checked_policies(projection)
    try:
        content = projection["content"]
        claims = content["claims"]
        refs = [
            EvidenceRef.model_validate(ref)
            for ref in content["evidence"] + [r for c in claims for r in c["evidence"]]
        ]
        refs = list({ref.model_dump_json(): ref for ref in refs}.values())
        snapshots = {s["id"]: s for s in projection["source_snapshots"]}
        for ref in refs:
            source = snapshots[ref.revision_id]
            if any(
                getattr(ref, key) != source[key]
                for key in ("source_id", "source_revision", "resource_id", "path", "kind")
            ):
                raise ValueError
        temporal = [content, *claims, *[r.model_dump(mode="json") for r in refs]]
        starts = [_instant(v["valid_from"]) for v in temporal if v.get("valid_from")]
        ends = [_instant(v["valid_until"]) for v in temporal if v.get("valid_until")]
        start, end = max(starts, default=None), min(ends, default=None)
        states = {content["state"], *[c["state"] for c in claims]}
        state = "retracted" if "retracted" in states else "stale" if "stale" in states else "valid"
        if start and end and start >= end:
            state = "stale"
        if not 256 <= chunk_chars <= 16000:
            raise ValueError("Invalid chunk size")
        base = {
            "page_id": projection["page_id"],
            "revision_id": projection["revision_id"],
            "version": projection["version"],
            "space_id": projection["space_id"],
            "title": content["title"],
            "read_clauses": projection["read_clauses"],
            "acl_domain": projection["acl_domain"],
            "acl_epoch": policies[0].auth_epoch,
            "acl_version": max(p.acl_version for p in policies),
            "state": state,
            "valid_from": start,
            "valid_until": end,
            "known_at": projection["created_at"],
            "is_current": projection["is_current"],
            "embedding_configuration_id": configuration_id,
            "embedding_dimensions": dimensions,
        }
        fragments = []
        for ordinal, offset in enumerate(range(0, len(content["markdown"]), chunk_chars)):
            fragments.append(
                Fragment(
                    **base,
                    id=digest(
                        [projection["page_id"], projection["revision_id"], "markdown", ordinal]
                    ),
                    text=content["markdown"][offset : offset + chunk_chars],
                    kind="narrative",
                    entity_ids=[projection["page_id"]],
                    evidence=refs,
                )
            )
        for claim in claims:
            # Long claims are chunked without adding independent evidence. Identity remains stable.
            for ordinal, offset in enumerate(range(0, len(claim["text"]), chunk_chars)):
                fragments.append(
                    Fragment(
                        **base,
                        id=digest(
                            [
                                projection["page_id"],
                                projection["revision_id"],
                                "claim",
                                claim["id"],
                                ordinal,
                            ]
                        ),
                        text=claim["text"][offset : offset + chunk_chars],
                        kind=claim["kind"],
                        entity_ids=sorted({projection["page_id"], claim["id"]}),
                        evidence=claim["evidence"],
                    )
                )
        return fragments
    except (KeyError, TypeError, ValueError, ValidationError):
        raise unavailable(
            "invalid_projection", "Canonical fragment projection is invalid"
        ) from None
"""Strict retrieval boundary DTOs. Scores are rankings, never confidence."""

