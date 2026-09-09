"""Bounded operational telemetry: never collect bodies, URLs, credentials or exception text."""

from __future__ import annotations

import time

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import SpanKind, Status, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest, start_http_server

PROPAGATOR = TraceContextTextMapPropagator()
METHODS = {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"}


def trace_headers() -> dict[str, str]:
    headers: dict[str, str] = {}
    # Do not propagate arbitrary baggage or user-provided identity headers.
    PROPAGATOR.inject(headers)
    return headers


class Telemetry:
    def __init__(self, service: str, *, provider=None, otlp_endpoint: str = ""):
        self.provider = provider or TracerProvider(
            resource=Resource.create({"service.name": service, "service.version": "2.0.0"})
        )
        if provider is None and otlp_endpoint:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            self.provider.add_span_processor(
                BatchSpanProcessor(
                    OTLPSpanExporter(endpoint=otlp_endpoint.rstrip("/") + "/v1/traces", timeout=3),
                    max_queue_size=2048,
                    max_export_batch_size=256,
                )
            )
        self.tracer = self.provider.get_tracer("knowledge-platform.http")
        self.service = service
        self.registry = CollectorRegistry()
        labels = ["service", "method", "route", "status"]
        self.requests = Counter(
            "knowledge_http_requests_total", "Completed HTTP requests", labels, registry=self.registry
        )
        self.duration = Histogram(
            "knowledge_http_request_duration_seconds",
            "HTTP duration including streamed response completion",
            labels,
            buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 180),
            registry=self.registry,
        )
        self.server = None
        self.server_thread = None

    def instrument(self, app):
        app.add_middleware(TelemetryMiddleware, telemetry=self)

    def start(self, metrics_port: int = 0):
        # A runtime process installs one provider. Tests pass their own provider without
        # touching the process-global OpenTelemetry singleton or opening a listener.
        trace.set_tracer_provider(self.provider)
        if metrics_port:
            self.server, self.server_thread = start_http_server(
                metrics_port, addr="0.0.0.0", registry=self.registry
            )

    def close(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server_thread.join(timeout=3)
        self.provider.shutdown()

    def metrics(self) -> bytes:
        return generate_latest(self.registry)


class TelemetryMiddleware:
    def __init__(self, app, telemetry: Telemetry):
        self.app = app
        self.telemetry = telemetry

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path") in {"/healthz", "/readyz"}:
            return await self.app(scope, receive, send)
        method = scope.get("method", "")
        method = method if method in METHODS else "OTHER"
        carrier = {
            key.decode("ascii"): value.decode("ascii", errors="ignore")
            for key, value in scope.get("headers", [])
            if key in {b"traceparent", b"tracestate"}
        }
        started = time.monotonic()
        status = 500

        async def observed_send(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        with self.telemetry.tracer.start_as_current_span(
            method,
            kind=SpanKind.SERVER,
            context=PROPAGATOR.extract(carrier),
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            try:
                await self.app(scope, receive, observed_send)
            except BaseException:
                # Includes client cancellation; don't record potentially private exception payloads.
                status = 500 if status < 400 else status
                span.set_status(Status(StatusCode.ERROR))
                raise
            finally:
                route = getattr(scope.get("route"), "path", "unmatched")
                span.update_name(method + " " + route)
                span.set_attributes(
                    {"http.request.method": method, "http.route": route, "http.response.status_code": status}
                )
                if status >= 500:
                    span.set_status(Status(StatusCode.ERROR))
                labels = (self.telemetry.service, method, route, str(status))
                self.telemetry.requests.labels(*labels).inc()
                self.telemetry.duration.labels(*labels).observe(time.monotonic() - started)
