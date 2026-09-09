package platform

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/prometheus/client_golang/prometheus/promhttp"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	"go.opentelemetry.io/otel/sdk/trace/tracetest"
	"go.opentelemetry.io/otel/trace"
)

func testTelemetry(t *testing.T, service string) (*Telemetry, *tracetest.InMemoryExporter) {
	t.Helper()
	ex := tracetest.NewInMemoryExporter()
	provider := sdktrace.NewTracerProvider(sdktrace.WithSyncer(ex))
	t.Cleanup(func() { provider.Shutdown(context.Background()) })
	return NewTelemetry(service, provider), ex
}
func TestHTTPTraceChainUsesSafeAttributesAndParentRelationships(t *testing.T) {
	incoming, a := testTelemetry(t, "gateway")
	outgoing, b := testTelemetry(t, "auth")
	upstream := httptest.NewServer(outgoing.Middleware(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Baggage") != "" {
			t.Error("baggage propagated")
		}
		JSON(w, 200, map[string]bool{"ok": true})
	})))
	defer upstream.Close()
	client := NewClient("gateway", testSecurity(t))
	handler := incoming.Middleware(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var result any
		if e := client.Call(r.Context(), "auth", upstream.URL, "POST", "/internal/v1/resolve?token=QUERY-SECRET", "Bearer AUTH-SECRET", map[string]string{"body": "BODY-SECRET"}, &result); e != nil {
			t.Error(e)
		}
		w.WriteHeader(204)
	}))
	req := httptest.NewRequest("GET", "/api/v1/pages/PRIVATE-ID?secret=QUERY-SECRET", nil)
	req.Header.Set("Traceparent", "00-12345678901234567890123456789012-1234567890123456-01")
	req.Header.Set("Tracestate", "vendor=STATE-SECRET")
	req.Header.Set("Baggage", "secret=BAG-SECRET")
	handler.ServeHTTP(httptest.NewRecorder(), req)
	spans := append(a.GetSpans(), b.GetSpans()...)
	if len(spans) != 3 {
		t.Fatalf("got %d spans want server/client/server", len(spans))
	}
	byKind := map[trace.SpanKind][]tracetest.SpanStub{}
	for _, s := range spans {
		byKind[s.SpanKind] = append(byKind[s.SpanKind], s)
		if s.SpanContext.TraceID().String() != "12345678901234567890123456789012" {
			t.Fatal("trace broken")
		}
	}
	clients := byKind[trace.SpanKindClient]
	if len(clients) != 1 {
		t.Fatal("client span missing")
	}
	if b.GetSpans()[0].Parent.SpanID() != clients[0].SpanContext.SpanID() {
		t.Fatal("server is not client child")
	}
	raw, _ := json.Marshal(spans)
	for _, secret := range []string{"PRIVATE-ID", "QUERY-SECRET", "BODY-SECRET", "AUTH-SECRET", "BAG-SECRET", "STATE-SECRET"} {
		if strings.Contains(string(raw), secret) {
			t.Errorf("private input in trace: %s", secret)
		}
	}
	rec := httptest.NewRecorder()
	promhttp.HandlerFor(incoming.Registry, promhttp.HandlerOpts{}).ServeHTTP(rec, httptest.NewRequest("GET", "/metrics", nil))
	if !strings.Contains(rec.Body.String(), `route="/api/v1/pages/{path...}"`) {
		t.Fatal("safe route absent")
	}
	if strings.Contains(rec.Body.String(), "PRIVATE-ID") {
		t.Fatal("ID in metric")
	}
}
func TestTelemetryPreservesStreamingAndCountsCancelledResponseOnce(t *testing.T) {
	telemetry, exporter := testTelemetry(t, "gateway")
	exited := make(chan struct{})
	server := httptest.NewServer(telemetry.Middleware(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		defer close(exited)
		w.Header().Set("Content-Type", "text/event-stream")
		io.WriteString(w, "data: first\n\n")
		w.(http.Flusher).Flush()
		<-r.Context().Done()
	})))
	defer server.Close()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	req, _ := http.NewRequestWithContext(ctx, "GET", server.URL+"/api/v1/runs/PRIVATE-ID/events", nil)
	response, e := server.Client().Do(req)
	if e != nil {
		t.Fatal(e)
	}
	buf := make([]byte, 13)
	if _, e = io.ReadFull(response.Body, buf); e != nil {
		t.Fatal(e)
	}
	if len(exporter.GetSpans()) != 0 {
		t.Fatal("span ended before stream completion")
	}
	cancel()
	response.Body.Close()
	select {
	case <-exited:
	case <-time.After(time.Second):
		t.Fatal("cancellation lost")
	}
	deadline := time.Now().Add(time.Second)
	for len(exporter.GetSpans()) == 0 && time.Now().Before(deadline) {
		time.Sleep(time.Millisecond)
	}
	if len(exporter.GetSpans()) != 1 {
		t.Fatal("expected one completed server span")
	}
	families, e := telemetry.Registry.Gather()
	if e != nil {
		t.Fatal(e)
	}
	for _, f := range families {
		if f.GetName() == "knowledge_http_requests_total" && f.Metric[0].GetCounter().GetValue() != 1 {
			t.Fatal("request counted twice")
		}
	}
}
func TestTelemetryBoundsRoutesAndDiscardsInvalidParent(t *testing.T) {
	telemetry, exporter := testTelemetry(t, "iam")
	handler := telemetry.Middleware(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(404) }))
	for i := 0; i < 100; i++ {
		req := httptest.NewRequest("GET", "/unrecognized/"+ID(), nil)
		req.Header.Set("Traceparent", "BAD-PRIVATE-HEADER")
		handler.ServeHTTP(httptest.NewRecorder(), req)
	}
	families, e := telemetry.Registry.Gather()
	if e != nil {
		t.Fatal(e)
	}
	for _, f := range families {
		if len(f.Metric) != 1 {
			t.Fatalf("unbounded label count %d", len(f.Metric))
		}
	}
	for _, span := range exporter.GetSpans() {
		if span.Name != "GET unmatched" || span.Parent.IsValid() {
			t.Fatal("bad parent/name")
		}
	}
}
