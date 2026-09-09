import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


class SeedTests(unittest.TestCase):
    def seed(self):
        path = Path(__file__).with_name("seed.py")
        self.assertTrue(path.exists(), "Isolated performance seeder is missing")
        spec = importlib.util.spec_from_file_location("performance_seed", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_compose_requires_owned_network_volume_bind_and_fixed_images(self):
        seed = self.seed()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = "sunny-perf-12345678"
            base = {"name": project, "services": {"api": {"image": "sha256:" + "a" * 64, "networks": {"backend": None}, "volumes": [{"type": "bind", "source": str(root / "config"), "target": "/config"}]}},
                    "networks": {"backend": {"name": project + "_backend"}}, "volumes": {"db": {"name": project + "_db"}}}
            self.assertEqual(seed.check_compose(root, project, base), {"api": "sha256:" + "a" * 64})
            for kind in ("project", "network", "volume", "bind", "image", "build", "host"):
                value = json.loads(json.dumps(base))
                if kind == "project": value["name"] = "sunny-knowledge-v2"
                if kind == "network": value["networks"]["backend"]["name"] = "main_backend"
                if kind == "volume": value["volumes"]["db"]["external"] = True
                if kind == "bind": value["services"]["api"]["volumes"][0]["source"] = "/var/run/docker.sock"
                if kind == "image": value["services"]["api"]["image"] = "latest"
                if kind == "build": value["services"]["api"]["build"] = {"context": "."}
                if kind == "host": value["services"]["api"]["network_mode"] = "host"
                with self.assertRaises(ValueError): seed.check_compose(root, project, value)

    def test_checked_root_refuses_main_or_changed_origin_before_network(self):
        seed = self.seed()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".local").mkdir()
            (root / ".env").write_text("COMPOSE_PROJECT_NAME=sunny-knowledge-v2\nPUBLIC_WEB_URL=http://localhost:8080\n")
            (root / ".local/test-env.json").write_text(json.dumps({"public_url": "http://localhost:8080"}))
            (root / ".local/performance.json").write_text(json.dumps({"project": "sunny-knowledge-v2", "public_url": "http://localhost:8080"}))
            with self.assertRaises(ValueError): seed.checked_root(root)

    def test_index_validation_requires_every_current_revision_and_exact_total(self):
        seed = self.seed()
        record = {"pages": {"p1": "r1", "p2": "r2"}}
        value = {"count": 100001, "filtered_count": 100001,
                 "pages": {"p1": {"r1": 50000}, "p2": {"r2": 50001}}}
        self.assertEqual(seed.validate_index(value, record), 100001)
        for kind in ("foreign", "missing", "old_revision", "incomplete"):
            wrong = json.loads(json.dumps(value))
            if kind == "foreign": wrong["count"] += 1
            if kind == "missing": del wrong["pages"]["p2"]
            if kind == "old_revision": wrong["pages"]["p2"] = {"old": 50001}
            if kind == "incomplete": wrong["pages"]["p2"]["r2"] -= 1
            with self.assertRaises(ValueError): seed.validate_index(wrong, record)


if __name__ == "__main__":
    unittest.main()
