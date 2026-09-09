"""Actual Uvicorn TCP must never log response-validation inputs or request credentials."""

import asyncio
import importlib
import logging
import socket
from types import SimpleNamespace

import httpx
import pytest
import uvicorn
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import BaseModel


@pytest.mark.asyncio
@pytest.mark.parametrize("domain", ["knowledge", "agent", "ingest", "retrieval", "llm", "graphiti"])
async def test_native_factory_sanitizes_response_failure_over_real_tcp(domain, caplog):
    module = importlib.import_module(f"knowledge_platform.{domain}.app")
    app = module.create_app()
    app.state.service_security = SimpleNamespace(verify=lambda _: "gateway")
    exporter = InMemorySpanExporter()
    app.state.telemetry.provider.add_span_processor(SimpleSpanProcessor(exporter))
    marker, token = "SYNTHETIC_PRIVATE_RESPONSE_MARKER", "synthetic-bearer-private"

    class RequiredResult(BaseModel):
        required: str

    @app.get("/api/v1/contract-probe", response_model=RequiredResult)
    async def probe():
        return {"markdown": marker, "token": token}

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    address = listener.getsockname()
    server = uvicorn.Server(uvicorn.Config(app, lifespan="off", log_config=None, access_log=True))
    caplog.set_level(logging.INFO, logger="uvicorn.error")
    caplog.set_level(logging.INFO, logger="uvicorn.access")
    serving = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(5):
            while not server.started:
                if serving.done():
                    await serving
                await asyncio.sleep(0.01)
        async with httpx.AsyncClient(trust_env=False) as client:
            response = await client.get(f"http://{address[0]}:{address[1]}/api/v1/contract-probe", headers={"X-Service-Token": "gateway", "Authorization": "Bearer " + token})
        await asyncio.sleep(0.02)
        metrics = app.state.telemetry.metrics().decode()
        spans = exporter.get_finished_spans()
        evidence = caplog.text + response.text + metrics + repr([(s.attributes, s.events, s.status.description) for s in spans])
        assert marker not in evidence and token not in evidence
        assert "ResponseValidationError" not in caplog.text and "Traceback" not in caplog.text
        assert response.status_code == 500
        assert response.json() == {"error": {"code": "response_contract_violation", "message": "Service response failed validation"}}
        assert any(s.attributes.get("http.response.status_code") == 500 for s in spans)
        assert 'route="/api/v1/contract-probe",status="500"' in metrics
    finally:
        server.should_exit = True
        try:
            async with asyncio.timeout(5):
                await serving
        finally:
            if not serving.done():
                serving.cancel()
                await asyncio.gather(serving, return_exceptions=True)
            listener.close()
            app.state.telemetry.close()