"""CLI entry function keeps partial distinct and closes resources before exit."""
import asyncio
import importlib
import json
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("domain", ["retrieval", "graphiti"])
@pytest.mark.parametrize("complete", [False, True])
async def test_cli_report_exit_status_and_shutdown_do_not_leak_private_journal_fields(
    monkeypatch, capsys, domain, complete
):
    module = importlib.import_module(f"knowledge_platform.{domain}.worker")
    closed = []
    report = {"run_id": "opaque-run", "cursor": "private-page-key", "private_ref": "do-not-log",
              "status": "complete" if complete else "partial", "last_error": None,
              "projected": 2, "not_visible": 0 if complete else 1,
              "processed": 2 if complete else 3, "scan_complete": True,
              "full_rebuild_complete": complete}

    class Resource:
        def __init__(self, name):
            self.name, self.engine = name, object()
        async def close(self):
            closed.append(self.name)
        async def initialize(self):
            pass

    class Worker:
        def __init__(self, *args, **kwargs):
            pass
        async def rebuild(self):
            await asyncio.sleep(0)
            return report

    class Sampler:
        def __init__(self, *args):
            pass
        async def run(self):
            try:
                await asyncio.Event().wait()
            finally:
                closed.append("sampler")

    async def initialize(engine):
        pass

    monkeypatch.setattr(module, "create_database", lambda _: Resource("database"))
    monkeypatch.setattr(module, "HTTPAuthorizer", lambda _: Resource("auth"))
    monkeypatch.setattr(module, "MachineTokens", lambda _: Resource("tokens"))
    monkeypatch.setattr(module, "ElasticIndex" if domain == "retrieval" else "Neo4jGraph",
                        lambda _: Resource("backend"))
    monkeypatch.setattr(module, "Catalog", lambda _: object())
    monkeypatch.setattr(module, "Clients", lambda *args: object())
    monkeypatch.setattr(module, "initialize", initialize)
    monkeypatch.setattr(module, "Telemetry", lambda *args, **kwargs: SimpleNamespace(
        domain=object(), close=lambda: closed.append("telemetry")))
    monkeypatch.setattr(module, "ProjectionWorker", Worker)
    monkeypatch.setattr(module, "SnapshotSampler", Sampler)
    assert await module.run(rebuild=True) == (0 if complete else 2)
    output = capsys.readouterr().out
    assert "private" not in output and "do-not-log" not in output
    value = json.loads(output)
    assert value["full_rebuild_complete"] is complete and value["status"] == report["status"]
    assert closed == ["sampler", "telemetry", "tokens", "backend", "auth", "database"]