"""Safety preflight for the test-only physical fault controller, without live mutations."""

import importlib.util
import time
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("web_graph_fault_control", Path(__file__).with_name("fault_control.py"))
control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control)


class FaultScopeTest(unittest.TestCase):
    def test_rejects_stale_or_other_namespace_and_incomplete_requests(self):
        now = time.time()
        namespace = "git-graph-regression-" + "a" * 32
        request = {"fixture_id": "b" * 32, "namespace": namespace, "created_at": now,
                   "space_id": "space", "page_id": "page", "revision_id": "revision", "edge_id": "edge"}
        self.assertTrue(control.valid_request(request, namespace, now))
        for changes in ({"namespace": "knowledge-v2"}, {"created_at": now - 181}, {"created_at": now + 1},
                        {"created_at": True}, {"fixture_id": "../other"}, {"edge_id": ""}, {"page_id": "x" * 513}):
            self.assertFalse(control.valid_request({**request, **changes}, namespace, now))
        self.assertFalse(control.valid_request(None, namespace, now))
        self.assertFalse(control.valid_request(request, "knowledge-v2", now))

    def test_gate_cannot_treat_scalar_or_invalid_json_as_operator_acknowledgement(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gate.json"
            for value in ('null', '[]', '"fixture"', '{invalid'):
                path.write_text(value)
                self.assertIsNone(control.read(path))
            path.write_text('{"fixture_id":"fixture"}')
            self.assertEqual(control.read(path), {"fixture_id": "fixture"})