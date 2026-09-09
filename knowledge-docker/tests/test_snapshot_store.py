"""Immutable snapshot backup boundaries; optional test uses the real Compose S3."""

import importlib.util
import io
import json
import hashlib
import tempfile
import unittest
from pathlib import Path

from botocore.exceptions import ClientError

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/snapshot_store.py"
spec = importlib.util.spec_from_file_location("snapshot_store", SCRIPT)
store = importlib.util.module_from_spec(spec)
spec.loader.exec_module(store)


def object_key(data):
    digest = hashlib.sha256(data).hexdigest()
    return "objects/sha256/" + digest[:2] + "/" + digest


class MemoryS3:
    def __init__(self, objects=None):
        self.objects = dict(objects or {})
        self.puts = []
        self.list_calls = 0
        self.change_on_second_list = None
        self.fail_get = False

    def list_objects_v2(self, **kwargs):
        self.list_calls += 1
        if self.list_calls == 2 and self.change_on_second_list:
            key, data = self.change_on_second_list
            self.objects[key] = data
        return {"IsTruncated": False, "Contents": [
            {"Key": key, "Size": len(value), "ETag": hashlib.md5(value).hexdigest()}
            for key, value in sorted(self.objects.items())
        ]}

    def get_object(self, **kwargs):
        if self.fail_get:
            raise ClientError({"Error": {"Code": "AccessDenied", "Message": "PRIVATE_CONTENT"}}, "GetObject")
        value = self.objects[kwargs["Key"]]
        return {"Body": io.BytesIO(value), "ContentLength": len(value)}

    def put_object(self, **kwargs):
        self.puts.append(kwargs["Key"])
        assert kwargs["IfNoneMatch"] == "*"
        if kwargs["Key"] in self.objects:
            raise ClientError({"ResponseMetadata": {"HTTPStatusCode": 412}, "Error": {"Code": "PreconditionFailed"}}, "PutObject")
        body = kwargs["Body"]
        self.objects[kwargs["Key"]] = body.read() if hasattr(body, "read") else body
        return {}


class SnapshotStoreTests(unittest.TestCase):
    def fixture(self, path):
        data = b"# immutable\r\n\xe4\xb8\xad\xe6\x96\x87\n"
        source = MemoryS3({object_key(data): data})
        result = store.backup(source, "raw", path)
        return source, result

    def test_exact_bytes_manifest_and_idempotent_conditional_restore(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "backup"
            source, result = self.fixture(path)
            self.assertEqual(result["object_count"], 1)
            self.assertEqual(path.stat().st_mode & 0o777, 0o700)
            self.assertEqual((path / "manifest.json").stat().st_mode & 0o777, 0o600)
            target = MemoryS3()
            restored = store.restore(target, "fresh", path)
            self.assertEqual(restored["restored"], 1)
            self.assertEqual(target.objects, source.objects)
            self.assertEqual(store.restore(target, "fresh", path)["restored"], 0)
            self.assertEqual(len(target.puts), 1)

    def test_corrupt_local_object_is_detected_before_any_target_write(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "backup"
            self.fixture(path)
            next((path / "objects").iterdir()).write_bytes(b"tampered")
            target = MemoryS3()
            with self.assertRaisesRegex(store.SnapshotError, "backup_integrity"):
                store.restore(target, "fresh", path)
            self.assertEqual(target.puts, [])

    def test_unrelated_target_objects_and_existing_corruption_are_not_overwritten(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "backup"
            source, _ = self.fixture(path)
            for objects in ({"foreign": b"keep"}, {next(iter(source.objects)): b"corrupt"}):
                target = MemoryS3(objects)
                with self.subTest(objects=list(objects)):
                    with self.assertRaisesRegex(store.SnapshotError, "target_conflict"):
                        store.restore(target, "fresh", path)
                    self.assertEqual(target.objects, objects)
                    self.assertEqual(target.puts, [])

    def test_partial_failure_never_publishes_manifest_or_exposes_provider_message(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "backup"
            source = MemoryS3({object_key(b"data"): b"data"})
            source.fail_get = True
            with self.assertRaises(store.SnapshotError) as error:
                store.backup(source, "raw", path)
            self.assertNotIn("PRIVATE_CONTENT", str(error.exception))
            self.assertFalse((path / "manifest.json").exists())

    def test_changed_inventory_and_foreign_source_keys_refuse_complete_manifest(self):
        with tempfile.TemporaryDirectory() as root:
            source = MemoryS3({object_key(b"one"): b"one"})
            source.change_on_second_list = (object_key(b"two"), b"two")
            for index, client in enumerate((source, MemoryS3({"../outside": b"bad"}))):
                path = Path(root) / str(index)
                with self.assertRaises(store.SnapshotError):
                    store.backup(client, "raw", path)
                self.assertFalse((path / "manifest.json").exists())

    def test_existing_backup_symlink_and_manifest_path_injection_are_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "backup"
            self.fixture(path)
            with self.assertRaisesRegex(store.SnapshotError, "backup_exists"):
                store.backup(MemoryS3(), "raw", path)
            manifest_path = path / "manifest.json"
            original = manifest_path.read_bytes()
            manifest = json.loads(original)
            manifest["objects"][0]["file"] = "../../outside"
            manifest_path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(store.SnapshotError, "backup_integrity"):
                store.restore(MemoryS3(), "fresh", path)
            manifest_path.write_bytes(original)
            obj = next((path / "objects").iterdir())
            contents = obj.read_bytes()
            outside = Path(root) / "outside"
            outside.write_bytes(contents)
            obj.unlink()
            obj.symlink_to(outside)
            with self.assertRaisesRegex(store.SnapshotError, "backup_integrity"):
                store.restore(MemoryS3(), "fresh", path)

    def test_duplicate_or_missing_manifest_records_and_unsupported_version_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "backup"
            self.fixture(path)
            manifest_path = path / "manifest.json"
            original = json.loads(manifest_path.read_text())
            for mutate in (
                lambda m: m.update(version=2),
                lambda m: m["objects"].append(dict(m["objects"][0])),
                lambda m: m.update(objects=[]),
                lambda m: m["objects"][0].update(size=True),
            ):
                manifest = json.loads(json.dumps(original))
                mutate(manifest)
                manifest_path.write_text(json.dumps(manifest))
                with self.assertRaisesRegex(store.SnapshotError, "backup_integrity"):
                    store.restore(MemoryS3(), "fresh", path)


if __name__ == "__main__":
    unittest.main()
