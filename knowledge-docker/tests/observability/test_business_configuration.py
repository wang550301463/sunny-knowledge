"""Static deployment guards; no secrets, application startup, or runtime mutation."""

import json
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKERS = ('ingest-worker', 'retrieval-worker', 'graphiti-worker')


class BusinessMetricsConfiguration(unittest.TestCase):
    def test_worker_listeners_are_only_in_backend_network_without_host_ports(self):
        base = yaml.safe_load((ROOT / 'compose.yaml').read_text())['services']
        overlay = yaml.safe_load((ROOT / 'compose.observability.yaml').read_text())['services']
        for worker in WORKERS:
            self.assertEqual(base[worker]['networks'], ['backend'])
            self.assertNotIn('ports', base[worker])
            self.assertNotIn('ports', overlay[worker])
            self.assertNotIn('network_mode', overlay[worker])
            self.assertEqual(overlay[worker]['environment']['METRICS_PORT'], '9090')

    def test_prometheus_explicitly_scrapes_every_independent_worker(self):
        config = yaml.safe_load((ROOT / 'observability/prometheus.yaml').read_text())
        job = next(job for job in config['scrape_configs'] if job['job_name'] == 'knowledge-services')
        targets = [target for group in job['static_configs'] for target in group['targets']]
        for worker in WORKERS:
            self.assertEqual(targets.count(worker + ':9090'), 1)

    def test_dashboard_does_not_convert_unknown_backlog_to_zero_or_sum_replicas(self):
        config = json.loads((ROOT / 'observability/grafana/knowledge.json').read_text())
        queries = [target['expr'] for panel in config['panels'] for target in panel['targets']]
        self.assertTrue(any('knowledge_queue_observed_age_seconds' in query for query in queries))
        self.assertTrue(any('knowledge_queue_known' in query for query in queries))
        self.assertTrue(any('operation="search_local"' in query for query in queries))
        for query in queries:
            self.assertNotIn('or vector(0)', query)
            if 'knowledge_queue_size' in query:
                self.assertIn('max by', query)
                self.assertNotIn('sum', query)


if __name__ == '__main__':
    unittest.main()