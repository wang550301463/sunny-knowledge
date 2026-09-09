"""The load gate cannot pass by averaging away tail latency or missing requests."""

import unittest

from measurement import MeasurementError, summarize


def snapshot(count, under_two, total=1.0):
    return {"count": count, "sum": total, "buckets": {"1.0": 0, "2.0": under_two, "5.0": count, "+Inf": count}}


class MeasurementTests(unittest.TestCase):
    def measure(self, before=None, after=None, **changes):
        values = {"successful_requests": 100, "failed_requests": 0,
                  "peak_concurrency": 10, "fragment_count": 100000,
                  "client_seconds": [0.25] * 95 + [3.0] * 5}
        values.update(changes)
        return summarize(before or snapshot(8, 8), after or snapshot(108, 103, 200), **values)

    def test_uses_bucket_deltas_instead_of_lifetime_latency_or_mean(self):
        report = self.measure()
        self.assertTrue(report["local_latency_passed"])
        self.assertEqual(report["local_p95_upper_bound_seconds"], 2)
        self.assertEqual(report["client_p95_seconds"], 0.25)
        self.assertEqual(report["local_over_two_seconds"], 5)

    def test_six_percent_slow_requests_fail_even_with_fast_mean(self):
        report = self.measure(after=snapshot(108, 102, 2))
        self.assertFalse(report["local_latency_passed"])
        self.assertEqual(report["local_p95_upper_bound_seconds"], 5)

    def test_any_failed_request_prevents_overall_acceptance(self):
        report = self.measure(failed_requests=1)
        self.assertTrue(report["local_latency_passed"])
        self.assertFalse(report["accepted"])

    def test_insufficient_scale_or_concurrency_is_not_an_acceptance(self):
        for change in ({"fragment_count": 99999}, {"peak_concurrency": 9}):
            self.assertFalse(self.measure(**change)["accepted"])

    def test_lost_or_unrelated_successes_invalidate_the_measurement(self):
        for count in (107, 109):
            with self.assertRaises(MeasurementError):
                self.measure(after=snapshot(count, 100))

    def test_restart_nonmonotonic_buckets_and_missing_exact_threshold_fail(self):
        bad = [snapshot(2, 1), {"count": 108, "sum": 200, "buckets": {"1.0": 20, "2.0": 19, "5.0": 108, "+Inf": 108}},
               {"count": 108, "sum": 200, "buckets": {"1.0": 20, "5.0": 108, "+Inf": 108}}]
        for after in bad:
            with self.assertRaises(MeasurementError):
                self.measure(after=after)

    def test_partial_client_results_cannot_claim_a_percentile(self):
        with self.assertRaises(MeasurementError):
            self.measure(client_seconds=[0.1] * 99)


if __name__ == "__main__":
    unittest.main()
