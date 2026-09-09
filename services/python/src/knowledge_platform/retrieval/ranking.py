"""RRF ranks evidence fragments. Its scores never represent factual confidence."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class RankedFragment:
    id: str
    score: float
    contributions: dict[str, float]


def fuse(
    rankings: Mapping[str, Sequence[str]],
    *,
    weights: Mapping[str, float] | None = None,
    k: int = 60,
    limit: int = 60,
) -> list[RankedFragment]:
    """Fuse original BM25/vector ranks again after graph expansion, with stable ties.

    A fragment contributes at most once per retriever. In particular, callers must
    not replace the original BM25/vector lists with the already fused seed list:
    doing so would discard the agreed rank contributions in the second phase.
    Permission, validity and temporal eligibility are separate hard constraints.
    """
    if isinstance(k, bool) or not isinstance(k, int) or k < 1:
        raise ValueError("RRF k must be a positive integer")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("RRF output limit must be a positive integer")
    effective_weights = {"bm25": 1.0, "vector": 1.0, "graph": 0.5, **(weights or {})}
    if any(not math.isfinite(weight) or weight <= 0 for weight in effective_weights.values()):
        raise ValueError("RRF weights must be positive and finite")
    contributions: dict[str, dict[str, float]] = {}
    for source, fragments in rankings.items():
        seen = set()
        for fragment_id in fragments:
            if not isinstance(fragment_id, str) or not fragment_id:
                raise ValueError("Fragment IDs must be nonempty strings")
            if fragment_id in seen:
                continue
            seen.add(fragment_id)
            contribution = effective_weights.get(source, 1.0) / (k + len(seen))
            contributions.setdefault(fragment_id, {})[source] = contribution
    result = [
        RankedFragment(fragment_id, math.fsum(parts.values()), parts)
        for fragment_id, parts in contributions.items()
    ]
    return sorted(result, key=lambda fragment: (-fragment.score, fragment.id))[:limit]
