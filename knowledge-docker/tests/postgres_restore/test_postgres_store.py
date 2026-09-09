"""Real PG17 backup/restore, an owned project with no published ports."""
import importlib.util
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import unittest
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/postgres_store.py"


def load_store():
    spec = importlib.util.spec_from_file_location("postgres_store", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PostgresRestoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = load_store()
        cls.project = "sk-pg-restore-" + uuid4().hex[:12]
        cls.env = {**os.environ, "PG_RESTORE_TEST_PASSWORD": secrets.token_hex(24)}
        cls.compose = ["docker", "compose", "-p", cls.project, "-f", str(ROOT / "compose.postgres-restore-regression.yaml")]
        cls.addClassCleanup(cls.cleanup)
        cls.compose_run("up", "-d", "--wait", "postgres")
        cls.container = cls.compose_run("ps", "-q", "postgres").strip()
        cls.pg = cls.store.PostgresTools({
            "host": "127.0.0.1", "port": 5432, "user": "postgres",
            "password": cls.env["PG_RESTORE_TEST_PASSWORD"], "sslmode": "disable",
            "maintenance_database": "postgres",
        }, docker_container=cls.container)
        cls.sources = []
        for service, owner in (("knowledge", "knowledge"), ("iam", "iam"), ("temporal_visibility", "temporal")):
            cls.pg.sql("postgres", f'CREATE ROLE "{owner}" LOGIN')
            cls.pg.sql("postgres", f'CREATE DATABASE "source_{service}" OWNER "{owner}"')
            cls.sources.append({"service": service, "database": "source_" + service, "owner": owner, "connect_roles": [owner]})
        cls.compose_run("--profile", "test", "run", "--rm", "seed")

    @classmethod
    def compose_run(cls, *args):
        result = subprocess.run(cls.compose + list(args), env=cls.env, capture_output=True, text=True, timeout=120)
        if result.returncode:
            raise AssertionError("isolated_compose_failed: " + " ".join(args))
        return result.stdout

    @classmethod
    def cleanup(cls):
        cls.compose_run("down", "--volumes", "--timeout", "5")
        remains = subprocess.run(["docker", "ps", "-aq", "--filter", "label=com.docker.compose.project=" + cls.project], capture_output=True, text=True, check=True)
        assert not remains.stdout.strip(), "owned containers remain"
        print("CLEANUP " + cls.project + " containers removed; owned Compose volumes removed", flush=True)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "backup"
        self.prefix = "restore_" + uuid4().hex[:10]

    def targets(self):
        return [{**source, "database": self.prefix + "_" + source["service"]} for source in self.sources]

    def backup(self):
        return self.store.backup(self.pg, self.sources, self.path)

    def absent(self, targets):
        for target in targets:
            self.assertEqual(self.pg.sql("postgres", "SELECT count(*) FROM pg_database WHERE datname='" + target["database"] + "'"), "0")

    def dump_rows(self, database):
        result = {}
        tables = self.pg.sql(database, "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename").splitlines()
        for table in tables:
            result[table] = json.loads(self.pg.sql(database, f'SELECT coalesce(jsonb_agg(row ORDER BY row::text),\'[]\'::jsonb) FROM (SELECT to_jsonb(t) row FROM "{table}" t) rows'))
        return result

    def test_roundtrip_real_canonical_immutable_lineage_audit_outbox_and_owner_acl(self):
        manifest = self.backup()
        self.assertEqual(manifest["consistency"], "per_database_snapshot")
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o700)
        self.assertTrue(all(path.stat().st_mode & 0o777 == 0o600 for path in self.path.iterdir()))
        targets = self.targets()
        receipt = Path(self.temp.name) / "receipt.json"
        result = self.store.restore(self.pg, self.path, targets, receipt)
        self.assertEqual(result["restored"], 3)
        self.assertTrue(receipt.exists())
        for source, target in zip(self.sources, targets, strict=True):
            self.assertEqual(self.dump_rows(source["database"]), self.dump_rows(target["database"]))
            checks = self.pg.sql("postgres", f"SELECT pg_get_userbyid(datdba), has_database_privilege('{target['owner']}', oid, 'CONNECT'), EXISTS (SELECT FROM aclexplode(datacl) WHERE grantee=0 AND privilege_type='CONNECT'), datconnlimit FROM pg_database WHERE datname='{target['database']}'")
            self.assertEqual(checks, target["owner"] + "|t|f|-1")
            self.assertEqual(self.pg.sql(target["database"], f"SELECT count(*) FROM pg_tables WHERE schemaname='public' AND tableowner <> '{target['owner']}'"), "0")
        for table in ("knowledge_revisions", "knowledge_source_snapshots", "knowledge_audit", "knowledge_outbox"):
            with self.assertRaises(self.store.PostgresStoreError):
                self.pg.sql(targets[0]["database"], f'DELETE FROM "{table}"')
        self.assertEqual(self.dump_rows(self.sources[0]["database"]), self.dump_rows(targets[0]["database"]))

    def test_tamper_missing_manifest_and_archive_rejected_before_any_create(self):
        manifest = self.backup()
        targets = self.targets()
        archive = self.path / manifest["databases"][-1]["file"]
        original = archive.read_bytes()
        for content in (original[:-8], b"tampered" + original[8:]):
            archive.write_bytes(content)
            with self.assertRaises(self.store.PostgresStoreError):
                self.store.restore(self.pg, self.path, targets, Path(self.temp.name) / "receipt")
            self.absent(targets)
        archive.write_bytes(original)
        archive.unlink()
        with self.assertRaises(self.store.PostgresStoreError):
            self.store.restore(self.pg, self.path, targets, Path(self.temp.name) / "receipt")
        self.absent(targets)
        (self.path / "manifest.json").unlink()
        with self.assertRaises(self.store.PostgresStoreError):
            self.store.restore(self.pg, self.path, targets, Path(self.temp.name) / "receipt")
        self.absent(targets)

    def test_existing_empty_target_and_missing_role_rejected_before_first_create(self):
        self.backup()
        targets = self.targets()
        self.pg.sql("postgres", f'CREATE DATABASE "{targets[-1]["database"]}"')
        with self.assertRaisesRegex(self.store.PostgresStoreError, "target_exists"):
            self.store.restore(self.pg, self.path, targets, Path(self.temp.name) / "receipt")
        self.absent(targets[:-1])
        targets = [{**t, "database": t["database"] + "_other"} for t in targets]
        targets[-1]["owner"] = "absent_owner"
        targets[-1]["connect_roles"] = ["absent_owner"]
        with self.assertRaisesRegex(self.store.PostgresStoreError, "target_role"):
            self.store.restore(self.pg, self.path, targets, Path(self.temp.name) / "receipt")
        self.absent(targets)

    def test_sql_restore_failure_rolls_back_entire_database_no_receipt_no_error_leak(self):
        name = "source_failure_" + uuid4().hex[:8]
        self.pg.sql("postgres", f'CREATE DATABASE "{name}" OWNER "knowledge"')
        self.pg.sql(name, """CREATE TABLE important (id int PRIMARY KEY); INSERT INTO important VALUES (7);
        CREATE FUNCTION fails_on_restore(int) RETURNS int LANGUAGE plpgsql IMMUTABLE AS $$ BEGIN
        IF current_database() LIKE 'restore_%' THEN RAISE EXCEPTION 'PRIVATE_BODY_PASSWORD_SENTINEL'; END IF;
        RETURN $1; END $$; CREATE INDEX postdata_failure ON important(fails_on_restore(id));""")
        sources = [self.sources[1], {"service": "knowledge", "database": name, "owner": "knowledge", "connect_roles": ["knowledge"]}]
        self.store.backup(self.pg, sources, self.path)
        targets = [{**s, "database": self.prefix + "_" + s["service"]} for s in sources]
        receipt = Path(self.temp.name) / "receipt.json"
        with self.assertRaises(self.store.PostgresStoreError) as error:
            self.store.restore(self.pg, self.path, targets, receipt)
        self.assertNotIn("PRIVATE_BODY", str(error.exception))
        self.assertNotIn(self.env["PG_RESTORE_TEST_PASSWORD"], str(error.exception))
        self.assertFalse(receipt.exists())
        self.assertEqual(self.pg.sql(targets[-1]["database"], "SELECT count(*) FROM pg_tables WHERE schemaname='public'"), "0")
        self.assertEqual(self.pg.sql(targets[-1]["database"], "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public'"), "0")
        self.assertEqual(self.dump_rows(sources[0]["database"]), self.dump_rows(targets[0]["database"]))
        for target in targets:
            self.assertEqual(self.pg.sql("postgres", f"SELECT datconnlimit FROM pg_database WHERE datname='{target['database']}'"), "0")