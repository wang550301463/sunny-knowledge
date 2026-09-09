import httpx
from fastapi import FastAPI
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
import pytest


@pytest.mark.asyncio
async def test_metrics_and_traces_use_route_templates_and_omit_private_inputs():
    from knowledge_platform.common.observability import Telemetry

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    telemetry = Telemetry("knowledge", provider=provider)
    app = FastAPI()

    @app.get("/api/v1/pages/{page_id}")
    async def page(page_id: str):
        return {"markdown": "private-response"}

    telemetry.instrument(app)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/v1/pages/private-page?secret=private-query",
            headers={"Authorization": "Bearer private-token", "X-Request-ID": "private-id"},
        )
    assert response.status_code == 200
    output = telemetry.metrics().decode()
    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].name == "GET /api/v1/pages/{page_id}"
    assert spans[0].attributes["http.response.status_code"] == 200
    assert 'route="/api/v1/pages/{page_id}"' in output
    serialized = output + str(spans[0].attributes)
    for secret in ["private-page", "private-query", "private-token", "private-id", "private-response"]:
        assert secret not in serialized
    provider.shutdown()


@pytest.mark.asyncio
async def test_unknown_routes_are_bounded_and_traceparent_continues():
    from knowledge_platform.common.observability import Telemetry

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    telemetry = Telemetry("knowledge", provider=provider)
    app = FastAPI()
    telemetry.instrument(app)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        await client.get("/does-not-exist-a", headers={"traceparent": "00-12345678901234567890123456789012-1234567890123456-01"})
        await client.get("/does-not-exist-b")
    output = telemetry.metrics().decode()
    assert 'route="unmatched"' in output
    assert "does-not-exist" not in output
    spans = exporter.get_finished_spans()
    assert spans[0].context.trace_id == int("12345678901234567890123456789012", 16)
    provider.shutdown()


@pytest.mark.asyncio
async def test_failure_span_does_not_record_exception_payload():
    from knowledge_platform.common.observability import Telemetry

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    telemetry = Telemetry("knowledge", provider=provider)
    app = FastAPI()

    @app.get("/failure")
    async def failure():
        raise ValueError("private-key-and-prompt")

    telemetry.instrument(app)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test") as client:
        response = await client.get("/failure")
    assert response.status_code == 500
    span = exporter.get_finished_spans()[0]
    assert span.status.status_code.name == "ERROR"
    assert not span.events
    assert "private-key" not in str(span.attributes)
    provider.shutdown()
