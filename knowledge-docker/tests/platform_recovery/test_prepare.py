"""Preparation is local-only; no Docker, network, live config or service mutation."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("recovery_prepare", Path(__file__).with_name("prepare.py"))
module = importlib.util.module_from_spec(spec)


class Preparation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec.loader.exec_module(module)

    def fixture(self, root):
        source = root / "acceptance-5add1c1af0" / "knowledge-docker"
        source.mkdir(parents=True, mode=0o700)
        values = {"COMPOSE_PROJECT_NAME":"sunny-acceptance-5add1c1af0",
                  "PUBLIC_WEB_URL":"http://localhost:28181", "GATEWAY_PORT":"28181",
                  "POSTGRES_PORT":"25440", "POSTGRES_PASSWORD":"fixture-pg-secret",
                  "S3_ACCESS_KEY":"fixture-access", "S3_SECRET_KEY":"fixture-secret",
                  "CREDENTIAL_ENCRYPTION_KEY":"fixture-encryption",
                  "AGENT_ENCRYPTION_KEY":"fixture-agent", "CHANNEL_ENCRYPTION_KEY":"fixture-channel"}
        values.update({"DB_PASSWORD_"+s.upper():"fixture-"+s for s in module.DATABASES})
        module.write_private(source/".env", "".join(k+"="+v+"\n" for k,v in values.items()))
        for relative in module.PRIVATE_FILES:
            p=source/relative;p.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            module.write_private(p, "fixture-preserved-"+relative)
        module.write_private(source/".local/test-env.json", json.dumps({"public_url":values["PUBLIC_WEB_URL"],
            "admin_username":"fixture", "admin_password":"fixture-secret", "databases":{}}), replace=True)
        module.write_private(source/".local/git-graph-regression.json",json.dumps({
            "state":"ready","model_kind":"deterministic_protocol_simulation",
            "models":{"embedding":{"configuration_id":"e"},"rerank":{"configuration_id":"r"}},
            "es_index":"source-fixture-index","graph_namespace":"source-fixture-graph"}),replace=True)
        return source, values

    def test_complete_inventory_keeps_original_owners_and_separate_fresh_projection_targets(self):
        source=module.database_mapping("knowledge")
        target=module.database_mapping("recovery")
        self.assertEqual(len(source),13)
        self.assertEqual({r["service"] for r in source},set(module.DATABASES))
        visibility=next(r for r in target if r["service"]=="temporal_visibility")
        self.assertEqual((visibility["owner"],visibility["connect_roles"]),("temporal",["temporal"]))
        self.assertFalse({r["database"] for r in source}&{r["database"] for r in target})

    def test_prepare_preserves_secret_bytes_same_origin_and_never_overwrites_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);source,values=self.fixture(root)
            before={str(p.relative_to(source)):p.read_bytes() for p in source.rglob("*") if p.is_file()}
            with patch.object(module,"EXPECTED_SOURCE",source), patch.object(module,"port_available",return_value=True):
                result=module.prepare(source,root/"recovery", "b"*32, 25441, copy_public=False)
            target=Path(result["target_root"])
            env=module.read_env(target/".env")
            self.assertEqual(env["POSTGRES_PASSWORD"],values["POSTGRES_PASSWORD"])
            self.assertEqual(env["PUBLIC_WEB_URL"],values["PUBLIC_WEB_URL"])
            self.assertEqual(env["GATEWAY_PORT"],"28181")
            self.assertEqual(env["POSTGRES_PORT"],"25441")
            for rel in module.PRIVATE_FILES:
                if rel not in {".local/test-env.json",".local/git-graph-regression.json"}:
                    self.assertEqual((target/rel).read_bytes(),before[rel])
            self.assertEqual(before,{str(p.relative_to(source)):p.read_bytes() for p in source.rglob("*") if p.is_file()})
            overlay=json.loads((target/"compose.recovery.json").read_text())
            self.assertIn("projection_retrieval",overlay["services"]["retrieval"]["environment"]["DATABASE_URL"])
            self.assertIn("recovery_knowledge",overlay["services"]["knowledge"]["environment"]["DATABASE_URL"])
            self.assertNotIn("--import-realm",overlay["services"]["keycloak"]["command"])
            self.assertEqual((target/".env").stat().st_mode&0o777,0o600)
            self.assertEqual(len(json.loads((target/".local/recovery/target-pg.json").read_text())["databases"]),13)

    def test_wrong_source_main_origin_busy_target_and_existing_target_fail_before_copy(self):
        for mode in ("wrong_source","main_origin","busy_port","existing"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary);source,values=self.fixture(root);dest=root/"recovery"
                expected=source if mode!="wrong_source" else root/"different"
                if mode=="main_origin":
                    values["PUBLIC_WEB_URL"]="http://localhost:18180"
                    module.write_private(source/".env","".join(k+"="+v+"\n" for k,v in values.items()),replace=True)
                if mode=="existing": dest.mkdir()
                with patch.object(module,"EXPECTED_SOURCE",expected),patch.object(module,"port_available",return_value=mode!="busy_port"):
                    with self.assertRaises(module.RecoveryError):
                        module.prepare(source,dest,"b"*32,25441,copy_public=False)
                if mode!="existing":self.assertFalse(dest.exists())

    def test_symlink_private_material_is_rejected_before_any_target_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);source,_=self.fixture(root);dest=root/"recovery"
            original=source/module.PRIVATE_FILES[0]; original.unlink()
            elsewhere=root/"elsewhere";elsewhere.write_text("do not copy")
            original.symlink_to(elsewhere)
            with patch.object(module,"EXPECTED_SOURCE",source),patch.object(module,"port_available",return_value=True):
                with self.assertRaises(module.RecoveryError):module.prepare(source,dest,"c"*32,25441,copy_public=False)
            self.assertFalse(dest.exists())


if __name__=="__main__": unittest.main()