"""Conservative local-latency acceptance from an exclusive live histogram window.

The exact 2-second bucket proves the requested threshold for the observed sample;
it does not interpolate a precise percentile or subtract model averages from p95.
"""

import math


class MeasurementError(ValueError):
    pass


def _number(value, *, integer=False):
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise MeasurementError("Invalid numeric observation")
    if not math.isfinite(value) or value < 0 or (integer and int(value) != value):
        raise MeasurementError("Invalid numeric observation")
    return value


def _histogram(snapshot):
    try:
        count = _number(snapshot["count"], integer=True)
        total = _number(snapshot["sum"])
        buckets = {}
        for label, observation in snapshot["buckets"].items():
            bound = float(label)
            if math.isnan(bound) or bound <= 0 or bound in buckets:
                raise MeasurementError("Invalid histogram boundary")
            buckets[bound] = _number(observation, integer=True)
        ordered = sorted(buckets)
        if 2.0 not in buckets or math.inf not in buckets or buckets[math.inf] != count:
            raise MeasurementError("Missing threshold or inconsistent histogram count")
        if any(buckets[a] > buckets[b] for a, b in zip(ordered, ordered[1:])):
            raise MeasurementError("Nonmonotonic histogram")
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        if isinstance(error, MeasurementError):
            raise
        raise MeasurementError("Malformed histogram snapshot") from None
    return count, total, buckets


def summarize(before, after, *, successful_requests, failed_requests,
              peak_concurrency, fragment_count, client_seconds):
    successful = _number(successful_requests, integer=True)
    failed = _number(failed_requests, integer=True)
    concurrency = _number(peak_concurrency, integer=True)
    fragments = _number(fragment_count, integer=True)
    if successful == 0 or len(client_seconds) != successful:
        raise MeasurementError("Incomplete successful-client observation set")
    durations = sorted(_number(item) for item in client_seconds)
    old_count, old_sum, old_buckets = _histogram(before)
    count, total, buckets = _histogram(after)
    if old_buckets.keys() != buckets.keys() or total < old_sum:
        raise MeasurementError("Histogram reset or configuration change")
    if count - old_count != successful:
        raise MeasurementError("Unrelated traffic, lost responses or histogram reset")
    delta = {bound: buckets[bound] - old_buckets[bound] for bound in buckets}
    ordered = sorted(delta)
    if any(item < 0 for item in delta.values()) or any(
        delta[a] > delta[b] for a, b in zip(ordered, ordered[1:])
    ):
        raise MeasurementError("Histogram reset or inconsistent observation window")
    rank = math.ceil(successful * 0.95)
    upper_bound = next(bound for bound in ordered if delta[bound] >= rank)
    latency_passed = delta[2.0] >= rank
    scale_passed = fragments >= 100000 and concurrency == 10
    return {
        "accepted": bool(latency_passed and scale_passed and failed == 0),
        "local_latency_passed": latency_passed,
        "scale_passed": scale_passed,
        "successful_requests": int(successful),
        "failed_requests": int(failed),
        "peak_concurrency": int(concurrency),
        "fragment_count": int(fragments),
        "local_at_most_two_seconds": int(delta[2.0]),
        "local_over_two_seconds": int(successful - delta[2.0]),
        "local_p95_upper_bound_seconds": upper_bound if math.isfinite(upper_bound) else None,
        "local_mean_seconds": (total - old_sum) / successful,
        "client_p95_seconds": durations[rank - 1],
        "percentile_method": "nearest_rank_successful_requests",
        "local_method": "exclusive_histogram_delta_exact_2s_bucket",
        "limitations": [
            "Observed synthetic workload only; no real model quality result",
            "Client percentile covers successes; any request failure rejects acceptance",
            "A matching count cannot detect equal numbers of unrelated and lost responses; the operator must enforce exclusive query admission",
            "External model durations must be reported separately without subtracting averages from percentiles",
        ],
    }
