"""Hard system-knowledge cutoffs, distinct from source business-valid timestamps."""
from datetime import datetime

from .schemas import fail


def instant(value):
    try:
        if isinstance(value, str):
            value = datetime.fromisoformat(value)
        if not isinstance(value, datetime) or value.utcoffset() is None:
            raise ValueError
        return value
    except (TypeError, ValueError, OverflowError):
        raise fail("knowledge_time_unavailable", "Authoritative knowledge observation time unavailable") from None


def within_cutoff(value, cutoff):
    if cutoff is None:
        return True
    return instant(value) <= instant(cutoff)


def require_revision_time(revision, cutoff):
    if cutoff is not None and not within_cutoff(revision.get("created_at"), cutoff):
        raise fail("knowledge_after_cutoff", "Knowledge revision was learned after the requested cutoff", 409)